"""D-03　三类路由策略对比：规则 / 语义 / 级联（成本-质量-延迟三维）

对应书中：**第 4 章 4.5（四策略与分层组合、级联经济性算式）、4.7（调用点级路由）**；
模式卡「语义路由」「级联升级」「能力画像路由」。

⚠️ 读数前必看：**本 demo 的质量、成本、延迟都是被注入的模型参数，不是任何真实
模型的测量结果。**它演示的是三件事：①策略之间的相对关系；②第 4 章 4.5 的级联
算式 `1 + u × 10` 是否被实测复现；③第 4 章 4.7 的"只优化第一跳"到底损失多少。
真实数字请用你的模型池与评估集重跑（那时质量列换成评估集分数）。

用法：
    python router.py --mock --tasks 200
    python router.py --mock --tasks 200 --strategy cascade --escalate-threshold 0.7
    python router.py --mock --tasks 200 --first-hop-only      # 只优化第一跳 vs 全调用点
    python router.py --mock --tasks 200 --hard-ratio 0.6      # 改变难度分布，看 u 与成本
"""
from __future__ import annotations

import argparse
import math
import random
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gateway import ROUTING_TABLE  # noqa: E402  复用 D-04 的调用点定义（4.7 的实验对象）

Tier = Literal["small", "mid", "flagship"]


@dataclass(frozen=True)
class TierSpec:
    cost: float          # 相对成本单位：小模型＝1，旗舰＝10（与 4.5 的算式同口径）
    latency_ms: int
    acc_easy: float      # 简单任务上的正确率（注入参数）
    acc_hard: float      # 困难任务上的正确率（注入参数）


TIERS: dict[Tier, TierSpec] = {
    "small": TierSpec(1.0, 200, 0.95, 0.35),
    "mid": TierSpec(3.0, 500, 0.98, 0.65),
    "flagship": TierSpec(10.0, 1500, 0.99, 0.92),
}
HARD_PROTO = "合同 违约 索赔 赔偿 发票 红冲 纠纷 法律 金额 逾期 责任 条款"
EASY_PROTO = "查询 进度 物流 状态 是否 支持 怎么 如何 版本 功能 咨询 导出"
# 4.7 的调用点 token 占比（智航实测形状）：只优化第一跳＝只优化 plan 那 15%
CALL_POINT_SHARES = {"plan": 0.15, "tool_args": 0.20, "summarize": 0.25,
                     "reply": 0.30, "judge": 0.10}


@dataclass
class Task:
    text: str
    hard: bool


def make_tasks(n: int, hard_ratio: float, seed: int) -> list[Task]:
    rng = random.Random(seed)
    easy = ["客户询问物流进度，包裹单号 SF{i}", "新版本是否支持批量导入历史数据",
            "怎么导出上月的工单统计报表", "咨询开通账号的流程与所需材料"]
    hard_pool = ["客户主张第 {i} 条违约条款并要求赔偿，涉及金额 {i} 万元",
                 "发票金额与合同不符，财务要求本周红冲重开并说明责任归属",
                 "设备故障导致停产，客户提出索赔并要求法务评估逾期责任",
                 "双方对条款解释存在纠纷，需判断赔偿责任与金额上限"]
    out: list[Task] = []
    for i in range(n):
        is_hard = rng.random() < hard_ratio
        pool = hard_pool if is_hard else easy
        out.append(Task(rng.choice(pool).format(i=rng.randint(1, 99)), is_hard))
    return out


def bigrams(s: str) -> set[str]:
    return {s[i:i + 2] for i in range(len(s) - 1)}


def cosine(a: str, b: str) -> float:
    """无第三方依赖的语义相似度：字符二元组的 Jaccard 式余弦（生产用真正的嵌入模型）。"""
    ba, bb = bigrams(a), bigrams(b)
    inter = len(ba & bb)
    return inter / math.sqrt(len(ba) * len(bb)) if inter else 0.0


def route_rule(t: Task) -> Tier:
    kw = ("合同", "违约", "索赔", "赔偿", "发票", "红冲", "纠纷", "责任")
    return "flagship" if any(k in t.text for k in kw) else "small"


def make_semantic(threshold: float) -> Callable[[Task], Tier]:
    def route(t: Task) -> Tier:
        score = cosine(t.text, HARD_PROTO) - cosine(t.text, EASY_PROTO)
        return "flagship" if score >= threshold else "small"
    return route


@dataclass
class Acc:
    cost: float = 0.0
    correct: int = 0
    latency: float = 0.0
    escalated: int = 0
    n: int = 0

    def add(self, tier: Tier, task: Task, *, escalated: bool = False,
            extra_cost: float = 0.0, extra_latency: float = 0.0) -> None:
        """质量用"文本＋档位"的确定性散列抽样，保证同种子下逐次可复现。"""
        spec = TIERS[tier]
        p = spec.acc_hard if task.hard else spec.acc_easy
        draw = random.Random(zlib.crc32(f"{task.text}|{tier}".encode())).random()
        self.cost += spec.cost + extra_cost
        self.latency += spec.latency_ms + extra_latency
        self.correct += int(draw < p)
        self.escalated += int(escalated)
        self.n += 1

    @property
    def quality(self) -> float:
        return self.correct / self.n if self.n else 0.0


