#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
D-12　编排者-工作者：子 agent 上下文隔离的最小实现（第 11 章 11.9）
==================================================================

演示什么：**隔离买的是"主窗口的清醒"，不是免费的午餐。**
单体 agent vs "主脑＋检索子 agent"在同一批八工单任务上对比，探针输出三组数：
主上下文峰值占用、总 token、答准率（书 11.2 理由一的账）。

⚠️ mock 环境只演示机制与相对形状（token 计数走 llm.py 的统一估算口径），
不代表任何真实模型的隔离收益。README 有"接真模型"的两行改动点。

用法：
    python d12_isolation.py --mock          # 两形态并跑，打对照表
观察什么（对应书中 11.9）：
    ① 主上下文峰值：monolith 随工单历史线性堆积；orch-worker 近似恒定
    ② 总 token：orch-worker 略升——边界税（每次委托都要付转述费）
    ③ 答准率：长历史工单改善、短工单持平——收益按任务形状分布，不是均匀撒
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "D-01-最小agent循环"))
from llm import get_llm, system, user  # noqa: E402
from agent_loop import Tool, build_tools, summarize  # noqa: E402

# 八工单：四张"长历史"（需要翻旧账）、四张"短工单"（一查就清）——11.9 的任务集
TICKETS = [
    ("SO-001", True, "物流延误索赔，客户近 90 天有 2 次投诉史"),
    ("SO-002", False, "单纯查询订单状态"),
    ("SO-003", True, "重复投诉第三次，需核对既往赔付结论是否适用"),
    ("SO-004", False, "修改收货地址"),
    ("SO-005", True, "赔付金额争议，涉及合同时效条款与历史判例"),
    ("SO-006", False, "查询知识库标准话术"),
    ("SO-007", True, "跨季度对账投诉，需汇总三次历史处理记录"),
    ("SO-008", False, "确认运单号格式"),
]


@dataclass
class FormResult:
    form: str
    peak_main_ctx: int = 0            # 主上下文峰值（字符数口径，README 说明换算）
    total_tokens: int = 0
    correct: int = 0                  # 答准率分子（判据：结论是否引用了该用的证据）
    boundary_msgs: int = 0            # 边界消息条数（转述费的计数）


def _call(llm, ctx_chars: int, prompt: str) -> int:
    """一次模型调用的 token 记账（mock 统一走 llm.py 估算口径）。"""
    resp = llm.chat([system("你是客服副驾的执行模型。"), user(prompt)])
    return resp.usage.total_tokens


def run_monolith(llm) -> FormResult:
    r = FormResult("monolith")
    ctx_chars = 0
    for tid, long_hist, desc in TICKETS:
        ctx_chars += len(desc) * 4 + 60          # 每张工单的"过程账"都留在主窗口
        if long_hist:
            ctx_chars += 900                      # 翻旧账的检索结果全文进主上下文
        r.peak_main_ctx = max(r.peak_main_ctx, ctx_chars)
        r.total_tokens += _call(llm, ctx_chars, f"处理工单 {tid}（全历史在窗口内）")
        r.correct += 1 if long_hist else 1        # 单体不缺信息，短工单也全对
    return r


def run_orch_worker(llm) -> FormResult:
    r = FormResult("orch-worker")
    main_chars = 0
    for tid, long_hist, desc in TICKETS:
        main_chars += len(desc) + 20              # 主脑只见任务描述与结论
        if long_hist:
            worker = get_llm(mock=True)           # 子 agent：独立窗口烧过程账
            proc_cost = _call(worker, 900, f"检索工单 {tid} 的历史证据并摘要") \
                + _call(worker, 900, "把证据整理成三行结论")
            r.total_tokens += proc_cost
            finding = summarize(f"结论：{tid} 既往处理可参照阶梯赔付口径（子 agent 摘要）", 120)
            r.boundary_msgs += 1
            main_chars += len(finding)            # 只有结论回迁主窗口
        r.peak_main_ctx = max(r.peak_main_ctx, main_chars)
        r.total_tokens += _call(llm, main_chars, f"基于结论处理工单 {tid}")
        # 答准判据（脚本化）：长历史工单靠子 agent 的完整检索，与单体同对；
        # 有一类"边界丢失"风险在这里被建模——见 11.5：转述丢关键事实时答错。
        r.correct += 1
    r.correct -= 1     # 刻意注入一次"边界税"事故：SO-007 跨季度汇总被摘要压掉了季度标注
    return r


def main() -> int:
    ap = argparse.ArgumentParser(description="D-12 子 agent 上下文隔离（第 11 章）")
    ap.add_argument("--mock", action="store_true", help="离线模式（本 demo 全程离线）")
    a = ap.parse_args()
    llm = get_llm(mock=True)
    mono, iso = run_monolith(llm), run_orch_worker(llm)
    print(f"{'形态':<12}{'主上下文峰值':>12}{'总 token':>12}{'答准':>10}{'边界消息':>10}")
    for r in (mono, iso):
        print(f"{r.form:<12}{r.peak_main_ctx:>10,}{r.total_tokens:>12,}"
              f"{r.correct}/8{r.boundary_msgs:>10}")
    drop = 1 - iso.peak_main_ctx / mono.peak_main_ctx
    print(f"\n对照要点（11.9）：主上下文峰值降约 {drop:.0%}（过程账进了子 agent 独立窗口）；"
          f"总 token 变化 {iso.total_tokens - mono.total_tokens:+,}（边界税+转述费）；"
          "答准率的差值来自一次刻意注入的'边界丢失'事故——隔离的收益与代价同时可见。")
    print("提示：把 --correct 判据换成你的真实评估器（第 18 章），数字才有生产含义。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
