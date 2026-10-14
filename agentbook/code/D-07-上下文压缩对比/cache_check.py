#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""D-07 前缀稳定性检查（第 6 章 6.8）：缓存友好序列的仪器。

公共前缀长度 = 本轮装配与上一轮装配从头逐 token 比对的首个分歧位置。
分歧越早，KV-cache 可复用越少。规范一（稳定前缀置顶、易变沉底）与
规范二（增量追加不重写）的效果直接体现在这条线上。
"""
from __future__ import annotations


def common_prefix_len(a, b):
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def report(seq_prev, seq_cur, label=""):
    cp = common_prefix_len(seq_prev, seq_cur)
    pct = (cp / len(seq_cur) * 100) if seq_cur else 0.0
    print(f"  {label}前缀复用 {cp:>6} tok（{pct:5.1f}%）· 本轮装配 {len(seq_cur)} tok")
    return cp
