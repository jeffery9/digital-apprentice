"""D-13 拓扑对比：Handoff（转交）vs Supervisor（监督者）多智能体拓扑

对应书中：**第 12 章**（多智能体协作拓扑）。两种根本形态：
  · Handoff    —— 控制权整体转移，下游 agent 接管会话，上游退场（无中心）；
  · Supervisor —— 中心调度者保留控制权，按注册表逐个派发子任务再汇总（有中心）。

本 demo 用同一批 5 个客服请求在两种拓扑下各跑一遍，对照：
  控制权落点、调用次数、失败半径、可观测性钩子位置。

用法：
    python topology.py --mock          # 离线脚本替身，零依赖零网络
真实模式需设置 LLM_ENDPOINT / LLM_MODEL / LLM_API_KEY（走 ../llm.py 薄适配层）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from llm import system, user            # noqa: E402
from _mockscript import ScriptedLLM     # noqa: E402

REQUESTS = ["我要退款，订单 8871", "帮我开一张发票", "你们的会员政策是什么",
            "投诉快递员态度", "退款进度查一下"]


class Agent:
    def __init__(self, name: str, llm, task: str) -> None:
        self.name, self.llm, self.task = name, llm, task

    def handle(self, text: str) -> str:
        r = self.llm.chat([system(f"[task:{self.task}]"), user(text)])
        return r.data["intent"] if "intent" in (r.data or {}) else "answered"


def run_handoff(llm, texts):
    """无中心：front-desk 决定转交后，控制权整体移交 billing-agent。"""
    trace, hops = [], 0
    for t in texts:
        d = llm.chat([system("[task:handoff_decide]"), user(t)]).data
        hops += 1
        if d["action"] == "handoff":
            hops += 1                      # 接管后再有一次处理调用
            trace.append(f"{t[:12]}… → handoff→{d['to']}")
        else:
            trace.append(f"{t[:12]}… → answer(自答)")
    return trace, hops


def run_supervisor(llm, texts):
    """有中心：supervisor 每轮都收回控制权，派发→汇总，agent 之间不直接对话。"""
    trace, dispatches = [], 0
    for t in texts:
        d = llm.chat([system("[task:supervisor_route]"), user(t)]).data
        dispatches += 1
        target = d["next_agent"]
        if target != "finish":
            dispatches += 1                # 派发一次 + 结果回 supervisor 汇总一次
            trace.append(f"{t[:12]}… → dispatch({target}) → 汇总")
        else:
            trace.append(f"{t[:12]}… → finish(supervisor 自答)")
    return trace, dispatches


def main() -> None:
    ap = argparse.ArgumentParser(description="D-13 拓扑对比（第 12 章）")
    ap.add_argument("--mock", action="store_true", help="离线脚本替身（推荐）")
    a = ap.parse_args()
    if not a.mock:
        sys.exit("本 demo 的对照结论只在脚本环境下成立；请直接使用 --mock。")
    llm = ScriptedLLM()
    print("── Handoff（无中心，控制权转移）──")
    th, hh = run_handoff(llm, REQUESTS)
    for x in th:
        print("  ", x)
    print("── Supervisor（有中心，控制权回收）──")
    ts, dd = run_supervisor(llm, REQUESTS)
    for x in ts:
        print("  ", x)
    print(f"\n对照：handoff 调用={hh}（转交即退场，失败半径大）｜"
          f"supervisor 派发={dd}（中心逐轮回收，可观测钩子在调度点）")
    print("观察什么（12.6）：同样的请求集，supervisor 多出的调用差＝'汇总税'；"
          "handoff 省下的调用＝'失控风险的对价'。")


if __name__ == "__main__":
    main()
