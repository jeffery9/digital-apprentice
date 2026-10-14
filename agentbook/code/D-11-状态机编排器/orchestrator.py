#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
D-11　50 行状态机编排器：检查点持久化与崩溃恢复（机制层）（第 10 章）
====================================================================

演示什么：**编排的最小内核长什么样**——节点＝纯函数、边＝显式转移、状态＝唯一
数据通道（书 10.4），加上 10.5 的机制层三问：在哪打点（节点提交后）、打什么
（状态＋动作回执＋模型输出快照）、怎么恢复（加载→校验 schema 版本→从下一节点继续）。

与 D-18 的分工（刻意做成同一状态机的两个观察面）：**本 demo 演示机制，D-18 演示治理**。

用法：
    python orchestrator.py --mock                     # 正常路径：跑到审批点挂起（设计如此）
    python orchestrator.py --mock --human-reply 同意   # 批复先到：五节点一次跑完
    python orchestrator.py --mock --crash-at 2        # 在第 3 个节点提交后强杀（模拟 SIGKILL）
    python orchestrator.py --mock --resume            # 从检查点续跑（先重跑上面两条）
    python orchestrator.py --no-checkpoint --mock     # 对照组：无检查点，崩溃即从头重跑

观察什么（对应书中 10.8）：
    ① 节点提交序列与检查点体积增长曲线——状态里只存引用，不存聊天历史
    ② kill→resume 对照：续跑路径的模型调用次数 = 剩余节点数，而非全部节点数
       （crash-at 2 现场恢复时 approve 是纯等待节点、send 命中幂等跳过，
        新增调用为 0——把 --crash-at 0 再试一次，就能看到"剩余节点各调一次模型"的形状）
    ③ 发送节点的幂等键防止二次发送（外部动作回执在检查点里，重放安全直接可见）
    ④ 审批节点等待期间进程零消耗（return 而非 sleep——进程可以为等待而死）

⚠️ mock 环境不调用任何模型：分类/起草为确定性桩，审批为脚本化"人的回复"。
只演示机制形状（QWEN.md 铁律三）。真实系统把桩换成 llm.py 适配层调用即可。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from llm import profile, system, user  # noqa: E402

STATE_SCHEMA_VERSION = "d11/v1"
CKPT_PATH_DEFAULT = "checkpoint.json"


# ─────────────────────────────────────────────────────────────
# 状态与节点（书 10.4 三约束的最小兑现）
# ─────────────────────────────────────────────────────────────
@dataclass
class State:
    ticket: str
    node: str = "start"
    done: list[str] = field(default_factory=list)
    category: str | None = None
    findings: str | None = None          # 只存结论引用，不存过程历史（10.4）
    draft: str | None = None
    pending: dict[str, Any] | None = None    # HITL：醒来从这里继续（10.6）
    human_reply: str | None = None
    sent_idempotency_key: str | None = None
    model_calls: int = 0                 # 探针：模型调用计数
    external_actions: int = 0            # 探针：外部动作计数（副作用必须可数）

    def as_dict(self) -> dict[str, Any]:
        return {"schema": STATE_SCHEMA_VERSION, **vars(self)}

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "State":
        if d.get("schema") != STATE_SCHEMA_VERSION:   # 恢复第一步：校验版本（10.5）
            raise ValueError(f"检查点 schema 版本不符：{d.get('schema')!r} ≠ "
                             f"{STATE_SCHEMA_VERSION!r}——迁移脚本先行，不许带病恢复")
        return State(**{k: v for k, v in d.items() if k != "schema"})


NodeFn = Callable[[State], tuple[str, str]]      # 返回 (结果摘要, 下一节点名)


def fake_model_call(st: State, prompt: str) -> str:
    """模型调用替身：只计数，让'续跑调用次数=剩余节点数'可被肉眼核对。"""
    st.model_calls += 1
    _ = [system("你是编排器节点内的执行模型。"), user(prompt)]
    profile("mock-small-1")                      # 快照钉扎纪律同样适用于节点内调用
    return ""


# ── 五个节点：分类 → 检索 → 起草 → 审批（HITL）→ 发送 ──────────
def n_classify(st: State) -> tuple[str, str]:
    fake_model_call(st, f"给工单 {st.ticket} 分类")
    st.category = "物流延误/赔付诉求"
    return st.category, "retrieve"


def n_retrieve(st: State) -> tuple[str, str]:
    fake_model_call(st, f"检索与「{st.category}」相关的处理标准")
    st.findings = "kb:延误阶梯赔付口径；history:近90天2次投诉（详见外存引用，不入状态）"
    return "检索完成", "draft"


def n_draft(st: State) -> tuple[str, str]:
    fake_model_call(st, "基于结论起草处理建议")
    st.draft = f"处理建议草稿（{st.category}）：核对时效条款→按阶梯赔付口径答复。"
    return "草稿就绪", "approve"


def n_approve(st: State) -> tuple[str, str]:
    """HITL 作为一等公民：等待不是阻塞，是"进程可以为等待而死"（10.6）。"""
    if st.human_reply is None:
        if st.pending is None:
            st.pending = {"awaiting": "人工审批", "node": "approve",
                          "asked_at": time.time()}
            return ("已挂起：等待人工审批（检查点含 pending 字段，进程退出零消耗）",
                    "__suspend__")
        return ("仍在等待（不应发生：恢复时先注入人的回复）", "__suspend__")
    return f"人批复：{st.human_reply}", "send"


