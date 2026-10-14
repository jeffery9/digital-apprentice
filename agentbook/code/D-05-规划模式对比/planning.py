#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
D-05　规划模式对比：ReAct vs Plan-and-Execute（第 5 章）
======================================================

演示什么：**同一任务、同一模型池下，两种规划形态的轨迹差异与各自的账。**
在 D-01 的循环基座上加两样东西：①显式计划工件（PE 路径）；②前提失效检测器
（5.4 触发源二）。末尾汇总三个本章正文用到的指标：模型调用次数、累计 token、
无效步比例（动作与本步计划/任务目标无关的步——循环打转的量化）。

⚠️ 读数前必看：mock 环境只演示**机制与相对形状**，不代表任何真实模型的规划质量。
真实对比请用你的模型池与任务集重跑（5.13 "你自己的失败轨迹库"）。

用法：
    python planning.py --mock                      # 两策略并跑，对齐时间线
    python planning.py --mock --strategy react     # 单跑 ReAct
    python planning.py --mock --strategy pe        # 单跑 Plan-and-Execute
    python planning.py --mock --perturb            # 第 3 步注入前提失效观察
    python planning.py --mock --loop-spin          # 让 ReAct 演示循环打转＋检测器
    python planning.py --mock --dump-trace t.json  # 轨迹落盘（吻合率可离线复算）

观察什么（对应书中 5.10.1）：
    ① PE 比 ReAct 多一次模型调用（规划费）——短任务摊不平固定开销的实测
    ② --perturb 下 ReAct 下一轮就地转向；PE 触发重规划（看它携带的证据字段）
    ③ 无效步比例：--loop-spin 让 ReAct 演示循环打转，检测器如何压回
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "D-01-最小agent循环"))
from llm import get_llm, system, tool_result, user  # noqa: E402
from agent_loop import DEMO_TASK, SYSTEM_PROMPT, Tool, build_tools, summarize  # noqa: E402


# ─────────────────────────────────────────────────────────────
# 轨迹记录：step → (类型, 动作, 说明)，供时间线与指标复算
# ─────────────────────────────────────────────────────────────
@dataclass
class Step:
    n: int
    kind: Literal["react", "plan", "execute", "replan"]
    action: str                 # 工具名或 "FINAL"/"PLAN"
    detail: str = ""
    tokens: int = 0
    invalid: bool = False       # 无效步：与上一步同动作且无新信息（打转计数的原子）


@dataclass
class RunResult:
    strategy: str
    status: Literal["done", "max_steps", "budget_exceeded"]
    steps: list[Step] = field(default_factory=list)
    tokens: int = 0
    answer: str = ""
    plan_versions: list[str] = field(default_factory=list)   # 计划留痕（反模式一探测器数据源）


def make_llm():
    return get_llm(mock=True)


# ─────────────────────────────────────────────────────────────
# 路径 A：ReAct —— 复用 D-01 的循环骨架，外面套一个"打转检测器"
# ─────────────────────────────────────────────────────────────
def spin_detector(recent: list[str], window: int = 3) -> bool:
    """5.2 失效模式一的检测器：最近 window 个动作是否全同（近参数忽略）。"""
    return len(recent) >= window and len(set(recent[-window:])) == 1


