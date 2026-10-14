#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
D-06　外部中断与动态重规划（第 5 章 5.4）
========================================

演示什么：**触发源三（外部中断）与"结论外存"如何救场。**
任务执行到一半注入一条用户追加需求；随后模拟一次上下文压缩（compaction）。
对照组 --no-replan 把中断排队不管，看它如何在尾部漏答新需求。
关键细节：已完成步骤的**结论**落在工作区文件 findings.json 里——压缩丢掉的
只是原始观察，重规划因此不必重查已完成项（6.7 "文件即外存"的先声）。

⚠️ mock 环境只演示机制与相对形状，不代表任何真实模型的中断处理能力。

用法：
    python replan.py --mock                    # 中断→重规划，计划 v1→v2 演进留痕
    python replan.py --mock --no-replan        # 对照组：中断被忽略，尾部漏答
    python replan.py --mock --compact-at 3     # 调整压缩时机，看结论外存的救场效果
    python replan.py --mock --max-replans 0    # 反模式二防线：重规划次数计入停止条件

观察什么（对应书中 5.10.2）：
    ① 重规划请求携带的三件东西：版本号／已完成结论（来自文件）／中断证据
    ② 外化判据命中时机：剩余步数×单步平均 token > 剩余预算一半 → 落盘计划
    ③ telemetry 里的 replan_count 字段——接进停止条件就是反模式二的解药
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "D-01-最小agent循环"))
from llm import get_llm, system, tool_result, user  # noqa: E402
from agent_loop import DEMO_TASK, build_tools, summarize  # noqa: E402

INTERRUPTION = ("【外部中断｜第 {at} 步后】用户追加需求："
                "顺便查一下该客户的续约意向，合并进本次答复。")
WORKDIR_NOTE = "（findings.json：已完成步骤的结论外存处，压缩不丢——见 6.7）"


@dataclass
class PlanStep:
    tool: str
    accept: str
    status: str = "todo"          # todo / done / dropped
    finding: str = ""


@dataclass
class Plan:
    version: int
    steps: list[PlanStep]
    reason: str = "初始计划"

    def render(self) -> str:
        mark = {"todo": "□", "done": "☑", "dropped": "✗"}
        return f"计划 v{self.version}（{self.reason}）\n  " + "\n  ".join(
            f"{mark[s.status]} {s.tool}｜验收：{s.accept}" for s in self.steps)


@dataclass
class Telemetry:
    """每次运行产一行 JSON——replan_count 是反模式二的探测器数据源（视角盒①指纹③）。"""
    events: list[dict[str, Any]] = field(default_factory=list)
    replan_count: int = 0
    compacted_at: int | None = None

    def log(self, **kw: Any) -> None:
        self.events.append(kw)


def persist_findings(path: Path, plan: Plan) -> None:
    """结论外存：只写'步骤→一句话结论'，不写原始观察（体积差一个数量级）。"""
    done = {s.tool: s.finding for s in plan.steps if s.status == "done"}
    path.write_text(json.dumps(done, ensure_ascii=False, indent=1), encoding="utf-8")


