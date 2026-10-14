"""D-02　结构化输出守卫：schema 约束 vs 文本解析（100 次运行统计）

对应书中：第 3 章 3.2.3 与 3.9 节，模式卡「结构化输出守卫」。

演示三层守卫（schema 约束 → 语义校验 → 有限重试与降级）与"只做 json.loads"
的差别。**离线 `--mock` 模式下，畸形率与语义错误率是被注入的参数**，因此本
demo 只对"机制与形状"负责，不代表任何真实模型的可靠性数字（第 3 章 3.9）。

用法：
    python structured_guard.py --mock --runs 100
    python structured_guard.py --mock --runs 100 --malform-rate 0.15
    python structured_guard.py --mock --runs 100 --strict-schema
    LLM_MODEL=<快照ID> LLM_API_KEY=... python structured_guard.py --runs 100
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from llm import MockLLM, get_llm, profile, system, user  # noqa: E402

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": ["物流", "质量", "账务", "其他"]},
        "priority": {"type": "string", "enum": ["high", "mid", "low"]},
        "need_human": {"type": "boolean"},
        "evidence": {"type": "string", "minLength": 1},
    },
    "required": ["category", "priority", "need_human", "evidence"],
}
# --strict-schema 时启用：证据必须足够长，且高优先级必须转人工（跨字段业务规则）
STRICT_RULES = {"min_evidence_chars": 12, "high_implies_human": True}
PROMPT_TEXT = "请判断工单类别、优先级、是否需转人工，并给出证据。只输出 JSON。"
RETRY_LIMIT = 2
TICKETS = [
    "包裹显示已签收但客户说没收到，已经催了两次，客户要求今天给答复。",
    "上月发票金额与合同不符，财务要求本周内红冲重开，涉及金额 12 万。",
    "设备运行时有异响，客户怀疑是轴承问题，希望安排工程师上门。",
    "客户询问新版本是否支持批量导入，属于售前咨询。",
]


@dataclass
class Result:
    status: Literal["ok", "parse_error", "semantic_error", "degraded"]
    data: dict[str, Any] | None = None
    reason: str = ""
    calls: int = 1


def validate_syntax(raw: str) -> tuple[dict[str, Any] | None, str]:
    """第一层的"退化版"：只做 json.loads（提示式路径能做的全部事情）。"""
    try:
        obj = json.loads(raw)
        return (obj, "") if isinstance(obj, dict) else (None, "顶层不是对象")
    except json.JSONDecodeError as exc:
        return None, f"JSON 解析失败：{exc.msg}"


def validate_semantic(data: dict[str, Any], *, strict: bool) -> tuple[bool, list[str]]:
    """第二层：schema 管不到的业务规则。守卫的价值主要在这一层。"""
    errs: list[str] = []
    for key, sch in SCHEMA["properties"].items():
        if key not in data:
            errs.append(f"缺字段 {key}")
            continue
        if "enum" in sch and data[key] not in sch["enum"]:
            errs.append(f"{key}={data[key]!r} 不在枚举内")
        if sch["type"] == "boolean" and not isinstance(data[key], bool):
            errs.append(f"{key} 应为布尔值")
        if sch.get("minLength") and not str(data[key]).strip():
            errs.append(f"{key} 为空（模型为满足 schema 填了占位值）")
    if strict:
        if len(str(data.get("evidence", ""))) < STRICT_RULES["min_evidence_chars"]:
            errs.append(f"证据短于 {STRICT_RULES['min_evidence_chars']} 字，疑似敷衍")
        if STRICT_RULES["high_implies_human"] and data.get("priority") == "high" \
                and data.get("need_human") is not True:
            errs.append("高优先级必须转人工（跨字段规则）")
    return (not errs, errs)


def classify_once(llm: Any, text: str, *, mode: str, strict: bool) -> Result:
    """mode='prompted' 只做文本解析；mode='guarded' 走三层守卫。"""
    if mode == "prompted":
        resp = llm.chat([system(PROMPT_TEXT), user(text)])
        obj, err = validate_syntax(resp.text or "")
        if obj is None:
            return Result("parse_error", None, err)
        ok, errs = validate_semantic(obj, strict=strict)
        return Result("ok" if ok else "semantic_error", obj, "; ".join(errs))
    resp = llm.chat([system(PROMPT_TEXT), user(text)], schema=SCHEMA)
    errs: list[str] = []
    for attempt in range(RETRY_LIMIT + 1):
        obj = resp.data if resp.data is not None else validate_syntax(resp.text or "")[0]
        if obj is None:
            # 画像写着 constrained 却拿不到合法 JSON ＝ 上游行为与画像不符（3.5 探针要抓的事）
            errs = ["上游未按 schema 返回（画像与实际不符）"]
        else:
            ok, errs = validate_semantic(obj, strict=strict)
            if ok:
                return Result("ok", obj, "", calls=attempt + 1)
        if attempt == RETRY_LIMIT:
            # 第三层的出口：显式降级，而不是假装成功把脏数据写进业务系统
            return Result("degraded" if obj is not None else "parse_error", obj,
                          "; ".join(errs), calls=attempt + 1)
        resp = llm.chat([system(PROMPT_TEXT), user(text),
                         user("上次输出不合法：" + "; ".join(errs) + "，请修正后重新输出。")],
                        schema=SCHEMA)
    return Result("degraded", None, "unreachable")


@dataclass
class Stats:
    counts: dict[str, int] = field(default_factory=dict)
    calls: int = 0

    def add(self, r: Result) -> None:
        self.counts[r.status] = self.counts.get(r.status, 0) + 1
        self.calls += r.calls


def build_llm(args: argparse.Namespace) -> Any:
    if args.mock:
        return MockLLM(args.model, malform_rate=args.malform_rate,
                       semantic_error_rate=args.semantic_error_rate,
                       force_output_mode=args.force_mode, json_schema=SCHEMA,
                       unrepairable_rate=args.unrepairable_rate, seed=args.seed)
    return get_llm(args.model)          # 真实调用：密钥只从环境变量读取


def run(args: argparse.Namespace) -> tuple[Stats, Stats]:
    out: dict[str, Stats] = {"prompted": Stats(), "guarded": Stats()}
    for mode, st in out.items():
        llm = build_llm(args)           # 每条路径独立实例，避免随机序列互相污染
        for i in range(args.runs):
            st.add(classify_once(llm, TICKETS[i % len(TICKETS)], mode=mode,
                                 strict=args.strict_schema))
    return out["prompted"], out["guarded"]


def main() -> None:
    ap = argparse.ArgumentParser(description="D-02 结构化输出守卫对比实验")
    ap.add_argument("--mock", action="store_true", help="离线模式（无需网络与密钥）")
    ap.add_argument("--runs", type=int, default=100, help="每条路径的运行次数")
    ap.add_argument("--model", default="mock-flagship-1", help="模型池中的快照 ID")
    ap.add_argument("--malform-rate", type=float, default=0.10,
                    help="提示式路径的语法畸形概率（仅 --mock 生效）")
    ap.add_argument("--semantic-error-rate", type=float, default=0.20,
                    help="语法合法但语义错的概率（仅 --mock 生效）")
    ap.add_argument("--force-mode", choices=["constrained", "prompted"], default=None,
                    help="强制输出模式，便于在同一模型上对比两条路径")
    ap.add_argument("--strict-schema", action="store_true",
                    help="启用严格业务规则（证据长度＋跨字段规则）")
    ap.add_argument("--unrepairable-rate", type=float, default=0.25,
                    help="反馈也修不好的比例：这部分会走到显式降级（仅 --mock 生效）")
    ap.add_argument("--seed", type=int, default=20261013)
    args = ap.parse_args()

    p = profile(args.model)
    print(f"模型 {args.model}｜structured_output={p.structured_output}"
          f"｜runs={args.runs}｜malform={args.malform_rate}｜semantic_err={args.semantic_error_rate}"
          f"｜strict={args.strict_schema}｜unrepairable={args.unrepairable_rate}"
          f"｜mock={args.mock}")
    pr, gu = run(args)
    print(f"\n{'路径':<22}{'ok':>6}{'parse_error':>13}{'semantic_error':>16}"
          f"{'degraded':>10}{'模型调用数':>12}{'平均调用倍数':>14}")
    for name, st in (("提示式（只 json.loads）", pr), ("三层守卫（schema+校验+重试）", gu)):
        c = st.counts
        print(f"{name:<22}{c.get('ok', 0):>6}{c.get('parse_error', 0):>13}"
              f"{c.get('semantic_error', 0):>16}{c.get('degraded', 0):>10}"
              f"{st.calls:>12}{st.calls / args.runs:>14.2f}")
    print("\n观察什么：")
    print("  ① 畸形率 0 时两条路径几乎没有差别——这就是'跑了二十个例子都没问题'的来源；")
    print("  ② 畸形率升高时，提示式的失败与它同量级上升，守卫靠重试吃掉大部分失败，")
    print("     代价看'平均调用倍数'（>1 的部分就是重试的钱与延迟）；")
    print("  ③ 加 --strict-schema 后，语法合法率不变但语义失败上升——约束解码不担保语义；")
    print("  ④ 守卫路径的失败全部落在 degraded（显式降级），不会静默写脏数据；")
    print("     --unrepairable-rate 调的就是'反馈也修不好'的比例，它是降级量的来源。")


if __name__ == "__main__":
    main()