def run_react(task: str, tools: dict[str, Tool], *, max_steps: int = 8,
              perturb: bool = False, loop_spin: bool = False) -> RunResult:
    llm = make_llm()
    ctx = [system(SYSTEM_PROMPT), user(task)]
    out = RunResult("react", "max_steps")
    spent = 0
    recent_actions: list[str] = []
    for step in range(1, max_steps + 1):
        resp = llm.chat(ctx, tools=list(tools.values()))
        spent += resp.usage.total_tokens
        if not resp.tool_calls:
            out.status, out.answer = "done", resp.text or ""
            out.steps.append(Step(step, "react", "FINAL", "产出最终建议",
                                  resp.usage.prompt_tokens))
            out.tokens = spent
            return out
        call = resp.tool_calls[0]
        name = call.name
        # 打转注入（演示用）：loop_spin 时前几轮强制重复同一动作，模拟 5.1 事故
        if loop_spin and step <= 4:
            name = "search_kb"
        invalid = bool(recent_actions) and name == recent_actions[-1]
        out.steps.append(Step(step, "react", name,
                              "检索/查询" + ("（打转：与上一步同动作）" if invalid else ""),
                              resp.usage.prompt_tokens, invalid))
        recent_actions.append(name)
        ctx.append(resp.as_message())
        result = tools[name].invoke(call.args) if name in tools else "ERROR: 未注册工具"
        if perturb and step == 3:
            # 5.3 触发源二：观察推翻原任务路线的前提（无错误码，最隐蔽的一类）
            result = ("NOTE: 该客户合同不含赔付条款（前提变化）——"
                      "此前'按延误天数阶梯赔付'的路线不再适用，需改走协商顺延。")
        ctx.append(tool_result(call.id, summarize(result), name=name))
        if spin_detector(recent_actions):
            # 检测器命中：注入一条纠偏提示（干预选项见 5.4：这里选最便宜的"提醒换道"）
            ctx.append(user("检测到连续重复动作。请改用其他可用工具或直接给出结论。"))
            recent_actions.clear()
    out.tokens = spent
    return out


# ─────────────────────────────────────────────────────────────
# 路径 B：Plan-and-Execute —— 显式计划工件 + 逐步执行 + 前提失效触发器
# 计划在本 demo 里由规则生成（中档粒度：步骤＋一句验收标准，5.3）；
# 真实系统里这一步是规划调用点的模型输出——形状相同，来源不同。
# ─────────────────────────────────────────────────────────────
PLAN_V1 = [
    ("get_order", "拿到订单与 SLA 等级"),
    ("get_logistics", "确认延误天数与责任归属"),
    ("search_kb", "命中赔付标准条款"),
    ("search_history", "确认是否重复投诉与既往赔付"),
]


def execute_step(item: tuple[str, str], tools: dict[str, Tool],
                 prior_findings: list[str]) -> tuple[Step, str]:
    """执行器只带"当前步骤＋前序结论摘要"，不带全历史——输入近似恒定（图 5-1 下轨）。"""
    llm = make_llm()
    ctx = [system("你是执行器。完成给定步骤，用一句话回报结论。"),
           user(f"步骤：{item[0]}｜验收标准：{item[1]}\n"
                f"已知结论：{'；'.join(prior_findings) or '（无）'}")]
    spec = tools[item[0]]
    resp = llm.chat(ctx, tools=[spec])
    finding = spec.invoke({"query": item[0]})
    return (Step(0, "execute", item[0], f"验收：{item[1]}", resp.usage.prompt_tokens),
            summarize(finding, 120))