def n_send(st: State) -> tuple[str, str]:
    """不可逆动作 + 幂等键：重放安全性在这里被验证（10.5 重放契约）。"""
    key = f"reply:{st.ticket}"
    if st.sent_idempotency_key == key:
        return "幂等命中：此前已发送，跳过（防止二次发送）", "end"
    st.sent_idempotency_key = key
    st.external_actions += 1                     # 外部动作必须可数（探针③的数据源）
    return "答复已发送给客服坐席（虚构动作）", "end"


NODES: dict[str, NodeFn] = {"classify": n_classify, "retrieve": n_retrieve,
                            "draft": n_draft, "approve": n_approve, "send": n_send}


# ─────────────────────────────────────────────────────────────
# 引擎：约 50 行的内核（书中 10.7 的骨干即此段）
# ─────────────────────────────────────────────────────────────
def save_ckpt(st: State, path: str) -> None:
    tmp = path + ".tmp"
    Path(tmp).write_text(json.dumps(st.as_dict(), ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)          # 原子写：半截检查点比没有检查点更糟（10.5）


def run(st: State, *, path: str, crash_after: int | None = None,
        checkpoint: bool = True, verbose: bool = True) -> State:
    executed = 0
    while st.node != "end":
        fn = NODES[st.node]
        outcome, nxt = fn(st)
        if verbose:
            print(f"  [{st.node:>8}] {outcome}")
        st.done.append(st.node)
        executed += 1
        if nxt == "__suspend__":                  # 挂起：把"等待"写进检查点后退出
            st.node = "approve"
            if checkpoint:
                save_ckpt(st, path)
            return st
        st.node = nxt
        if checkpoint:
            save_ckpt(st, path)                   # 打点时机：节点提交后，不是节点内部
        if crash_after is not None and executed > crash_after:
            if verbose:
                print(f"\n💥 模拟 SIGKILL：已在第 {executed} 个节点提交后强杀。"
                      f"重启方式：python orchestrator.py --mock --resume")
            raise SystemExit(3)                   # 非零退出码＝崩溃现场留真
    return st


def main() -> int:
    ap = argparse.ArgumentParser(description="D-11 状态机编排器（第 10 章）")
    ap.add_argument("--mock", action="store_true", help="离线模式（本 demo 全程离线）")
    ap.add_argument("--resume", action="store_true", help="从检查点恢复续跑")
    ap.add_argument("--crash-at", type=int, default=None, metavar="N",
                    help="在第 N 个节点提交后模拟强杀")
    ap.add_argument("--no-checkpoint", action="store_true",
                    help="关闭检查点：对照组，看'无检查点重跑'付多少账")
    ap.add_argument("--ticket", default="SO-20261013-0087")
    ap.add_argument("--ckpt", default=CKPT_PATH_DEFAULT)
    ap.add_argument("--human-reply", default=None, metavar="TEXT",
                    help="预置人工批复文本（模拟批复先于执行到达，用于一次跑完全程）")
    a = ap.parse_args()

    use_ckpt = not a.no_checkpoint
    if a.resume:
        if not use_ckpt or not Path(a.ckpt).exists():
            print("❌ 无检查点可恢复（--resume 需要检查点在位）。这正是 10.5 的第一课："
                  "没演练过恢复的检查点等于没有检查点。")
            return 1
        st = State.from_dict(json.loads(Path(a.ckpt).read_text(encoding="utf-8")))
        before = dict(model_calls=st.model_calls, actions=st.external_actions)
        if st.pending is not None:                # 恢复路径：pending → 注入人的回复
            st.human_reply = a.human_reply or "同意按阶梯赔付口径答复（脚本化模拟）"
            st.pending = None
            print("↩️  恢复：发现 pending（等待人工审批），注入批复后续跑")
        print(f"从检查点续跑：当前节点={st.node}｜已完成={st.done}\n")
        st = run(st, path=a.ckpt, checkpoint=True)
        print(f"\n✅ 续跑完成｜本次新增模型调用={st.model_calls - before['model_calls']}"
              f"（=剩余节点数，不是全部节点数）｜累计={st.model_calls}")
        print(f"   外部动作累计={st.external_actions}（幂等键防止了二次发送）")
        return 0

    st = State(ticket=a.ticket, node="classify")
    if a.human_reply:                             # 演示"人批复到达"的自动化入口
        st.human_reply = a.human_reply
    print(f"正常路径：五节点工单流程（检查点{'开' if use_ckpt else '关'}）")
    try:
        st = run(st, path=a.ckpt, crash_after=a.crash_at, checkpoint=use_ckpt)
    except SystemExit:
        return 3                                   # 崩溃场景：留给 --resume 接棒
    if st.node != "end":
        print(f"\n⏸️  流程在「{st.node}」挂起等待人工审批——这不是失败，是设计："
              f"进程可以为等待而死（10.6）。批复到达后续跑：\n"
              f"    python orchestrator.py --mock --resume")
        print(f"   本次运行模型调用={st.model_calls}｜外部动作={st.external_actions}"
              f"｜挂起期间零消耗（没有 sleep，没有占窗口的循环）")
        return 0
    print(f"\n✅ 完成｜模型调用={st.model_calls}｜外部动作={st.external_actions}"
          f"｜检查点体积={Path(a.ckpt).stat().st_size if use_ckpt else 0} 字节"
          f"（状态只存引用——体积不随会话历史增长）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
