"""D-04　最小模型网关：快照钉扎 ＋ 故障转移 ＋ 熔断 ＋ 按路由遥测

对应书中：**第 4 章 4.4（七个职责）、4.7（调用点级路由）、4.8（降级矩阵）**；
模式卡「模型网关」「故障转移与熔断」「能力画像路由」。

约 100 行核心代码实现网关七个职责里最要紧的六个：入口与预算闸、语义缓存、
策略层（合规分区＝候选集过滤器）、按调用点路由、快照钉扎、熔断与故障转移、
按路由遥测。**被后续 demo 复用**：第 6 章预算护栏、第 18 章轨迹采样都挂在遥测点上。

用法：
    python gateway.py --mock --calls 60
    python gateway.py --mock --calls 60 --failure-rate 0.5   # 看熔断与故障转移
    python gateway.py --mock --calls 60 --tier regulated      # 看合规分区收窄候选集
    python gateway.py --mock --calls 60 --budget 500          # 看预算闸
    python gateway.py --mock --calls 60 --dump-telemetry out.jsonl
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from llm import LLMError, MockLLM, get_llm, profile, user  # noqa: E402

PINNED = {"flagship": "mock-flagship-1", "vision": "mock-vision-1", "small": "mock-small-1"}
Tier = Literal["public", "internal", "regulated"]
RESIDENCY_OK: dict[Tier, tuple[str, ...]] = {     # 职责③：合规分区（4.10）
    "public": ("cloud-cn", "cloud-us", "on-prem"),
    "internal": ("cloud-cn", "on-prem"),
    "regulated": ("on-prem",),
}


@dataclass(frozen=True)
class RouteRule:
    """调用点级路由表的一行（第 4 章表 4-4 的最小实现）。"""
    call_point: str
    candidates: tuple[str, ...]        # 按档位排序的候选链（职责④）
    cacheable: bool = True             # judge 不缓存：它的输入含被判定内容
    degrade_to: str = "模板兜底＋转人工"


ROUTING_TABLE = {r.call_point: r for r in (
    RouteRule("plan", ("flagship", "vision")),
    RouteRule("tool_args", ("vision", "flagship", "small")),
    RouteRule("summarize", ("vision", "small")),
    RouteRule("reply", ("flagship", "vision"), degrade_to="模板兜底"),
    RouteRule("judge", ("flagship",), cacheable=False, degrade_to="转人工"),
)}


class Breaker:
    """职责⑥之一：熔断三态（关闭 → 打开 → 半开），阈值按模型分别计。"""

    def __init__(self, threshold: int = 3, cooldown: float = 0.05) -> None:
        self.threshold, self.cooldown, self.opens = threshold, cooldown, 0
        self.fails: dict[str, int] = {}
        self.opened_at: dict[str, float] = {}

    def is_open(self, model: str) -> bool:
        if model not in self.opened_at:
            return False
        return time.monotonic() - self.opened_at[model] < self.cooldown   # 冷却后转半开

    def on_failure(self, model: str) -> None:
        self.fails[model] = self.fails.get(model, 0) + 1
        if self.fails[model] >= self.threshold and model not in self.opened_at:
            self.opened_at[model] = time.monotonic()
            self.opens += 1

    def on_success(self, model: str) -> None:
        self.fails[model] = 0
        self.opened_at.pop(model, None)


class FlakyBackend:
    """把 MockLLM 包一层可注入故障的外壳；真实环境的故障以 LLMError 形式到来。"""

    def __init__(self, mock: bool, failure_rate: float, seed: int) -> None:
        self.mock, self.failure_rate = mock, failure_rate
        self._rng, self._pool = random.Random(seed), {}

    def chat(self, model_id: str, text: str) -> Any:
        if self._rng.random() < self.failure_rate:
            raise LLMError(f"上游 {model_id} 限流/超时（注入故障）", status=429)
        if model_id not in self._pool:
            self._pool[model_id] = MockLLM(model_id) if self.mock else get_llm(model_id)
        return self._pool[model_id].chat([user(text)])


@dataclass
class Gateway:
    backend: FlakyBackend
    breaker: Breaker = field(default_factory=Breaker)
    budget: int = 10**9
    spent: dict[str, int] = field(default_factory=dict)
    cache: dict[str, Any] = field(default_factory=dict)
    telemetry: list[dict[str, Any]] = field(default_factory=list)

    def _candidates(self, rule: RouteRule, tier: Tier) -> list[str]:
        allowed = RESIDENCY_OK[tier]
        # 职责⑤：别名在这里被钉成固定快照 ID，调用链里永远不出现 "latest"
        return [PINNED[a] for a in rule.candidates
                if set(profile(PINNED[a]).data_residency) & set(allowed)]

    def call(self, call_point: str, text: str, *, tenant: str = "default",
             tier: Tier = "public") -> dict[str, Any]:
        rule = ROUTING_TABLE[call_point]
        rec: dict[str, Any] = {"call_point": call_point, "tenant": tenant, "tier": tier,
                               "cached": False, "degraded": False, "model": None}
        self.telemetry.append(rec)                       # 职责⑦：每次调用一条遥测
        if self.spent.get(tenant, 0) >= self.budget:     # 职责③：预算闸在入口
            rec.update(degraded=True, reason="budget_exceeded", action=rule.degrade_to)
            return rec
        key = f"{call_point}:{tier}:{hash(text)}"
        if rule.cacheable and key in self.cache:         # 职责②：缓存在路由之前
            rec.update(cached=True, **self.cache[key])
            return rec
        cands = self._candidates(rule, tier)             # 职责③④：分区 → 路由
        rec["candidates"] = cands
        for model_id in cands:
            if self.breaker.is_open(model_id):           # 熔断打开：不打扰上游
                continue
            try:
                resp = self.backend.chat(model_id, text)
            except LLMError as exc:
                self.breaker.on_failure(model_id)
                rec.setdefault("failures", []).append(f"{model_id}:{exc.status}")
                continue                                 # 职责⑥：故障转移候选链
            self.breaker.on_success(model_id)
            tokens = resp.usage.total_tokens
            self.spent[tenant] = self.spent.get(tenant, 0) + tokens
            body = {"model": model_id, "tokens": tokens}
            if rule.cacheable:
                self.cache[key] = body
            rec.update(**body)
            return rec
        # 候选集为空（合规过滤后无可用模型）或全部熔断/失败 → 走降级矩阵（表 4-5）
        rec.update(degraded=True, action=rule.degrade_to,
                   reason="no_candidate" if not cands else "all_failed")
        return rec


def summarize(gw: Gateway, args: argparse.Namespace) -> None:
    print(f"calls={args.calls}｜failure_rate={args.failure_rate}｜tier={args.tier}"
          f"｜budget={args.budget}｜mock={args.mock}\n")
    print(f"{'调用点':<12}{'次数':>5}{'缓存':>6}{'降级':>6}{'token':>8}  模型分布")
    by_point: dict[str, list[dict[str, Any]]] = {}
    for rec in gw.telemetry:
        by_point.setdefault(rec["call_point"], []).append(rec)
    for cp, recs in by_point.items():
        models: dict[str, int] = {}
        for r in recs:
            if r.get("model"):
                models[r["model"]] = models.get(r["model"], 0) + 1
        print(f"{cp:<12}{len(recs):>5}{sum(r['cached'] for r in recs):>6}"
              f"{sum(r['degraded'] for r in recs):>6}"
              f"{sum(r.get('tokens', 0) for r in recs):>8}  {models}")
    print(f"\n熔断打开 {gw.breaker.opens} 次｜租户已用 token {gw.spent.get(args.tenant, 0)}"
          f"／{args.budget}｜遥测 {len(gw.telemetry)} 条")


def main() -> None:
    ap = argparse.ArgumentParser(description="D-04 最小模型网关")
    ap.add_argument("--mock", action="store_true", help="离线模式（无需网络与密钥）")
    ap.add_argument("--calls", type=int, default=60)
    ap.add_argument("--failure-rate", type=float, default=0.0, help="注入的上游故障率")
    ap.add_argument("--budget", type=int, default=10**9, help="租户 token 预算")
    ap.add_argument("--tenant", default="default")
    ap.add_argument("--tier", choices=["public", "internal", "regulated"], default="public")
    ap.add_argument("--seed", type=int, default=20261013)
    ap.add_argument("--dump-telemetry", type=Path, default=None, help="遥测落盘为 JSONL")
    args = ap.parse_args()

    gw = Gateway(FlakyBackend(args.mock, args.failure_rate, args.seed), budget=args.budget)
    texts = ["包裹三天没到，客户催了两次", "发票金额与合同不符，需红冲重开",
             "设备异响，客户怀疑轴承问题", "询问新版本是否支持批量导入"]
    points = list(ROUTING_TABLE)
    for i in range(args.calls):
        gw.call(points[i % len(points)], texts[i % len(texts)],
                tenant=args.tenant, tier=args.tier)
    summarize(gw, args)
    print("观察什么：①failure_rate 提高后模型分布向候选链下游迁移、熔断打开次数上升，"
          "而 judge 只有一个候选，于是大面积降级——**没有候选链的调用点没有韧性**；")
    print("         ②tier=regulated 时候选集只剩本地部署模型，plan/reply/judge 因无合规模型"
          "而显式降级（分区在路由之前生效，不可能被绕过）；③budget 调小后出现 budget_exceeded。")
    if args.dump_telemetry:
        args.dump_telemetry.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in gw.telemetry) + "\n",
            encoding="utf-8")
        print(f"\n遥测已写入 {args.dump_telemetry}（第 18 章轨迹评估、第 19 章成本归因的数据源）")


if __name__ == "__main__":
    main()
