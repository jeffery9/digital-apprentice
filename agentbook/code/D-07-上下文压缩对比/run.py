#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""D-07　上下文压缩对比：三种整理术在 50 轮长任务上的质量-成本-缓存三账（第 6 章）
================================================================================

演示什么：**同一批会话内容，四种装配策略各自把三本账（书 6.3）付成什么形状。**
  · 成本账——累计输入 token（平方重放 vs 整理的交换率）
  · 质量账——承重字段探针（订单号/SLA/主张在第 10/25/40 轮还能不能答对）
  · 缓存账——每轮装配与前一轮的公共前缀长度（cache_check.py）

⚠️ 读数前必看：本 demo 不调用任何模型——它模拟的是**装配层**的行为。
摘要的信息损失、探针的命中判定都是脚本化替身，只演示机制与相对形状，
不代表任何真实模型的压缩质量（免责口径同 D-02，QWEN.md 铁律三）。
真实对比请用你的会话日志重放（README"接真模型"一节）。

用法：
    python run.py --strategy none            # 全量保留（对照组）
    python run.py --strategy truncate        # FIFO 块截断
    python run.py --strategy summary         # 滚动摘要＋承重字段钉住
    python run.py --strategy tiered          # 分层压缩（生产推荐形）
    python run.py --all                      # 四策略并跑，打对照表
    python run.py --all --dump-composition   # 附第 40 轮的七成分归账（表 6-1 仪器）

观察什么（对应书中 6.10）：
    ① none 的成本曲线是平方形状；tiered 前 10 轮反而更贵（整理本身的开销）
    ② truncate 在第 25 轮后丢订单号——截断不知道什么重要
    ③ summary 的探针"幻觉通过"：关键词碰对了，细节已经错
    ④ 缓存账：none/tiered 高复用，truncate 每轮动历史边界→复用归零
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from compose import Budget, dump_composition          # noqa: E402
from compact import STRATEGIES                        # noqa: E402
from cache_check import common_prefix_len             # noqa: E402

TURNS = 50
PIN_KEYS = ("ticket_id", "sla", "claim")


def make_script():
    """50 轮工单会话剧本（虚构数据）。承重信息刻意放在最早几轮。"""
    blocks = []
    for i in range(1, TURNS + 1):
        b = {"idx": i, "kind": "turn", "tokens": 300 + (i % 7) * 60}
        if i == 1:
            b.update(ticket_id="SO-20261014-0087", sla="银级",
                     claim="货物损坏并要求合并赔付")
            b["tokens"] += 200
        if i == 3:
            b["promise"] = "承诺 48h 内给出定损方案"
        if i % 9 == 5:                       # 偶发巨量工具返回（预算护栏的头号敌人）
            b["kind"], b["tokens"] = "tool_output", 5_000
        if i % 6 == 2:
            b["kind"], b["tokens"] = "retrieval", 1_800
        blocks.append(b)
    return blocks


def size_of(view):
    """装配视图 → token 规模（含固定开销 system+tools）。"""
    fixed = 2_000 + 6_000
    hist = sum(b["tokens"] for b in view["kept"] if b.get("kind") == "turn")
    raw_out = sum(b["tokens"] for b in view["kept"] if b.get("kind") in ("tool_output", "retrieval"))
    mid = sum(120 for b in view["kept"] if b.get("kind") == "mid_summary")
    far = 400 if any(b.get("kind") == "far_summary" for b in view["kept"]) else 0
    pins = len(view["pins"]) * 40
    comp = {"system": 2_000, "tools": 6_000, "history_raw": hist,
            "summary": mid + far, "tool_output": raw_out, "retrieval": 0,
            "memory": pins}
    return fixed + hist + raw_out + mid + far + pins, comp


def probe(view, which):
    """承重字段探针：答案能否从"当下可见上下文"里取到（第二本账的质量面）。"""
    kept_turns = [b for b in view["kept"] if b.get("kind") == "turn"]
    all_turns = {b["idx"]: b for b in SCRIPT}
    src_idx = {"ticket_id": 1, "sla": 1, "claim": 1, "promise": 3}[which]
    src = all_turns[src_idx]
    if which in view["pins"]:
        return True, "原文钉住"
    if any(b.get("idx") == src_idx for b in kept_turns):
        return True, "原文在场"
    # 不在原文区也不在钉住区：截断=直接丢弃；摘要区=模拟"细节蒸发"
    note = "被截断丢弃（承重墙在最老块里）" if view.get("summary_of") is not None \
        and not view["summary_of"] and which in view.get("lost", ()) else None
    if note:
        return False, note
    if which in ("ticket_id",):
        return False, "已被摘要吞噬（细节蒸发）"
    if which in ("claim",):
        return True, "摘要碰巧保住（幻觉通过风险）"
    return False, "承诺细节蒸发"