def load_findings(path: Path) -> dict[str, str]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def main() -> int:
    ap = argparse.ArgumentParser(description="D-06 动态重规划（第 5 章）")
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--no-replan", action="store_true", help="对照组：忽略中断")
    ap.add_argument("--compact-at", type=int, default=4, help="在第几步后模拟上下文压缩")
    ap.add_argument("--max-replans", type=int, default=2, help="重规划次数上限（停止条件第四闸）")
    ap.add_argument("--workdir", default=".", help="findings.json 所在目录")
    a = ap.parse_args()

    random.seed(20261014)
    tools = build_tools()
    llm = get_llm(mock=True)
    work = Path(a.workdir)
    fpath = work / "findings.json"
    fpath.unlink(missing_ok=True)
    tel = Telemetry()

    plan = Plan(1, [PlanStep("get_order", "订单与 SLA"),
                   PlanStep("get_logistics", "延误天数与责任"),
                   PlanStep("search_kb", "赔付标准条款"),
                   PlanStep("search_history", "重复投诉与既往赔付")])
    print(plan.render(), "\n")

    ctx = [system("你是智航客服副驾，按计划逐步收集证据。"), user(DEMO_TASK)]
    remaining_budget = 6_000          # 演示用的小预算，让外化判据真的会命中
    avg_step_tokens = 0
    answered_interrupt = False

    i = 0
    while i < len(plan.steps):
        st = plan.steps[i]
        resp = llm.chat(ctx + [user(f"执行步骤 {st.tool}（验收：{st.accept}）")],
                        tools=[tools[st.tool]])
        step_tok = resp.usage.prompt_tokens
        avg_step_tokens = (avg_step_tokens * (i + 1) + step_tok) // (i + 2)
        remaining_budget -= step_tok
        obs = tools[st.tool].invoke({"query": st.tool})
        st.status, st.finding = "done", summarize(obs, 100)
        ctx.append(tool_result(f"c{i}", st.finding, name=st.tool))
        persist_findings(fpath, plan)                     # 每步结论即时落盘
        print(f"第 {i+1} 步 ☑ {st.tool}｜本轮输入 {step_tok:,} tok｜余预算 {remaining_budget:,}")
        tel.log(step=i + 1, tool=st.tool, tokens=step_tok, budget_left=remaining_budget)

        # —— 外化判据（5.4）：剩余步数×单步均值 > 剩余预算一半 → 落盘计划 ——
        left = len(plan.steps) - i - 1
        if left * avg_step_tokens > remaining_budget / 2:
            ppath = work / f"plan_v{plan.version}.json"
            ppath.write_text(json.dumps([vars(s) for s in plan.steps],
                                         ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"    ⚑ 外化判据命中（{left}×{avg_step_tokens} > {remaining_budget//2}）"
                  f"→ 计划已落盘 {ppath.name}")

        # —— 第 2 步后注入外部中断（触发源三）——
        if i == 1 and not answered_interrupt and not a.no_replan:
            msg = INTERRUPTION.format(at=i + 1)
            print("\n" + msg)
            if plan.version >= 1 and tel.replan_count >= a.max_replans:
                print("    重规划次数已达上限 → 走出口 C：降级收尾（反模式二防线）")
            else:
                # 重规划：携带版本号＋已完成结论（从文件读！）＋中断证据
                findings = load_findings(fpath)
                rp = llm.chat([system("你是规划器。根据中断证据修订计划，保留已完成步骤。"),
                               user(f"当前计划 v{plan.version}：\n{plan.render()}\n"
                                    f"已完成结论（来自 {fpath.name}）：{json.dumps(findings, ensure_ascii=False)}\n"
                                    f"中断证据：{msg}")])
                tel.replan_count += 1
                plan = Plan(plan.version + 1,
                            [s for s in plan.steps[:i + 1]] +
                            [PlanStep("search_history", "续约意向与近期互动频次", ),
                             ],
                            reason="外部中断：用户追加续约意向查询")
                # 注：demo 简化为"在旧计划尾部替换未完成段"；已完成段原样保留
                plan.steps = plan.steps[:i + 1] + [PlanStep("search_history", "续约意向与近期互动频次")]
                print(f"    ↻ 触发重规划 → {plan.render()}")
                print(f"    （重规划请求读取了 {fpath.name}：已完成结论未重查{WORKDIR_NOTE}）")
                tel.log(event="replan", from_version=plan.version - 1,
                        to_version=plan.version, replan_count=tel.replan_count)
                answered_interrupt = True
                ctx = [ctx[0], ctx[1]] + [tool_result("c9", f"（压缩后历史）已知结论："
                                                       f"{json.dumps(load_findings(fpath), ensure_ascii=False)}")]
                i += 1
                continue

        # —— 模拟上下文压缩（重建边界）：丢掉原始观察，只留结论 ——
        if i + 1 == a.compact_at:
            findings = load_findings(fpath)
            ctx = [ctx[0], user(f"[compacted] 任务开始＋已完成结论：{json.dumps(findings, ensure_ascii=False)}")]
            tel.compacted_at = i + 1
            print(f"    ▓ 第 {i+1} 步后发生上下文压缩：原始观察丢弃，"
                  f"结论自 {fpath.name} 恢复（若无外存则需重查——这就是 5.4 的重建边界）")
            tel.log(event="compact", at=i + 1, restored_from=str(fpath.name))
        i += 1

    final = llm.chat(ctx + [user("汇总给出最终处理建议。")])
    print(f"\n最终答复：{final.text}")
    if a.no_replan:
        print("▲ 对照组提示：本运行忽略了中断——检查答复是否覆盖'续约意向'？"
              "（预期：漏答。与不带 --no-replan 的运行对比即是 5.10.2 的观察点①）")
        ok = "续约" in (final.text or "")
        print(f"   中断需求覆盖检查：{'意外命中' if ok else '漏答（符合预期）'}")
    print(f"\ntelemetry：replan_count={tel.replan_count}｜compacted_at={tel.compacted_at}"
          f"｜events={len(tel.events)}")
    (work / "telemetry.jsonl").write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in tel.events), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
