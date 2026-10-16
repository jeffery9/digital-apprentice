#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""D-08 工具粒度对比：原子 / 聚合 / code-mode 三种切法的调用数、token 与恢复成本（第 7 章）

演示什么：**同一业务任务（20 个订单项折扣重算 + 2 笔例外退款 + 收尾），三种工具粒度的账。**
  · 步数账——模型侧调用次数（每步 = 一次决策机会，也是出错机会）
  · token 账——累计输入上下文（平方重放，1.3.3 口径只看形状）+ 工具定义常驻税
  · 恢复账——注入"第 14 次调用失败"后，各粒度需要重放的单元数（幂等边界投影，书 7.5/16.5）

⚠️ 读数前必看：本 demo 不调用任何模型。选择器是脚本化替身（按预置剧本走），
只演示三种粒度的**结构性差异**，不代表真实模型的选型行为（免责口径同 D-07，QWEN.md 铁律三）。

用法：
    python run.py --strategy atomic      # 12 原子工具逐步编排
    python run.py --strategy aggregate   # 3 业务聚合工具
    python run.py --strategy code        # run_python + SDK
    python run.py --all                  # 三口径并跑对照表
    python run.py --all --inject-fail    # 在第 14 次调用注入失败，看恢复成本
"""
from __future__ import annotations

import argparse

from tools import registry_for, schema_tokens

N_ITEMS = 20          # 订单项数
N_EXCEPTIONS = 2      # 命中例外退款的项数


def _script(strategy: str):
    """预置剧本：模拟"合格模型"在各粒度下完成任务的最短调用序列。"""
    if strategy == "atomic":
        steps = [("db_list_order_items", 1), ("db_get_customer_tier", 1)]
        steps += [("db_get_item_price", N_ITEMS), ("calc_discount_rate", N_ITEMS),
                  ("calc_line_amount", N_ITEMS)]
        steps += [("db_check_exception_rule", N_ITEMS)]
        steps += [("refund_request", N_EXCEPTIONS)]
        steps += [("invoice_prepare", 1), ("email_draft", 1), ("email_send", 1),
                  ("ticket_append_note", 1), ("ticket_close", 1)]
        return steps
    if strategy == "aggregate":
        return [("recalc_order_discounts", 1), ("apply_exception_refunds", 1),
                ("finalize_ticket", 1)]
    if strategy == "code":
        return [("run_python", 1)]
    raise ValueError(strategy)


def run(strategy: str, inject_fail: bool = False):
    reg = {t["name"]: t for t in registry_for(strategy)}
    script = _script(strategy)
    calls = sum(n for _, n in script)

    # ---- 步数账 & token 账（平方重放启发式：第 k 步携带 ~base + growth*k 的上下文）----
    base = 900                       # 系统提示 + 任务描述 + 工具定义之外的固定项
    per_step_ctx = 120               # 每步新增的观察进入历史的量
    total_in = 0
    ctx = base + schema_tokens(strategy)
    for k in range(1, calls + 1):
        total_in += ctx
        ctx += per_step_ctx
    schema_tax_total = schema_tokens(strategy) * calls  # 定义税按轮摊（简化：每轮全带）

    # ---- 成功率账（每步一个独立出错概率 p_step；结构错误率随步数复合）----
    p_step = 0.02                    # 单步选择/填参错误率（mock 设定）
    success = (1 - p_step) ** calls

    # ---- 恢复账：在第 14 次调用注入失败，各粒度要重放多少"幂等单元" ----
    recovery = None
    if inject_fail and calls >= 14:
        if strategy == "atomic":
            # 已完成的前 13 个原子步里含不可逆 refund 吗？剧本中 refund 在很后面——
            # 但"从哪一步续起"本身需要模型再决策：重放单元 = 已完成的读步骤数（可安全重放）
            # + 写步骤需逐个查幂等登记表。最坏情形：整段折扣循环重来。
            recovery_units = N_ITEMS * 3   # 三个逐行循环全部重放
            recovery_note = "需决定续点；循环段整体重放（读步可重放，靠幂等键兜底）"
        elif strategy == "aggregate":
            recovery_units = 1             # 失败发生在 apply_exception_refunds，服务端事务内回滚，整单元重放
            recovery_note = "聚合工具内部事务保证恰好一次；重放 1 个单元即可"
        else:  # code
            recovery_units = 1             # 沙箱内代码重跑；副作用只在 sdk.refund 处出沙箱，幂等键去重
            recovery_note = "SDK 出口幂等；代码重跑零外部副作用（沙箱即隔离，书 7.5）"
        recovery = (recovery_units, recovery_note)

    return {
        "strategy": strategy,
        "tools": len(reg),
        "calls": calls,
        "success": success,
        "total_in_tok": total_in,
        "schema_tax": schema_tokens(strategy),
        "recovery": recovery,
    }


def table(rows):
    hdr = f"{'粒度':<10}{'工具数':>6}{'调用数':>8}{'端到端成功':>10}{'累计输入tok':>12}{'定义税tok':>10}"
    print(hdr); print("-" * len(hdr.encode('gbk', errors='ignore')))
    for r in rows:
        print(f"{r['strategy']:<10}{r['tools']:>6}{r['calls']:>8}{r['success']:>9.1%}"
              f"{r['total_in_tok']:>12,}{r['schema_tax']:>10,}")
    if any(r["recovery"] for r in rows):
        print("\n[恢复账] 第 14 次调用注入失败后：")
        for r in rows:
            if r["recovery"]:
                u, note = r["recovery"]
                print(f"  {r['strategy']:<10} 重放单元={u:>3}  —— {note}")


def main():
    ap = argparse.ArgumentParser(description="D-08 工具粒度对比（--mock 全程离线，无需网络与 API key）")
    ap.add_argument("--strategy", choices=["atomic", "aggregate", "code"])
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--inject-fail", action="store_true")
    ap.add_argument("--mock", action="store_true", help="显式声明离线模式（本 demo 默认即离线）")
    args = ap.parse_args()
    strategies = ["atomic", "aggregate", "code"] if args.all or not args.strategy else [args.strategy]
    rows = [run(s, args.inject_fail) for s in strategies]
    table(rows)
    print("\n观察（对应书中 7.3/7.8）：")
    print("  ① 调用数 atomic≈80 vs aggregate=3 vs code=1 —— 每步都是决策机会，也是出错机会")
    print("  ② 端到端成功列随调用数指数衰减（0.98^n）——粒度即可靠性")
    print("  ③ 累计输入 token 呈平方形状分化；atomic 约为 code 的数十倍（只看形状）")
    print("  ④ --inject-fail 下恢复账：聚合/code 重放 1 个幂等单元，atomic 需重放整个循环段")


if __name__ == "__main__":
    main()