def run_cascade(tasks: list[Task], rng: random.Random, *, judge_recall: float,
                judge_fpr: float) -> Acc:
    """级联：先小模型，判别器不通过再升级到旗舰（升级的请求双跑，两家都付）。"""
    acc = Acc()
    for t in tasks:
        p = judge_recall if t.hard else judge_fpr
        if rng.random() < p:
            # 双跑：升级的请求两家都付钱、两段延迟都吃（4.5 算式里 u × 10 的那一项）
            acc.add("flagship", t, escalated=True, extra_cost=TIERS["small"].cost,
                    extra_latency=TIERS["small"].latency_ms)
        else:
            acc.add("small", t)
    return acc


def first_hop_experiment(strategy: Callable[[Task], Tier], tasks: list[Task]) -> None:
    """4.7 的算式实测：只优化第一跳 vs 全调用点路由。"""
    base = sum(TIERS["flagship"].cost * s for s in CALL_POINT_SHARES.values())
    saved_first = sum((TIERS["flagship"].cost - TIERS[strategy(t)].cost) * CALL_POINT_SHARES["plan"]
                      for t in tasks) / len(tasks)
    saved_all = sum((TIERS["flagship"].cost - TIERS[strategy(t)].cost) * sum(CALL_POINT_SHARES.values())
                    for t in tasks) / len(tasks)
    print("\n【4.7 实验】调用点 token 占比：" +
          "、".join(f"{k} {v:.0%}" for k, v in CALL_POINT_SHARES.items()))
    print(f"  全部调用点都用旗舰的基准成本 ＝ {base:.2f} 单位／任务")
    print(f"  只路由第一跳（plan）  → 节省 {saved_first:.2f} 单位（{saved_first / base:.1%}）")
    print(f"  路由全部调用点        → 节省 {saved_all:.2f} 单位（{saved_all / base:.1%}）")
    print(f"  调用点（来自 D-04 的路由表）：{list(ROUTING_TABLE)}")


def main() -> None:
    ap = argparse.ArgumentParser(description="D-03 三类路由策略对比")
    ap.add_argument("--mock", action="store_true", help="离线模式（本 demo 的质量/成本模型本身即离线）")
    ap.add_argument("--tasks", type=int, default=200)
    ap.add_argument("--hard-ratio", type=float, default=0.3, help="困难任务占比（决定级联的 u）")
    ap.add_argument("--strategy", choices=["all", "rule", "semantic", "cascade"], default="all")
    ap.add_argument("--semantic-threshold", type=float, default=0.05)
    ap.add_argument("--judge-recall", type=float, default=0.8, help="判别器抓住困难任务的比例")
    ap.add_argument("--judge-fpr", type=float, default=0.1, help="判别器误升级简单任务的比例")
    ap.add_argument("--first-hop-only", action="store_true", help="跑 4.7 的调用点实验")
    ap.add_argument("--seed", type=int, default=20261014)
    args = ap.parse_args()

    tasks = make_tasks(args.tasks, args.hard_ratio, args.seed)
    rng = random.Random(args.seed)
    strategies: dict[str, tuple[Callable[[Task], Tier], str]] = {
        "rule": (route_rule, "规则：关键词命中即旗舰"),
        "semantic": (make_semantic(args.semantic_threshold), "语义：与困难/简单原型的相似度差"),
    }
    print(f"tasks={args.tasks}｜hard_ratio={args.hard_ratio}｜judge_recall={args.judge_recall}"
          f"｜judge_fpr={args.judge_fpr}｜成本口径：small=1, mid=3, flagship=10（相对单位）\n")
    print(f"{'策略':<34}{'成本/任务':>10}{'质量':>8}{'延迟ms':>9}{'升级率u':>9}")

    rows: list[tuple[str, Acc]] = []
    for name, tier in (("全旗舰（基准）", "flagship"), ("全小模型（基准）", "small")):
        a = Acc()
        for t in tasks:
            a.add(tier, t)          # type: ignore[arg-type]
        rows.append((name, a))
    want = args.strategy
    for key, (fn, label) in strategies.items():
        if want not in ("all", key):
            continue
        a = Acc()
        for t in tasks:
            a.add(fn(t), t)
        rows.append((label, a))
    if want in ("all", "cascade"):
        a = run_cascade(tasks, rng, judge_recall=args.judge_recall, judge_fpr=args.judge_fpr)
        u = a.escalated / a.n
        rows.append((f"级联：小模型→判别器→旗舰（u={u:.2f}）", a))
        print(f"\n【4.5 算式核对】实测 u = {u:.2f}；1 + u × 10 = {1 + u * 10:.2f} 单位；"
              f"级联实测成本 = {a.cost / a.n:.2f} 单位／任务 → 算式被复现"
              f"（口径：small=1、flagship=10，升级请求双跑）")
    for name, a in rows:
        print(f"{name:<34}{a.cost / a.n:>10.2f}{a.quality:>8.1%}"
              f"{a.latency / a.n:>9.0f}{(a.escalated / a.n if a.escalated else 0):>9.2f}")
    print("\n观察什么：①级联的成本随 u 线性上升，u>0.5 时不如用规则把难任务直接分给旗舰；"
          "②语义路由的质量-成本点落在规则与全旗舰之间，阈值决定它在前沿上的位置；")
    print("         ③改变 --hard-ratio 会同时移动 u 与质量，这就是\"难度分布决定策略选择\"；"
          "④质量列在真实环境应替换为你的评估集分数（第 18 章）。")
    if args.first_hop_only:
        first_hop_experiment(strategies["rule"][0], tasks)


if __name__ == "__main__":
    main()
