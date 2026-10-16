"""D-04／基座 `code/gateway.py`　最小模型网关：模型池 ＋ LLM 路由 ＋ 快照钉扎 ＋ 故障转移

对应书中：**第 4 章 4.3（模型池与准入）、4.4（七个职责）、4.7（调用点级路由）、
4.8（降级矩阵）**；模式卡「模型网关」「故障转移与熔断」「能力画像路由」。

作者拍板（2026-10-15，M2 复核意见）：**使用 LLM 接入网关，网关要提供 LLM 路由
与模型池功能**。据此本文件升级为 M2 交付判据中的 `gateway.py` 基座——从 D-04
目录提升为 `code/gateway.py`（与 `llm.py` 并列），供全书 demo 统一复用；本目录
只保留 README 与向后兼容 shim。网关三块核心能力：

  · **模型池（pool）**：注册表式治理——登记快照 ID 与别名映射、准入校验（未登记
    即拒，写进代码而不是文档，4.2）、运行时画像指标（4.3）。
  · **LLM 路由（routing）**：规则／调用点级路由表（默认）、语义路由钩子、级联
    升级钩子；合规分区作为候选集过滤器在路由之前生效（4.10）。
  · **其余职责**：预算闸、缓存前置、快照钉扎、熔断三态与故障转移、按路由遥测。

用法：
    python gateway.py --mock --calls 60
    python gateway.py --mock --calls 60 --failure-rate 0.5   # 看熔断与故障转移
    python gateway.py --mock --calls 60 --tier regulated      # 看合规分区收窄候选集
    python gateway.py --mock --calls 60 --budget 500          # 看预算闸
    python gateway.py --mock --calls 60 --dump-telemetry out.jsonl
    python gateway.py --mock --show-pool                      # 看模型池准入状态与画像
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))   # 与 llm.py 同级（基座并列）
from llm import (  # noqa: E402
    LLM, LLMError, Message, Response, ToolSpec, Usage, get_llm, profile, user,
)

# 网关内部档位别名 → 模型池快照 ID。职责⑤：别名只在注册表里出现一次，
# 调用链里永远不出现 "latest" 一类的浮动别名（4.9）。
PINNED = {"flagship": "mock-flagship-1", "vision": "mock-vision-1", "small": "mock-small-1"}
SNAPSHOT_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*-\d+$")   # 快照 ID 形状：以序号结尾
Tier = Literal["public", "internal", "regulated"]
RESIDENCY_OK: dict[Tier, tuple[str, ...]] = {     # 职责③：合规分区（4.10）
    "public": ("cloud-cn", "cloud-us", "on-prem"),
    "internal": ("cloud-cn", "on-prem"),
    "regulated": ("on-prem",),
}


# ══════════════════════════════════════════════════════════════
# 模型池（authoritative model pool，4.2／4.3）
# ══════════════════════════════════════════════════════════════
@dataclass
class PoolEntry:
    """模型池的一行：一个被治理的模型。准入＝登记别名＋快照＋通过画像校验。"""
    alias: str                         # 网关内部档位名（唯一允许出现在路由表里的形态）
    snapshot_id: str                   # 钉扎的具体快照，禁止 latest 类别名
    status: Literal["active", "deprecated", "shadow"] = "active"
    calls: int = 0                     # 运行时指标：路由读画像、遥测写回这里（图 4-1 下方回路）
    failures: int = 0
    tokens: int = 0

    @property
    def fail_rate(self) -> float:
        return self.failures / self.calls if self.calls else 0.0


class ModelPool:
    """模型池注册表。两条纪律都写成异常而不写成规范（4.2）：
    ①未登记的模型不能被调用；②非快照形状的 ID 不能入池。"""

    def __init__(self) -> None:
        self._entries: dict[str, PoolEntry] = {}

    def register(self, alias: str, snapshot_id: str, *,
                 status: Literal["active", "deprecated", "shadow"] = "active") -> PoolEntry:
        if not SNAPSHOT_RE.match(snapshot_id):
            raise ValueError(
                f"{snapshot_id!r} 不是快照 ID（须以序号结尾，如 mock-small-1）。"
                "浮动别名的行为会在你不觉时漂移，评估结论随之作废（4.9）。")
        profile(snapshot_id)                    # 准入第一道门：无画像即拒（llm.py 会抛 KeyError）
        e = PoolEntry(alias, snapshot_id, status)
        self._entries[alias] = e
        return e

    def entry(self, alias: str) -> PoolEntry:
        try:
            return self._entries[alias]
        except KeyError:
            raise KeyError(f"档位 {alias!r} 不在模型池中。现有：{sorted(self._entries)}") from None

    def active_snapshots(self) -> list[str]:
        return [e.snapshot_id for e in self._entries.values() if e.status == "active"]

    def usable_for(self, snapshot_id: str, tier: Tier) -> bool:
        """驻留过滤之外再加弃用闸：deprecated 的快照只许存量迁移，不许新流量（4.11）。"""
        e = next((x for x in self._entries.values() if x.snapshot_id == snapshot_id), None)
        return (e is not None and e.status == "active"
                and bool(set(profile(snapshot_id).data_residency) & set(RESIDENCY_OK[tier])))

    def record(self, snapshot_id: str, *, ok: bool, tokens: int = 0) -> None:
        for e in self._entries.values():
            if e.snapshot_id == snapshot_id:
                e.calls += 1
                e.failures += int(not ok)
                e.tokens += tokens


def default_pool() -> ModelPool:
    p = ModelPool()
    for alias, snap in PINNED.items():
        p.register(alias, snap)
    return p


# ══════════════════════════════════════════════════════════════
# LLM 路由（rule／semantic／cascade，4.5／4.7）
# ══════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class RouteRule:
    """调用点级路由表的一行（第 4 章表 4-3 的最小实现）。"""
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

RouteFn = Callable[[str, "Message | str", list[str]], list[str]]


def _rule_route(cp: str, payload: "Message | str", cands: list[str]) -> list[str]:
    return cands


def rule_router() -> RouteFn:
    """默认路由：调用点级候选链原样返回（表 4-3）。规则式与学习式的分层组合见 4.5。"""
    return _rule_route


HARD_PROTO = {"合同", "违约", "索赔", "赔偿", "发票", "红冲", "纠纷", "法律", "责任", "条款"}


def _hard_overlap(text: str) -> float:
    """困难原型词的字符二元重合率（零依赖的"廉价语义信号"）。
    生产实现换成嵌入相似度，接口不变——这正是路由分层叠加（4.5）能落地的原因：
    **换策略不换调用方**。"""
    bigrams = {text[i:i + 2] for i in range(len(text) - 1)} if len(text) > 1 else set(text)
    proto = {w[i:i + 2] for w in HARD_PROTO for i in range(len(w) - 1)}
    return len(bigrams & proto) / max(len(proto), 1)


def _by_alias(snap: str) -> str:
    return next((a for a, sid in PINNED.items() if sid == snap), snap)


def _promote(cands: list[str], alias: str) -> list[str]:
    snap = PINNED[alias]
    if alias not in [_by_alias(c) for c in cands]:
        return cands                       # 池内分区过滤后该档不可用就不硬塞（4.10）
    return [snap] + [c for c in cands if c != snap]


def semantic_router(threshold: float = 0.02) -> RouteFn:
    """语义路由（模式卡「语义路由」的最小形态）：低重合率的简单请求降档到 small，
    高重合率的纠纷/合同类请求升档到 flagship——两个方向都改候选链顺序。"""
    def route(cp: str, payload: "Message | str", cands: list[str]) -> list[str]:
        score = _hard_overlap(str(payload))
        if score < threshold:
            return _promote(cands, "small")
        if score >= threshold * 4:
            return _promote(cands, "flagship")
        return cands
    return route


def cascade_router(escalate_threshold: float = 0.02) -> RouteFn:
    """级联路由（模式卡「级联升级」在网关内的形状）：判别器（此处用困难原型分数
    代理）判定难的请求直达 flagship；其余一律让最便宜的 small 先答第一跳。
    与 D-03 的关系：D-03 实测级联经济性算式 `1 + u × 10`（含双跑成本），这里提供
    **可被其他 demo 复用的实现钩子**——生产里判别器换成上一跳响应的自评或分类器。"""
    def route(cp: str, payload: "Message | str", cands: list[str]) -> list[str]:
        if _hard_overlap(str(payload)) >= escalate_threshold:
            return _promote(cands, "flagship")
        return _promote(cands, "small")
    return route


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
    """把模型实例包一层可注入故障的外壳；真实环境的故障以 LLMError 形式到来。

    实现 `LLM` 协议（model_id＋chat），因此可以被 `get_llm(..., gateway=)` 直接包装——
    这就是薄适配层与网关的接缝：应用侧接口不变，换的是注入的实现（4.4）。"""

    def __init__(self, mock: bool, failure_rate: float, seed: int) -> None:
        self.mock, self.failure_rate = mock, failure_rate
        self._rng, self._pool = random.Random(seed), {}

    def _client(self, model_id: str) -> LLM:
        if model_id not in self._pool:
            self._pool[model_id] = get_llm(model_id, mock=self.mock)
        return self._pool[model_id]

    def chat(self, model_id: str, payload: "Message | str") -> Response:
        if self._rng.random() < self.failure_rate:
            raise LLMError(f"上游 {model_id} 限流/超时（注入故障）", status=429)
        messages = [user(payload)] if isinstance(payload, str) else list(payload)
        return self._client(model_id).chat(messages)

    def complete(self, model_id: str, prompt: str) -> str:      # llm.LLM 协议的便捷文本入口
        return self.chat(model_id, prompt).text or ""


@dataclass
class Gateway:
    """LLM 接入网关：**模型池 ＋ LLM 路由**是它的两块核心（作者拍板 2026-10-15），
    其余职责（预算闸、缓存前置、快照钉扎、熔断与故障转移、遥测）围绕它们展开。"""

    backend: FlakyBackend
    pool: ModelPool = field(default_factory=default_pool)
    router: RouteFn = field(default_factory=rule_router)
    breaker: Breaker = field(default_factory=Breaker)
    budget: int = 10**9
    spent: dict[str, int] = field(default_factory=dict)
    cache: dict[str, Any] = field(default_factory=dict)
    telemetry: list[dict[str, Any]] = field(default_factory=list)

    def candidates(self, rule: RouteRule, payload: "Message | str",
                   tier: Tier) -> list[str]:
        """路由决策序列：模型池解析（含快照钉扎与准入闸）→ 合规分区过滤 → 路由函数。
        顺序不可颠倒：分区在路由之前生效，不可能被策略作者绕过（4.10）。"""
        aliases = [a for a in rule.candidates
                   if self.pool.usable_for(self.pool.entry(a).snapshot_id, tier)]
        snaps = [self.pool.entry(a).snapshot_id for a in aliases]   # 职责⑤：钉成快照 ID
        return self.router(rule.call_point, payload, snaps)

    def call(self, call_point: str, text: "Message | str", *, tenant: str = "default",
             tier: Tier = "public") -> dict[str, Any]:
        rule = ROUTING_TABLE[call_point]
        rec: dict[str, Any] = {"call_point": call_point, "tenant": tenant, "tier": tier,
                               "cached": False, "degraded": False, "model": None}
        self.telemetry.append(rec)                       # 职责⑦：每次调用一条遥测
        if self.spent.get(tenant, 0) >= self.budget:     # 职责③：预算闸在入口
            rec.update(degraded=True, reason="budget_exceeded", action=rule.degrade_to)
            return rec
        stable = re.sub(r"｜\d+$", "", str(text))          # 演示轮次后缀不进缓存键
        key = f"{call_point}:{tier}:{hash(stable)}"
        if rule.cacheable and key in self.cache:         # 职责②：缓存在路由之前
            rec.update(cached=True, **self.cache[key])
            return rec
        cands = self.candidates(rule, text, tier)        # 池 → 分区 → 路由（职责③④）
        rec["candidates"] = cands
        for model_id in cands:
            if self.breaker.is_open(model_id):           # 熔断打开：不打扰上游
                continue
            try:
                resp = self.backend.chat(model_id, text)
            except LLMError as exc:
                self.breaker.on_failure(model_id)
                self.pool.record(model_id, ok=False)
                rec.setdefault("failures", []).append(f"{model_id}:{exc.status}")
                continue                                 # 职责⑥：故障转移候选链
            self.breaker.on_success(model_id)
            tokens = resp.usage.total_tokens
            self.pool.record(model_id, ok=True, tokens=tokens)     # 遥测写回画像（图 4-1）
            self.spent[tenant] = self.spent.get(tenant, 0) + tokens
            body = {"model": model_id, "tokens": tokens}
            rec["text"] = resp.text or ""                        # 文本随遥测落盘（第 18 章语料）
            if rule.cacheable:
                self.cache[key] = {**body, "text": f"[缓存命中 {model_id}]"}
            rec.update(**body)
            return rec
        # 候选集为空（合规/弃用过滤后无可用模型）或全部熔断/失败 → 走降级矩阵（表 4-4）
        rec.update(degraded=True, action=rule.degrade_to,
                   reason="no_candidate" if not cands else "all_failed")
        return rec

    # —— LLM 协议门面：网关自己就是一个 LLM 实现，供 get_llm(..., gateway=gw) 注入 ——
    @property
    def model_id(self) -> str:
        return "gateway"

    def complete(self, call_point: str, payload: "Message | str", *,
                 tenant: str = "default", tier: Tier = "public") -> Response:
        """chat 的响应形态：把路由命中的真实 Response 带回来（缓存命中时给摘要文本）。"""
        rec = self.call(call_point, payload, tenant=tenant, tier=tier)
        if rec.get("degraded"):
            raise LLMError(f"网关降级：{rec.get('reason')}→{rec.get('action')}", status=None)
        tokens = rec.get("tokens", 0)
        if rec.get("cached"):                                    # 缓存命中：上游未重付，给摘要响应
            return Response(rec.get("text") or f"[缓存命中 {rec.get('model')}]", (),
                            Usage(tokens, 0), rec.get("model") or "unknown")
        return Response(rec.get("text") or "", (), Usage(tokens, 0),
                        rec.get("model") or "unknown")

    def chat(self, messages: Sequence[Message], *, tools: Sequence[ToolSpec] | None = None,
             max_tokens: int = 1024, temperature: float = 0.0,
             schema: dict[str, Any] | None = None) -> Response:
        """LLM 协议入口：默认按 reply 调用点路由。需要别的调用点请用 call()/complete()。"""
        return self.complete("reply", list(messages))


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


def print_pool(gw: Gateway) -> None:
    """模型池视图：准入状态＋画像摘要＋运行时指标（路由读它、遥测写回它，图 4-1）。"""
    print(f"\n{'档位':<10}{'快照 ID':<18}{'状态':<12}{'驻留':<22}"
          f"{'上下文':>8}{'价格档':>7}{'调用':>6}{'失败率':>8}{'token':>9}")
    for e in gw.pool._entries.values():
        p = profile(e.snapshot_id)
        print(f"{e.alias:<10}{e.snapshot_id:<18}{e.status:<12}"
              f"{'+'.join(p.data_residency):<22}{p.context_window:>8,}"
              f"{p.price_tier:>7}{e.calls:>6}{e.fail_rate:>8.1%}{e.tokens:>9,}")


def main() -> None:
    ap = argparse.ArgumentParser(description="D-04 最小模型网关（模型池＋LLM路由基座）")
    ap.add_argument("--mock", action="store_true", help="离线模式（无需网络与密钥）")
    ap.add_argument("--calls", type=int, default=60)
    ap.add_argument("--failure-rate", type=float, default=0.0, help="注入的上游故障率")
    ap.add_argument("--budget", type=int, default=10**9, help="租户 token 预算")
    ap.add_argument("--tenant", default="default")
    ap.add_argument("--tier", choices=["public", "internal", "regulated"], default="public")
    ap.add_argument("--router", choices=["rule", "semantic", "cascade"], default="rule",
                    help="LLM 路由策略：规则＝调用点级候选链；语义＝按难度双向改档；"
                         "级联＝易任务小模型先答（判别器代理）")
    ap.add_argument("--show-pool", action="store_true", help="打印模型池注册表与运行时指标")
    ap.add_argument("--seed", type=int, default=20261013)
    ap.add_argument("--dump-telemetry", type=Path, default=None, help="遥测落盘为 JSONL")
    args = ap.parse_args()

    router = {"rule": rule_router(), "semantic": semantic_router(),
              "cascade": cascade_router()}[args.router]
    gw = Gateway(FlakyBackend(args.mock, args.failure_rate, args.seed),
                 router=router, budget=args.budget)
    # 每个调用点跑一易一难两条任务（各 repeats 遍）：语义/级联路由的差异只有
    # 在难度混合的流量里才观察得到——这正是 4.5 "分层叠加"要处理的真实形状。
    easy = {"plan": "查询订单进度并汇总状态", "tool_args": "查询物流状态是否支持导出",
            "summarize": "咨询版本功能，请总结要点", "reply": "如何重置密码，请答复客户",
            "judge": "检查这段答复是否合规"}
    hard = {"plan": "合同纠纷索赔方案：审查违约条款与赔偿责任",
            "tool_args": "发票金额与合同不符，按法律条款红冲重开",
            "summarize": "逾期索赔纠纷的法律条款责任认定摘要",
            "reply": "客户要求赔偿违约损失，答复需引用合同条款",
            "judge": "判定这份红冲发票是否存在违规责任"}
    repeats = max(args.calls // (2 * len(ROUTING_TABLE)), 1)
    n = 0
    for cp in ROUTING_TABLE:
        for text in (easy[cp], hard[cp]):
            for _ in range(repeats):
                gw.call(cp, f"{text}｜{n}", tenant=args.tenant, tier=args.tier)
                n += 1
    summarize(gw, args)
    if args.show_pool:
        print_pool(gw)
    print("观察什么：①failure_rate 提高后模型分布向候选链下游迁移、熔断打开次数上升，"
          "而 judge 只有一个候选，于是大面积降级——**没有候选链的调用点没有韧性**；")
    print("         ②tier=regulated 时候选集只剩本地部署模型，plan/reply/judge 因无合规模型"
          "而显式降级（分区在路由之前生效，不可能被绕过）；③budget 调小后出现 budget_exceeded；")
    print("         ④--router semantic/cascade 后易任务的候选链头部出现 mock-small-1"
          "（模型池里的第三档开始接量）——换策略不换调用方，这就是『网关提供 LLM 路由』"
          "的最小形状；--show-pool 看每档实际接了多少。")
    if args.dump_telemetry:
        args.dump_telemetry.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in gw.telemetry) + "\n",
            encoding="utf-8")
        print(f"\n遥测已写入 {args.dump_telemetry}（第 18 章轨迹评估、第 19 章成本归因的数据源）")


if __name__ == "__main__":
    main()
