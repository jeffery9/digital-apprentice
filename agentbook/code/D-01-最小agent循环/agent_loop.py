#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
D-01　最小 agent 循环（第 1 章）
================================

演示什么：**agent 是循环，不是魔法。** 用不到 40 行核心代码实现第 1 章 1.3 节描述的
四要素循环，直调模型接口，不依赖任何 agent 框架。全书后续 demo 都往这段代码上加东西
（D-03/04 加路由与网关，D-05 加规划，D-07 加上下文压缩，D-11 加状态机与检查点）。

运行：
    python agent_loop.py --mock                    # 离线跑通全流程（无网络、无 key）
    python agent_loop.py --mock --max-steps 15     # 观察 token 的平方级增长
    python agent_loop.py --mock --tool-fail-rate .5  # 观察预算熔断先于"模型说完成"触发
    python agent_loop.py --mock --no-summarize     # 对比上下文压缩的作用（第 6 章预告）
    python agent_loop.py --mock --dump-trace trace.json
    python agent_loop.py --model <快照ID>          # 真实调用（需 LLM_API_KEY / LLM_ENDPOINT）

观察什么（对应书中 1.7 节）：
    ① 步数 ↔ 累计 token 是否按 T(n) ≈ 400n² + 800n 的量级走
    ② 三个停止条件谁先触发
    ③ summarize() 开关对 token 曲线的影响
    ④ --dump-trace 落盘的轨迹：没有它就无法归因（第 18 章）
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from llm import (LLM, Message, Response, ToolSpec, Usage, get_llm,  # noqa: E402
                 system, tool_result, user)

SYSTEM_PROMPT = (
    "你是智航科技的客服副驾。你可以调用工具查询订单、物流、知识库与历史工单，"
    "然后给出一段**给客服人员看的处理建议**（不直接发给客户）。"
    "只使用工具返回的信息，不要编造；信息不足时明确说明缺什么。"
)


# ─────────────────────────────────────────────────────────────
# 工具：duck-typing 兼容 ToolSpec（有 name/description/parameters/to_openai），
# 因此可以直接传给 llm.chat(tools=...)——书中片段照此写。
# ─────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Tool:
    name: str
    description: str                                  # 给模型读的 API 文档（第 7 章）
    parameters: dict[str, Any]
    fn: Callable[[dict[str, Any]], str]
    side_effect: Literal["read", "compensable", "irreversible"] = "read"
    fail_rate: float = 0.0

    def to_openai(self) -> dict[str, Any]:            # 与 ToolSpec 同签名
        return ToolSpec(self.name, self.description, self.parameters).to_openai()

    def invoke(self, args: dict[str, Any]) -> str:
        """执行层：错误整形在这里发生。给模型看的错误消息质量要求比给人看的高
        （第 1 章 1.3.4 结论③）——所以错误里必须写清"哪个参数、期望什么、收到什么"。"""
        if self.fail_rate and random.random() < self.fail_rate:
            bad = next(iter(args), "参数")
            return (f"ERROR: 参数 {bad} 格式不合法，期望 ISO 8601 日期（如 2026-10-13），"
                    f"收到 {args.get(bad)!r}。请修正后重试。")
        try:
            return self.fn(args)
        except Exception as e:                                        # noqa: BLE001
            return f"ERROR: {type(e).__name__}: {e}。请检查参数后重试。"


def summarize(text: str, limit: int = 260) -> str:
    """工具结果入上下文前先摘要——减缓 1.3.3 的平方膨胀（第 6 章的最小演示）。

    刻意做得很笨（截断＋计数留痕）：真实的摘要策略有三种，取舍见第 6 章。
    关键不是摘要质量，而是**截断必须显式、必须留痕**，否则模型不知道自己失忆了。
    """
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…[已截断 {len(text) - limit} 字符，如需完整内容请重新查询]"


# ─────────────────────────────────────────────────────────────
# 核心：agent 循环（书中 1.7 节列出的骨干即此段）
# ─────────────────────────────────────────────────────────────
@dataclass
class Result:
    status: Literal["done", "max_steps", "budget_exceeded"]
    steps: int = 0
    text: str | None = None
    tokens: int = 0
    context: list[Message] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)


