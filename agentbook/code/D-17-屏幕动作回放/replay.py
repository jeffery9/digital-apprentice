"""D-17 屏幕动作回放：GUI agent 的"观察→动作→验证"循环与失效注入

对应书中：**第 14 章**（屏幕动作通道）。核心论点：像素级点击不可验证，必须用
**受限动作 schema + 显式结果验证 + 轨迹回放**把 GUI 循环拉回可审计的地面。

本 demo 实现一个脚本化"假操作系统"（窗口状态机），agent 每步动作后系统给出
可检查的状态断言；随后注入两类典型失效——元素漂移（截图过期）与弹窗劫持——
观察没有验证环时它们如何静默通过、有验证环时如何被当场拦下。

用法：
    python replay.py --mock               # 正常三步任务 + 轨迹回放
    python replay.py --mock --inject drift   # 注入元素漂移
    python replay.py --mock --inject popup   # 注入弹窗劫持
"""
from __future__ import annotations

import argparse
import json

SCREEN = {"login_btn": (120, 30), "user_field": (120, 10), "ok": True}


class FakeOS:
    """假操作系统：动作的效果落在结构化状态上，而不是'看起来点对了'。"""

    def __init__(self, inject: str | None) -> None:
        self.state = {"logged_in": False, "form_filled": False}
        self.log: list[dict] = []
        self.inject = inject

    def click(self, target: str) -> dict:
        if self.inject == "drift" and target == "login_btn":
            pos = (999, 999)                     # 页面改版：按钮已不在截图时的坐标
        else:
            pos = SCREEN.get(target, (0, 0))
        effect = {}
        if self.inject == "popup" and len(self.log) == 1:
            effect = {"popup": "恭喜中奖！点确定领取", "expected_effect": None}
        elif target == "user_field":
            self.state["form_filled"] = True
            effect = {"focused": "user_field"}
        elif target == "login_btn":
            if self.state["form_filled"]:
                self.state["logged_in"] = True
            effect = {"attempt_login": True}
        rec = {"action": f"click({target})", "coord": pos, "effect": effect}
        self.log.append(rec)
        return rec

    def verify(self, action_rec: dict, expect: dict) -> bool:
        """显式验证（14.5）：不信任'执行了'，只信任'状态确实变成预期'。"""
        for k, v in expect.items():
            if self.state.get(k) != v and action_rec["effect"].get("popup"):
                return False
        return all(self.state.get(k) == v for k, v in expect.items()) \
            and action_rec["coord"] == SCREEN.get(
                action_rec["action"][6:-1], action_rec["coord"])


def run(inject: str | None, verbose: bool = True) -> tuple[bool, list]:
    os_ = FakeOS(inject)
    steps = [("user_field", {}), ("login_btn", {"logged_in": True})]
    ok = True
    for tgt, expect in steps:
        rec = os_.click(tgt)
        if expect and not os_.verify(rec, expect):
            ok = False
            if verbose:
                print(f"   ✗ 拦截：{rec['action']} 后状态断言失败 {expect}"
                      f"｜effect={rec['effect']} coord={rec['coord']}")
            break
        if verbose:
            print(f"   ✓ {rec['action']} @ {rec['coord']} → 断言通过")
    return ok, os_.log


def main() -> None:
    ap = argparse.ArgumentParser(description="D-17 屏幕动作回放（第 14 章）")
    ap.add_argument("--mock", action="store_true", required=True)
    ap.add_argument("--inject", choices=["none", "drift", "popup"], default="none")
    a = ap.parse_args()
    inj = None if a.inject == "none" else a.inject

    print(f"▶ 基线（无注入）：")
    ok0, log0 = run(None)
    print(f"▶ 注入={a.inject}：")
    ok1, log1 = run(inj)

    print("\n── 轨迹回放（逐步重放 log，供事后审计）──")
    for i, r in enumerate(log1, 1):
        print(f"  step{i}: {json.dumps(r, ensure_ascii=False)}")
    print(f"\n对照：基线成功={ok0}｜注入后成功={ok1}")
    print("观察什么（14.5/14.7）：坐标断言+状态断言让'漂移'和'劫持'从静默错误变成"
          "当场失败；回放日志是取证与回归测试的原料——没有它，GUI agent 无法上线。")


if __name__ == "__main__":
    main()
