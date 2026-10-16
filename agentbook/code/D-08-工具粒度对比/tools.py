#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""D-08 工具注册表：三种粒度下的契约定义（第 7 章）

本文件是"契约即数据"的演示：每个工具是一个 dict，含书中 7.2/7.5 要求的一等字段——
name / description(互斥判据式) / params / max_response_tokens / side_effect。
atomic    : 12 个原子工具（贴近底层原语）
aggregate : 3 个业务聚合工具（一次调用封装流程）
code      : 1 个 run_python 入口 + SDK 文档（代码接口）

注意：这里不接真模型。选择器在 run.py 中是脚本化替身（装配层模拟，免责口径同 D-07）。
"""
from __future__ import annotations

# ---------- 原子工具：12 个 ----------
ATOMIC_TOOLS = [
    {"name": "db_list_order_items", "desc": "列出工单关联的订单项 ID（分页 limit/cursor）", "side_effect": "read", "max_resp_tok": 800},
    {"name": "db_get_item_price", "desc": "查单个订单项的成交价与原价", "side_effect": "read", "max_resp_tok": 120},
    {"name": "db_get_customer_tier", "desc": "查客户等级（gold/silver/bronze）", "side_effect": "read", "max_resp_tok": 60},
    {"name": "calc_discount_rate", "desc": "按等级与促销规则算折扣率", "side_effect": "read", "max_resp_tok": 40},
    {"name": "calc_line_amount", "desc": "算单行折后金额", "side_effect": "read", "max_resp_tok": 40},
    {"name": "db_check_exception_rule", "desc": "判断该订单是否命中例外退款规则", "side_effect": "read", "max_resp_tok": 80},
    {"name": "refund_request", "desc": "发起一笔退款（幂等键必填；仅当例外规则命中时调用）", "side_effect": "write_irreversible", "max_resp_tok": 100},
    {"name": "invoice_prepare", "desc": "为重算后的订单准备红字发票草稿", "side_effect": "write_reversible", "max_resp_tok": 150},
    {"name": "email_draft", "desc": "起草客户通知邮件（不发送）", "side_effect": "read", "max_resp_tok": 300},
    {"name": "email_send", "desc": "发送已确认的邮件草稿", "side_effect": "write_irreversible", "max_resp_tok": 60},
    {"name": "ticket_append_note", "desc": "向工单追加处理记录", "side_effect": "write_reversible", "max_resp_tok": 50},
    {"name": "ticket_close", "desc": "关闭工单（所有必交付项完成后才允许）", "side_effect": "write_irreversible", "max_resp_tok": 50},
]

# ---------- 聚合工具：3 个 ----------
AGGREGATE_TOOLS = [
    {"name": "recalc_order_discounts", "desc": "整单折扣重算：遍历全部订单项、按等级套用规则、产出逐行明细（内部完成 db_*/calc_* 全家）", "side_effect": "write_reversible", "max_resp_tok": 900},
    {"name": "apply_exception_refunds", "desc": "例外退款批处理：检测命中例外规则的项并批量发起（幂等内建）", "side_effect": "write_irreversible", "max_resp_tok": 400},
    {"name": "finalize_ticket", "desc": "收尾三件套：红字发票＋客户通知＋关单（审批位内置）", "side_effect": "write_irreversible", "max_resp_tok": 200},
]

# ---------- code-mode：1 个入口 + SDK 文档 ----------
CODE_TOOL = {
    "name": "run_python",
    "desc": "在沙箱内执行 Python。售后 SDK 以库形式可用（见 sdk_doc），循环/条件/异常由代码表达。",
    "side_effect": "sandboxed",  # 隔离等级替代闸门等级（书 7.5 不适用场景）
    "max_resp_tok": 1200,
}

SDK_DOC = """from aftersale import sdk
sdk.list_items(ticket_id) -> list[Item]        # Item(id, price, orig)
sdk.customer_tier(ticket_id) -> str        # 'gold'|'silver'|'bronze'
sdk.discount_rate(tier, item) -> float
sdk.check_exception_rule(order_id) -> bool
sdk.refund(item_id, amount, idem_key) -> Receipt   # 幂等键必填
sdk.notify_and_close(ticket_id, summary) -> None   # 内含审批门
"""

def registry_for(strategy: str):
    if strategy == "atomic":
        return ATOMIC_TOOLS
    if strategy == "aggregate":
        return AGGREGATE_TOOLS
    if strategy == "code":
        return [CODE_TOOL]
    raise ValueError(strategy)

def schema_tokens(strategy: str) -> int:
    """粗估工具定义的常驻 token 税（4 字符≈1 token 的启发式，只看形状）。"""
    reg = registry_for(strategy)
    text = "".join(t["name"] + t["desc"] for t in reg)
    if strategy == "code":
        text += SDK_DOC
    return len(text) // 4
