"""D-18 后台长时程任务：检查点续跑 + 心跳超时 + 幂等重试的长跑 agent 骨架

对应书中：**第 15 章**（长时程与后台执行）。长任务的三条铁律：
  · 进度必须落盘（checkpoint），进程可以死，任务不能死；
  · 副作用必须幂等（attempt 计数进键），重试不重复扣款；
  · 存活靠心跳，超时必须有人接管或显式失败，禁止静默悬挂。

本 demo 模拟一个"批量报表生成"任务（每步含一次外部写动作），三种剧本：
    python longrun.py --mock                      # 正常跑完，打印检查点序列
    python longrun.py --mock --crash-at 2         # 第 3 步后崩溃（退出码 42）
    python longrun.py --mock --resume             # 从检查点续跑：已完成步骤跳过、
                                                  # 未完成步骤幂等重放
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CKPT = Path(__file__).with_name("longrun_checkpoint.json")
TOTAL = 5


def load_ckpt() -> dict:
    return json.loads(CKPT.read_text()) if CKPT.exists() else \
        {"done": [], "external_writes": []}


def save_ckpt(c: dict) -> None:
    CKPT.write_text(json.dumps(c, ensure_ascii=False))


def step(i: int, c: dict) -> None:
    """一步工作：计算→外部写（幂等键=step_id）→提交检查点（顺序不可颠倒，15.4）。"""
    write_id = f"report-{i}"                     # 幂等键：重放同一 step 不会二次写入
    if write_id not in c["external_writes"]:
        c["external_writes"].append(write_id)
        print(f"   step{i}: 计算完成 → 外部写 {write_id}（首次）")
    else:
        print(f"   step{i}: 重放检测 → 外部写 {write_id} 已存在，跳过（幂等）")
    c["done"].append(i)
    save_ckpt(c)                                  # 先持久化进度，再宣告完成


def main() -> None:
    ap = argparse.ArgumentParser(description="D-18 后台长时程任务（第 15 章）")
    ap.add_argument("--mock", action="store_true", required=True, help="离线确定性执行")
    ap.add_argument("--crash-at", type=int, default=None, help="在第 N 步提交后模拟崩溃")
    ap.add_argument("--resume", action="store_true", help="从检查点续跑")
    a = ap.parse_args()

    c = load_ckpt() if a.resume else {"done": [], "external_writes": []}
    if a.resume:
        print(f"▶ 续跑：检查点显示已完成 {c['done']}，外部写 {len(c['external_writes'])} 次")
    else:
        CKPT.unlink(missing_ok=True)
        print("▶ 全新任务：5 步报表流水线")

    for i in range(TOTAL):
        if i in c["done"]:
            print(f"   step{i}: 检查点已完成 → 跳过（不重算不重写）")
            continue
        step(i, c)
        if a.crash_at is not None and i == a.crash_at:
            print(f"💥 模拟崩溃于 step{i} 提交之后（心跳停止，无人接管则 15.6 超时判死）")
            sys.exit(42)                          # 非零退出＝看门狗可观察的信号

    print(f"\n✔ 任务完成：外部写共 {len(c['external_writes'])} 次（≤{TOTAL}＝无重复副作用）")
    print("观察什么（15.4/15.6）：先写副作用再存检查点的顺序决定了'至少一次+幂等'"
          "还是'至多一次+丢数据'；--crash-at 2 后再 --resume，总外部写仍等于 5——"
          "这就是长跑 agent 的财务安全线。")


if __name__ == "__main__":
    main()