def run_agent(task: str, tools: dict[str, Tool], *, max_steps: int = 8,
              token_budget: int = 48_000, llm: LLM | None = None,
              use_summarize: bool = True, verbose: bool = True,
              system_prompt: str = SYSTEM_PROMPT) -> Result:
    llm = llm or get_llm()                      # 薄适配层：可换供应商，可换 mock
    ctx: list[Message] = [system(system_prompt), user(task)]
    spent = 0
    trace: list[dict[str, Any]] = []

    for step in range(1, max_steps + 1):        # 停止条件②：步数上限（代码持有）
        resp: Response = llm.chat(ctx, tools=list(tools.values()))
        spent += resp.usage.total_tokens
        trace.append({"step": step, "model": resp.model,
                      "prompt_tokens": resp.usage.prompt_tokens,
                      "cumulative_tokens": spent,
                      "tool_calls": [c.name for c in resp.tool_calls],
                      "finish_reason": resp.finish_reason})
        if verbose:
            calls = ",".join(c.name for c in resp.tool_calls) or "—"
            print(f"  第 {step:>2} 轮｜本轮输入 {resp.usage.prompt_tokens:>7,} tok"
                  f"｜累计 {spent:>8,} tok｜动作 {calls}")

        if spent > token_budget:                # 停止条件③：预算熔断（代码持有）
            return Result("budget_exceeded", step, None, spent, ctx, trace)

        if not resp.tool_calls:                 # 停止条件①：模型声明完成（不可单独信任）
            return Result("done", step, resp.text, spent, ctx, trace)

        ctx.append(resp.as_message())
        for call in resp.tool_calls:
            tool = tools.get(call.name)
            if tool is None:                    # 模型可能幻觉出不存在的工具
                out = (f"ERROR: 未注册的工具 {call.name!r}。可用工具："
                       f"{sorted(tools)}。请从中选择。")
            elif tool.side_effect == "irreversible":
                out = "BLOCKED: 该动作不可逆，需要人工审批（第 15、17 章）。"
            else:
                out = tool.invoke(call.args)
            shaped = summarize(out) if use_summarize else out
            ctx.append(tool_result(call.id, shaped, name=call.name))
    return Result("max_steps", max_steps, None, spent, ctx, trace)   # 降级出口


# ─────────────────────────────────────────────────────────────
# demo 用的四个只读工具（智航客服副驾场景，数据全部虚构）
# ─────────────────────────────────────────────────────────────
def _long_text(topic: str) -> str:
    return (f"{topic}｜处理标准（虚构数据）：" + "客户主张物流延迟并要求赔付时，"
            "应先核对合同中的时效条款与不可抗力约定；若确属承运方责任，"
            "按延误天数阶梯赔付，单日赔付上限为合同月费的一定比例；"
            "若属不可抗力，出具承运方证明并协商顺延。" * 6)


def build_tools(fail_rate: float = 0.0) -> dict[str, Tool]:
    P_STR = {"type": "object", "properties": {"query": {"type": "string"}}}
    return {
        "search_kb": Tool(
            "search_kb", "在客服知识库中检索处理标准与话术。当需要确认公司政策、"
                          "赔付标准、操作规范时使用；不要用它查具体客户数据。",
            P_STR, lambda a: _long_text(f"知识库命中：{a.get('query', '')}"),
            "read", fail_rate),
        "get_order": Tool(
            "get_order", "按订单号查询订单详情（客户、合同、SLA 等级）。"
                         "只读；参数 query 传订单号。",
            P_STR, lambda a: f"订单 {a.get('query')}：客户=华东制造，SLA=银级，"
                             f"月费档=中，合同含时效条款。", "read", fail_rate),
        "get_logistics": Tool(
            "get_logistics", "查询物流轨迹与延误情况。只读；参数 query 传运单号。",
            P_STR, lambda a: f"运单 {a.get('query')}：预计 3 日达，实际 6 日达，"
                             f"延误原因=承运方中转积压（非不可抗力）。", "read", fail_rate),
        "search_history": Tool(
            "search_history", "查询该客户的历史工单与既往处理结论。只读；"
                              "用于判断是否重复投诉、既往是否已赔付。",
            P_STR, lambda a: f"客户历史：近 90 天 2 次投诉，1 次已赔付（承运方责任），"
                             f"1 次判定为不可抗力未赔付。", "read", fail_rate),
    }