def run_pe(task: str, tools: dict[str, Tool], *, perturb: bool = False,
           max_replans: int = 2) -> RunResult:
    out = RunResult("pe", "done")
    spent = 0
    version = 1
    # 规划调用点：低频、高价值，配旗舰档位（第 4 章表 4-3 的 plan 行）
    pr = make_llm().chat([system("你是规划器。产出带验收标准的步骤列表。"), user(task)])
    spent += pr.usage.total_tokens
    out.plan_versions.append(f"v{version}: {', '.join(n for n, _ in PLAN_V1)}")
    out.steps.append(Step(0, "plan", "PLAN",
                          f"计划 v{version}（{len(PLAN_V1)} 步，含验收标准）",
                          pr.usage.prompt_tokens))
    findings: list[str] = []
    plan = list(PLAN_V1)
    i = 0
    while i < len(plan):
        item = plan[i]
        st, obs = execute_step(item, tools, findings)
        spent += st.tokens
        st.n = i + 1
        out.steps.append(st)
        premise_break = perturb and i == 2      # 第 3 步的观察推翻后续步骤的前提
        if premise_break and max_replans > 0:
            # 触发器命中 → 重规划：携带版本号＋已完成结论＋证据（图 5-2 关键边）
            version += 1
            max_replans -= 1
            rp = make_llm().chat(
                [system("你是规划器。根据新证据修订剩余步骤。"),
                 user(f"计划 v{version - 1} 执行至第 {i + 1} 步出现前提失效：\n"
                      f"证据：赔付条款不适用（合同不含该条款）\n"
                      f"已完成结论：{'；'.join(findings) or '（无）'}")])
            spent += rp.usage.total_tokens
            new_plan = [("search_kb", "检索'不可抗力/协商顺延'处理口径"),
                        ("search_history", "确认既往同类案例的处理结论")]
            out.plan_versions.append(f"v{version}(重规划): {', '.join(n for n, _ in new_plan)}")
            out.steps.append(Step(i + 1, "replan", "REPLAN",
                                  f"前提失效→重规划，产出 v{version}（证据随请求携带）",
                                  rp.usage.prompt_tokens))
            plan = plan[:i + 1] + new_plan      # 出口 A：修补计划继续
        findings.append(f"{item[0]}→{obs}")
        i += 1
    out.tokens = spent
    if perturb:
        # 前提失效路线：合同不含赔付条款 → 结论改走协商顺延（与 findings 一致，不编造）
        out.answer = ("订单核实延误属实，但该客户合同不含赔付条款——赔付路线不适用；"
                      "建议按知识库'不可抗力/协商顺延'口径处理，并核对既往同类案例结论。")
    else:
        out.answer = f"已完成信息收集（计划 v{version}），建议按知识库标准答复客户。"
    return out


# ─────────────────────────────────────────────────────────────
def report(r: RunResult) -> None:
    calls = len(r.steps)
    inv = sum(1 for s in r.steps if s.invalid)
    print(f"\n【策略 {r.strategy}】status={r.status}｜模型调用={calls}"
          f"｜累计 token={r.tokens:,}｜无效步比例={inv}/{calls}")
    for s in r.steps:
        flag = " ✗无效" if s.invalid else ""
        print(f"  [{s.kind:>7}] {s.action:<16} {s.detail}"
              f"（本轮输入 {s.tokens:,} tok）{flag}")
    for pv in r.plan_versions:
        print(f"  计划留痕 · {pv}")
    if r.answer:
        print(f"  最终答复：{r.answer}")


def main() -> int:
    ap = argparse.ArgumentParser(description="D-05 规划模式对比（第 5 章）")
    ap.add_argument("--mock", action="store_true", help="离线模式（本 demo 仅演示 mock 形状）")
    ap.add_argument("--strategy", choices=["react", "pe", "both"], default="both")
    ap.add_argument("--perturb", action="store_true", help="第 3 步注入前提失效观察")
    ap.add_argument("--loop-spin", action="store_true", help="让 ReAct 演示循环打转＋检测器")
    ap.add_argument("--task", default=DEMO_TASK)
    ap.add_argument("--dump-trace", metavar="FILE")
    a = ap.parse_args()

    random.seed(20261014)
    tools = build_tools()
    results: list[RunResult] = []
    if a.strategy in ("react", "both"):
        results.append(run_react(a.task, tools, perturb=a.perturb, loop_spin=a.loop_spin))
    if a.strategy in ("pe", "both"):
        results.append(run_pe(a.task, tools, perturb=a.perturb))
    for r in results:
        report(r)

    if len(results) == 2:
        ra, pe = results
        print("\n── 对照要点（5.10.1）──────────────────────────────")
        print(f"调用次数：react {len(ra.steps)} vs pe {len(pe.steps)}"
              f"（PE 多出的是规划费；重规划再另计）")
        print("token 形状：react 逐轮加粗（平方账），pe 执行步输入≈恒定（线性账＋规划常数项）")
        print("提示：--perturb 看两条轨迹在同一事件处分叉；--loop-spin 看打转检测器。")

    if a.dump_trace:
        payload = [{"strategy": r.strategy, "status": r.status, "tokens": r.tokens,
                    "steps": [vars(s) for s in r.steps], "plans": r.plan_versions}
                   for r in results]
        Path(a.dump_trace).write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
        print(f"轨迹已落盘：{a.dump_trace}（计划-执行吻合率的离线复算数据源，反模式一）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
