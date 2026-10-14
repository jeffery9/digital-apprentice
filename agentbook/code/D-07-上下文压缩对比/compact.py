#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""D-07 三种压缩策略（第 6 章 6.4）：截断 / 滚动摘要 / 分层压缩。

每个策略接收完整轮列表，返回"本轮装配视图"（保留哪些原文块、摘
到了什么程度）。质量由 run.py 里的承重字段探针测，成本由累计输入
token 记账，缓存命中由 cache_check.py 的前缀分歧点测——三本账一
一对应 6.3。
"""
from __future__ import annotations

PIN_FIELDS = ("ticket_id", "sla", "claim")   # 承重字段：摘要也钉住原文（6.4 策略二缓解）


def _pin_header(turns):
    """从历史中抽承重字段，永远以结构化形式钉在装配头部。"""
    pins = {}
    for t in turns:
        for k in PIN_FIELDS:
            if k in t and k not in pins:
                pins[k] = t[k]
    return pins


def truncate(blocks, keep_last=8):
    """策略一：FIFO 块截断。不知道什么重要——除非承重字段另路钉住。"""
    dropped = blocks[:-keep_last]
    lost = tuple(k for b in dropped for k in PIN_FIELDS if k in b)
    return {"kept": blocks[-keep_last:], "summary_of": [], "pins": {},
            "lost": lost, "rewrote_history": True}


def rolling_summary(blocks, keep_last=2):
    """策略二：滚动摘要。旧块全部压成一段摘要（模拟信息损失率见 run.py）。"""
    old, recent = blocks[:-keep_last], blocks[-keep_last:]
    return {"kept": recent, "summary_of": old,
            "pins": _pin_header(old) if old else {}, "rewrote_history": True}


def tiered(blocks, raw_zone=8, mid_zone=16):
    """策略三：分层压缩。近区原文、中区逐轮摘要、远区并入滚动总摘要。"""
    n = len(blocks)
    raw = blocks[max(0, n - raw_zone):]
    mid = blocks[max(0, n - mid_zone):max(0, n - raw_zone)]
    far = blocks[:max(0, n - mid_zone)]
    pins = _pin_header(far)                # 仅远区需要钉住承重字段（中区仍是原文）
    kept = ([{"kind": "far_summary", "src": len(far)}] if far else []) \
        + [{"kind": "mid_summary", "src": 1} for _ in mid] + list(raw)
    return {"kept": kept, "summary_of": [],
            "pins": pins, "rewrote_history": bool(far or mid)}


STRATEGIES = {"none": lambda b: {"kept": list(b), "summary_of": [], "pins": {},
                                 "rewrote_history": False},
              "truncate": truncate, "summary": rolling_summary, "tiered": tiered}