DEMO_TASK = ("客户「华东制造」投诉订单 SO-20261013-0087 物流延误 3 天，要求赔付。"
             "请查询相关信息，给出一段给客服人员的处理建议。")


# ─────────────────────────────────────────────────────────────
# 轨迹落盘格式（版本化契约）——D-20（第 18 章）直接消费此文件。
# 跨章数据接口的形状本身就是 18.6 span schema 的先声：字段只增不改。
# ─────────────────────────────────────────────────────────────
TRACE_FORMAT = "agentbook-trace/v1"


def trace_document(r: Result, task: str, model_id: str) -> dict[str, Any]:
    return {"format": TRACE_FORMAT, "task": task, "model": model_id,
            "status": r.status, "steps": r.steps, "tokens": r.tokens,
            "answer": r.text, "trace": r.trace,
            "messages": [{"role": m.role, "content": m.text_only(),
                          "tool_calls": [c.name for c in m.tool_calls]}
                         for m in r.context]}


def dump_trace(r: Result, task: str, model_id: str, path: str) -> None:
    from pathlib import Path as _P
    _P(path).write_text(json.dumps(trace_document(r, task, model_id),
                                   ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="D-01 最小 agent 循环（第 1 章）")
    ap.add_argument("--mock", action="store_true", help="离线模式：无网络、无 API key")
    ap.add_argument("--model", default=None, help="模型快照 ID（默认取 LLM_MODEL）")
    ap.add_argument("--max-steps", type=int, default=8, help="停止条件②：步数上限")
    ap.add_argument("--token-budget", type=int, default=48_000,
                    help="停止条件③：预算熔断阈值")
    ap.add_argument("--tool-fail-rate", type=float, default=0.0,
                    help="工具随机失败率；调高可观察预算熔断先触发")
    ap.add_argument("--no-summarize", action="store_true",
                    help="关闭工具结果摘要，对比 token 曲线（第 6 章预告）")
    ap.add_argument("--dump-trace", metavar="FILE", help="把轨迹落盘为 JSON")
    ap.add_argument("--seed", type=int, default=20261013, help="随机种子（保证可复现）")
    ap.add_argument("--task", default=DEMO_TASK, help="任务描述")
    a = ap.parse_args()

    random.seed(a.seed)
    llm = get_llm(a.model, mock=a.mock)
    print(f"模型：{llm.model_id}｜模式：{'mock 离线' if a.mock else '真实调用'}")
    print(f"停止条件：步数上限 {a.max_steps}｜token 预算 {a.token_budget:,}"
          f"｜摘要：{'关' if a.no_summarize else '开'}\n")

    r = run_agent(a.task, build_tools(a.tool_fail_rate), max_steps=a.max_steps,
                  token_budget=a.token_budget, llm=llm,
                  use_summarize=not a.no_summarize)

    print(f"\n结果：status={r.status}｜步数={r.steps}｜累计 token={r.tokens:,}")
    if r.text:
        print(f"模型最终答复：{r.text}")
    else:
        print("（未产出最终答复——这正是“触达上限之后怎么办”要设计的降级出口，见 1.3.5）")

    # 平方级增长的实证对照（1.3.3 的算式）
    n = r.steps
    print(f"\n实测累计 token = {r.tokens:,}｜T(n)≈400n²+800n 在 n={n} 时为 "
          f"{400 * n * n + 800 * n:,}（口径不同，只看量级与增长形状）")

    if a.dump_trace:
        dump_trace(r, a.task, llm.model_id, a.dump_trace)
        print(f"轨迹已落盘：{a.dump_trace}（格式 {TRACE_FORMAT}，D-20 可直接消费）")
    return 0 if r.status == "done" else 2


if __name__ == "__main__":
    raise SystemExit(main())