SCRIPT = make_script()


def short_view_cost(strategy, horizon=10):
    """短任务只比"历史+摘要区"的规模——固定开销（system/tools）各策略相同，
    计入会把策略间差异稀释掉。tiered 在前 10 轮更贵的原因在此可见。"""
    fn = STRATEGIES[strategy]
    tot = 0
    for n in range(1, horizon + 1):
        v = fn(SCRIPT[:n])
        tot += sum(b["tokens"] if b.get("kind") == "turn" else 120
                   for b in v["kept"]) + len(v["pins"]) * 40
    return tot


def run(strategy, dump_comp=False, horizon=TURNS):
    budget, fn = Budget(), STRATEGIES[strategy]
    cost, probes, prefix_hits, prefix_total = 0, [], 0, 0
    prev_seq = None
    events = []
    for n in range(1, horizon + 1):
        view = fn(SCRIPT[:n])
        size, comp = size_of(view)
        cost += size
        notes = budget.check(comp)
        for msg in notes:
            if msg.startswith(("COMPACT", "REJECT")):
                events.append(f"第{n}轮 {msg}")
        if n in (10, 25, 40) and n <= horizon:
            ok, why = probe(view, "ticket_id")
            probes.append((n, ok, why))
        seq = tuple(b["idx"] if b.get("kind") == "turn" else b.get("kind","?") for b in view["kept"])
        if prev_seq is not None:
            cp = common_prefix_len(list(prev_seq), list(seq))
            prefix_hits += cp
            prefix_total += len(seq)
        prev_seq = seq
    if dump_comp:
        print(dump_composition(comp, budget.window))
    q = sum(1 for _, ok, _ in probes if ok) / max(1, len(probes))
    reuse = prefix_hits / max(1, prefix_total)
    return {"strategy": strategy, "cost": cost, "quality": q,
            "cache": reuse, "probes": probes, "events": events}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--strategy", default=None,
                    choices=list(STRATEGIES))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--dump-composition", action="store_true")
    ap.add_argument("--mock", action="store_true",
                    help="显式声明离线模式（本 demo 全程离线，此开关为 no-op 别名，统一各 demo 的 --mock 口径）")
    args = ap.parse_args()
    names = list(STRATEGIES) if args.all else [args.strategy or "tiered"]
    base = None
    print(f"{'策略':<10}{'账单(相对)':>12}{'探针通过率':>12}{'前缀复用率':>12}")
    print("-" * 50)
    results = {}
    for s in names:
        r = run(s, dump_comp=args.dump_composition and s == names[-1])
        results[s] = r
    short = {s2: short_view_cost(s2) for s2 in names}
    short_base = short.get("none") or short_view_cost("none")
    base = results.get("none") or run("none")
    for s in names:
        r = results[s]
        rel = r["cost"] / base["cost"]
        print(f"{s:<10}{rel:>10.2f}x{r['quality']:>12.0%}{r['cache']:>11.0%}")
    print("\n── 短任务对照（前 10 轮账单 vs none）——整理本身是开销，别过早压缩 ──")
    for s2 in names:
        print(f"  {s2:<9}{short[s2]/short_base:>8.2f}x")
    print("\n── 探针明细（第 10/25/40 轮问：客户订单号是什么？）──────")
    for s in names:
        for n, ok, why in results[s]["probes"]:
            mark = "✓" if ok else "✗"
            print(f"  {s:<9} 第{n:>2}轮 {mark} {why}")
    ev = [e for s in names for e in results[s]["events"]]
    if ev:
        print("\n── 水位线事件 ──")
        for e in sorted(set(ev))[:6]:
            print("  " + e)
    print("\n提示：mock 数字只演示形状，不代表真实模型压缩质量（见文件头免责）。")


if __name__ == "__main__":
    main()
