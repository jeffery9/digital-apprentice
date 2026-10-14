#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""D-10 反思循环对照：无反思 / Self-Refine+外部验证器 / 纯自评，三臂成功率-token-轮数（第 9 章）

三条实验臂（书 9.3/9.4/9.7）：
  arm_none   —— 直出，不检查
  arm_ext    —— 生成 → 外部规则验证器（可行动差异报告）→ 修订，max_rounds=2
  arm_self   —— 生成 → "自评"批评（与生成同一套启发式＝证人兼任法官）→ 修订

⚠️ 不调用任何模型。错误注入与修复均为脚本化替身，只演示对照实验的机制形状
（QWEN.md 铁律三）。真实收益曲线斜率须用你自己的评测集重测——本 demo 交付结构，不交付结论数值。
"""
from __future__ import annotations

import argparse
import random

# ---- 任务集：每个任务带一组"缺陷"（模拟一次生成的质量问题）----
# 缺陷类型：format(格式/遗漏类，自检可见) | factual(幻觉类，自检不可见) | verbosity(冗长包装)
TASKS = []
_rng = random.Random(42)
for i in range(60):
    kinds = []
    if _rng.random() < 0.55:
        kinds.append("format")     # 半数以上任务带可见缺陷
    if _rng.random() < 0.30:
        kinds.append("factual")    # 幻觉：只有外部证据能抓
    if _rng.random() < 0.25:
        kinds.append("verbosity")  # 冗长：自评判官的偏爱对象
    TASKS.append({"id": i, "defects": set(kinds)})


def generate(task: dict) -> dict:
    """optimizer 的一次产出：携带原缺陷，成本 100 token。"""
    return {"defects": set(task["defects"]), "tokens": 100, "padding": 0}


def verifier_external(cand: dict) -> list[str]:
    """外部规则验证器：schema/检索核对的替身。format 与 factual 都能抓，且给出可行动差异报告。
    verbosity 它也认得（超长告警），但那是低优先级提示。"""
    report = []
    if "format" in cand["defects"]:
        report.append("缺政策编号引用（期望：clause_id 字段非空）")
    if "factual" in cand["defects"]:
        report.append("金额与订单系统不一致（期望：¥128.00，实际：¥145.00）")
    if cand["padding"] > 40:
        report.append("长度超预算（低优先级）")
    return report


def critic_self(cand: dict) -> list[str]:
    """纯自评批评器：与生成同源的启发式——看不见 factual（证人即法官），
    还偏爱 verbosity（把冗长当质量），并在修订里引入震荡。"""
    report = []
    if "format" in cand["defects"]:
        report.append("我觉得引用可以更完整一点")   # 模糊批评，修复命中率打折
    if cand["padding"] > 40:
        report.clear()                              # 冗长在自评眼里不是问题（长度偏差）
    return report


def revise(cand: dict, report: list[str], *, external: bool) -> dict:
    """按差异报告最小改动修订；成本 +75 token。external=False 时修对一半、改坏一处（震荡）。"""
    c = dict(cand, tokens=cand["tokens"] + 75, defects=set(cand["defects"]))
    if not report:
        c["padding"] += 15  # 无批评则灌水（自我检讨式幻觉的token形态）
        return c
    if external:
        if any("政策编号" in r for r in report):
            c["defects"].discard("format")
        if any("金额" in r for r in report):
            c["defects"].discard("factual")
    else:
        # 自评臂：format 只有六成概率被模糊批评真正修掉；修订还会以四成概率引入新 format 缺陷
        if "format" in c["defects"] and _rng.random() < 0.6:
            c["defects"].discard("format")
        elif _rng.random() < 0.4:
            c["defects"].add("format")  # 震荡：改回去
        # factual 原样保留——自评根本看不见它
    return c


def run_arm(name: str, use_external: bool, max_rounds: int = 2) -> dict:
    _rng_local = random.Random(7)  # 每臂同种子，保证对照公平
    global _rng
    _rng = _rng_local
    succ, tok, rounds = 0, 0, 0
    resid_factual = 0
    for task in TASKS:
        cand = generate(task)
        for _ in range(max_rounds):
            rep = verifier_external(cand) if use_external else critic_self(cand)
            if not rep and not cand["defects"]:
                break
            if not rep:
                cand = revise(cand, ["(空)"], external=use_external)  # 保持灌水路径一致
                rounds += 1
                continue
            cand = revise(cand, rep, external=use_external)
            rounds += 1
        ok = not cand["defects"]
        succ += ok
        tok += cand["tokens"]
        resid_factual += ("factual" in cand["defects"])
    n = len(TASKS)
    return {"arm": name, "success": f"{succ/n:.1%}", "avg_tokens": round(tok/n),
            "avg_rounds": round(rounds/n, 2), "factual_leak": f"{resid_factual}/{n}"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true", help="离线运行（本 demo 恒为 mock）")
    args = ap.parse_args()
    rows = [run_arm("none（直出）", use_external=False, max_rounds=0),
            run_arm("self（纯自评）", use_external=False),
            run_arm("ext（外部验证器）", use_external=True)]
    print(f"{'臂':<16}{'端到端成功':<10}{'平均tok':<9}{'平均轮数':<9}{'幻觉漏放'}")
    print("-" * 62)
    for r in rows:
        print(f"{r['arm']:<16}{r['success']:<12}{r['avg_tokens']:<10}{r['avg_rounds']:<10}{r['factual_leak']}")
    print("""
观察（对应书中 9.2/9.3/9.4）：
  ① 幻觉（factual）只有外部验证器臂能清零——自评臂对它完全失明（证人兼任法官，书 9.2）
  ② 自评臂成功率常低于直出臂或仅微高：震荡修订+灌水把省下的错误又赔回去（书 9.3 收益衰减+帮倒忙）
  ③ 外部验证臂 token 最高但按风险分级付账才划算（书 9.7：把最贵的反思留给最贵的错误）
  ④ 数字随剧本变、形状不变——换你的评测集重测，别引用本页绝对值（铁律三）""")


if __name__ == "__main__":
    main()
