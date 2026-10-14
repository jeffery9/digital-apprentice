#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""D-07 装配器与预算护栏（第 6 章 6.2／6.3）：把一轮请求按七成分归账。

演示对象是**装配策略**，不是模型能力：同一批内容，不同的保留/压缩/注入
决定产生不同的 token 账单与前缀稳定性。对应书中表 6-1 的成分清单与
预算护栏卡的"配额＋水位线＋泄洪口"三件套。
"""
from __future__ import annotations

from dataclasses import dataclass, field

COMPONENTS = ["system", "tools", "history_raw", "summary", "tool_output",
              "retrieval", "memory"]


@dataclass
class Budget:
    """预算护栏的最小实现：每类成分一个配额，总量一条水位线。

    超限策略不允许空白（书 6.11 视角盒①的纪律）：
      · tool_output / retrieval 超限 → 摘要入窗 + 原文落盘留指针（6.7）
      · history_raw 超水位 → 交给压缩策略（compact.py）
    """
    window: int = 48_000                      # 有效注意力工作线（≠标称窗口，6.3.2）
    quota: dict = field(default_factory=lambda: {
        "system": 2_000, "tools": 6_000, "history_raw": 20_000,
        "summary": 4_000, "tool_output": 2_000, "retrieval": 8_000,
        "memory": 2_000})
    warn_at: float = 0.60
    compact_at: float = 0.80
    reject_at: float = 0.95

    def check(self, sizes: dict) -> list:
        notes = []
        total = sum(sizes.get(c, 0) for c in COMPONENTS)
        if total > self.window * self.reject_at:
            notes.append("REJECT：超过拒绝线，新注入一律走落盘")
        elif total > self.window * self.compact_at:
            notes.append("COMPACT：越过压缩水位线（触发 6.4 整理术）")
        elif total > self.window * self.warn_at:
            notes.append("WARN：预算告警区")
        for c, s in sizes.items():
            q = self.quota.get(c)
            if q and s > q:
                notes.append(f"QUOTA：{c} {s}>{q} → 摘要入窗+原文落盘留指针")
        return notes


def dump_composition(sizes: dict, window: int) -> str:
    """成分归账打印（6.11 排查清单第 1 步的仪器）。"""
    total = sum(sizes.values()) or 1
    lines = [f"{'成分':<12}{'token':>8}{'占比':>8}", "-" * 30]
    for c in COMPONENTS:
        s = sizes.get(c, 0)
        lines.append(f"{c:<12}{s:>8}{s / total:>7.1%}")
    lines.append("-" * 30)
    lines.append(f"{'合计':<12}{total:>8}{total / window:>7.1%}  （工作线 {window}）")
    return "\n".join(lines)
