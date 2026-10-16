#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
llm.py — 全书 demo 共用的模型薄适配层（framework-agnostic / vendor-agnostic）
=============================================================================

设计目标（策划案§十、QWEN.md §八）：
  · **零第三方依赖**：只用标准库。真实供应商走 OpenAI 兼容的 HTTP 接口，用 urllib 直调，
    以此证明"不绑定商业框架"不是口号——换供应商只改一个字符串。
  · **多模态输入**：Message.content 可以是文本，也可以是 ContentPart 列表
    （文本／图像／音频／文档句柄），为第 13–14 章的 demo 预留通道。
  · **模型快照固定（snapshot pinning）**：能力画像与请求都锁定到具体快照 ID，
    而不是 "latest" 一类的浮动别名（第 4 章）。
  · **故障转移钩子**：FailoverLLM 提供熔断与降级，具体策略在第 4 章 D-04 展开。
  · **网关接缝（gateway 注入）**：`get_llm(..., gateway=gw)` 把调用改道到 `code/gateway.py`
    ——模型池与 LLM 路由住在那里；不注入时仍是进程内薄适配层（4.4 的演进路径）。
  · **能力画像查询**：`profile(model_id)` 返回结构化画像，是路由的输入、选型的输出、
    回归测试的基线（第 4 章 4.3）。
  · **--mock 离线模式**：MockLLM 用脚本化轨迹替身，无网络、无 API key 可跑通全流程。

书中引用本文件时统一标注路径 `code/llm.py`。
"""
from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Protocol, Sequence

# ─────────────────────────────────────────────────────────────
# 1. 消息与内容部件（多模态通道）
# ─────────────────────────────────────────────────────────────
Role = Literal["system", "user", "assistant", "tool"]


@dataclass(frozen=True)
class TextPart:
    text: str
    type: Literal["text"] = "text"


@dataclass(frozen=True)
class ImagePart:
    """图像句柄。data 为 bytes 或 None（此时用 ref 指向本地路径/URL）。

    trust_level 是第 17 章的关键字段：外部来源的图像内容与原生视觉输入
    不可同等信任，注入防御要按 trust_level 分层。
    """
    ref: str | None = None
    data: bytes | None = None
    mime: str = "image/png"
    trust_level: Literal["native", "derived", "untrusted"] = "untrusted"
    type: Literal["image"] = "image"


@dataclass(frozen=True)
class AudioPart:
    ref: str | None = None
    data: bytes | None = None
    mime: str = "audio/wav"
    duration_ms: int | None = None          # 第 14 章延迟预算需要
    trust_level: Literal["native", "derived", "untrusted"] = "untrusted"
    type: Literal["audio"] = "audio"


@dataclass(frozen=True)
class DocumentPart:
    """文档句柄。ocr_text 非空表示已经过感知管线转换（第 13 章的"管线在哪切"）。"""
    ref: str | None = None
    data: bytes | None = None
    mime: str = "application/pdf"
    ocr_text: str | None = None
    trust_level: Literal["native", "derived", "untrusted"] = "derived"
    type: Literal["document"] = "document"


ContentPart = TextPart | ImagePart | AudioPart | DocumentPart


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class Message:
    role: Role
    content: str | tuple[ContentPart, ...] = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    name: str | None = None

    def text_only(self) -> str:
        if isinstance(self.content, str):
            return self.content
        return "".join(p.text for p in self.content if isinstance(p, TextPart))


def system(text: str) -> Message:
    return Message(role="system", content=text)


def user(text: str) -> Message:
    return Message(role="user", content=text)


def user_multimodal(*parts: ContentPart) -> Message:
    """多模态用户消息：第 13–14 章 demo 的入口。"""
    return Message(role="user", content=tuple(parts))


def tool_result(call_id: str, text: str, name: str | None = None) -> Message:
    return Message(role="tool", content=text, tool_call_id=call_id, name=name)


# ─────────────────────────────────────────────────────────────
# 2. 工具规格、用量、响应
# ─────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ToolSpec:
    """工具定义＝给模型读的 API 文档（第 7 章）。description 的质量决定调用正确率。"""
    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})

    def to_openai(self) -> dict[str, Any]:
        return {"type": "function",
                "function": {"name": self.name, "description": self.description,
                             "parameters": self.parameters}}


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class Response:
    text: str | None
    tool_calls: tuple[ToolCall, ...]
    usage: Usage
    model: str
    finish_reason: str = "stop"
    data: dict[str, Any] | None = None   # 结构化输出载荷（第 3 章 3.2.3：约束解码的产物）

    def as_message(self) -> Message:
        return Message(role="assistant", content=self.text or "", tool_calls=self.tool_calls)


# ─────────────────────────────────────────────────────────────
# 3. 能力画像（capability profile）——第 4 章 4.3 的数据结构
# ─────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class CapabilityProfile:
    model_id: str                      # 必须是快照 ID，不接受 "latest"
    vendor: str
    context_window: int
    modalities_in: tuple[str, ...]     # text / image / document / audio / video / screen
    modalities_out: tuple[str, ...]
    tool_call_style: str               # "openai-function" / "anthropic-tool" / "none"
    parallel_tool_calls: bool
    structured_output: Literal["constrained", "prompted", "none"]
    thinking_budget: bool              # 是否支持显式思维链预算
    latency_class: Literal["low", "mid", "high"]
    price_tier: int                    # 1（最省）… 5（旗舰）。**相对档位，不是单价**
    license: str                       # "proprietary" / "open-weights"
    data_residency: tuple[str, ...]    # 可选部署区域，合规路由用
    deprecation_horizon: str           # 弃用预告周期，如 "≥12 个月"

    @property
    def supports(self) -> set[str]:
        return set(self.modalities_in)


# 模型池：两条真实形态的画像 + 一条本地小模型画像（第 4 章 4.10 的驻留路由用）。
# 注意：这里登记的是**结构与量级**，不是任何时刻的真实规格；书中一律不写单价与排名。
MODEL_POOL: dict[str, CapabilityProfile] = {
    "mock-small-1": CapabilityProfile(
        model_id="mock-small-1", vendor="mock", context_window=32_000,
        modalities_in=("text",), modalities_out=("text",),
        tool_call_style="openai-function", parallel_tool_calls=False,
        structured_output="prompted", thinking_budget=False, latency_class="low",
        price_tier=1, license="open-weights", data_residency=("on-prem",),
        deprecation_horizon="不适用（本地部署）"),
    "mock-flagship-1": CapabilityProfile(
        model_id="mock-flagship-1", vendor="mock", context_window=200_000,
        modalities_in=("text", "image", "document", "audio"), modalities_out=("text",),
        tool_call_style="openai-function", parallel_tool_calls=True,
        structured_output="constrained", thinking_budget=True, latency_class="high",
        price_tier=5, license="proprietary", data_residency=("cloud-cn", "cloud-us"),
        deprecation_horizon="≥12 个月"),
    "mock-vision-1": CapabilityProfile(
        model_id="mock-vision-1", vendor="mock", context_window=128_000,
        modalities_in=("text", "image", "document"), modalities_out=("text",),
        tool_call_style="openai-function", parallel_tool_calls=True,
        structured_output="constrained", thinking_budget=False, latency_class="mid",
        price_tier=3, license="proprietary", data_residency=("cloud-cn",),
        deprecation_horizon="≥6 个月"),
}


def profile(model_id: str) -> CapabilityProfile:
    """能力画像查询接口。路由决策、模态可行性判定、回归基线都从这里取。"""
    try:
        return MODEL_POOL[model_id]
    except KeyError:
        raise KeyError(
            f"模型 {model_id!r} 不在模型池中。接入新模型必须先登记画像——"
            f"这是第 4 章模型池治理的第一道门禁。现有：{sorted(MODEL_POOL)}") from None


# ─────────────────────────────────────────────────────────────
# 4. LLM 接口与三种实现
# ─────────────────────────────────────────────────────────────
class LLM(Protocol):
    model_id: str

    def chat(self, messages: Sequence[Message], *,
             tools: Sequence[ToolSpec] | None = None,
             max_tokens: int = 1024, temperature: float = 0.0,
             schema: dict[str, Any] | None = None) -> Response: ...


def _estimate_tokens(messages: Sequence[Message]) -> int:
    """离线 token 估算：中文约 1 字 ≈ 1.5 token，英文约 4 字符 ≈ 1 token。
    估算口径写在 README 里；真实调用以供应商返回的 usage 为准（第 19 章）。"""
    n = 0
    for m in messages:
        s = m.text_only() + "".join(c.name + json.dumps(c.args, ensure_ascii=False)
                                    for c in m.tool_calls)
        n += int(len([c for c in s if ord(c) > 0x2E80]) * 1.5) + len(s) // 4 + 4
    return n


class MockLLM:
    """离线替身：按脚本推进轨迹，遇到工具错误会重试（用于演示预算熔断）。

    行为设计对应第 1 章 1.7 节的"观察什么"：
      · 默认把每个工具按给定顺序调一遍，然后给出最终答复；
      · 若上一条 tool 消息含 "ERROR"，则重试同一工具（最多 retry_limit 次），
        以此让 tool_fail_rate 高的场景真的把 token 预算烧穿；
      · token 计数用 _estimate_tokens 累加，使 T(n) 的平方增长可被观察。
    """

    def __init__(self, model_id: str = "mock-flagship-1", *, retry_limit: int = 3,
                 final_text: str = "已完成信息收集，建议按知识库标准答复客户。",
                 malform_rate: float = 0.0, semantic_error_rate: float = 0.0,
                 force_output_mode: Literal["constrained", "prompted"] | None = None,
                 json_schema: dict[str, Any] | None = None,
                 unrepairable_rate: float = 0.0,
                 seed: int = 20261013) -> None:
        if model_id not in MODEL_POOL:
            raise KeyError(f"MockLLM 只接受模型池中已登记画像的模型：{sorted(MODEL_POOL)}")
        self.model_id = model_id
        self.retry_limit = retry_limit
        self.final_text = final_text
        # 下面三个参数是 D-02 的"仪器旋钮"：模拟环境只演示**机制**，
        # 不代表任何真实模型的可靠性（第 3 章 3.9 的免责说明）。
        self.malform_rate = malform_rate                # 提示式：语法畸形概率
        self.semantic_error_rate = semantic_error_rate  # 约束式：语法合法但语义错概率
        self.force_output_mode = force_output_mode      # 强制输出模式，便于同一模型对比
        self.json_schema = json_schema                  # 提示式路径用：不给 schema 也要产出 JSON
        self.unrepairable_rate = unrepairable_rate      # 反馈也修不好的比例（→ 显式降级）
        self.calls = 0
        self._retries: dict[str, int] = {}
        self._rng = random.Random(seed)
        self._stuck = False            # 本轮请求是否属于"反馈也修不好"的那一类

    def chat(self, messages: Sequence[Message], *, tools: Sequence[ToolSpec] | None = None,
             max_tokens: int = 1024, temperature: float = 0.0,
             schema: dict[str, Any] | None = None) -> Response:
        self.calls += 1
        prompt = _estimate_tokens(messages)
        if schema is not None:
            return self._structured(messages, schema, prompt)
        wants_json = self.json_schema is not None and any(
            "JSON" in m.text_only() for m in messages if m.role == "system")
        if wants_json:      # 提示里要求 JSON 但没有约束解码 ＝ 提示式路径（3.2.3）
            return self._structured(messages, self.json_schema, prompt,
                                    mode_override="prompted")
        tools = list(tools or [])
        last = messages[-1] if messages else None
        last_failed = last is not None and last.role == "tool" and "ERROR" in last.text_only()

        # 出错重试：模型无法自我约束，重试次数必须由外部计数（第 1 章 1.3.2）
        if last_failed and last.name:
            n = self._retries.get(last.name, 0)
            if n < self.retry_limit:
                self._retries[last.name] = n + 1
                call = ToolCall(id=f"call_{self.calls}", name=last.name,
                                args={"retry": n + 1, "note": "参数已按错误提示修正"})
                return Response(None, (call,), Usage(prompt, 40), self.model_id, "tool_calls")

        done = {m.name for m in messages if m.role == "tool" and m.name}
        nxt = next((t for t in tools if t.name not in done), None)
        if nxt is not None:
            call = ToolCall(id=f"call_{self.calls}", name=nxt.name,
                            args=_mock_args(nxt, messages))
            return Response(None, (call,), Usage(prompt, 48), self.model_id, "tool_calls")
        return Response(self.final_text, (), Usage(prompt, len(self.final_text) // 2),
                        self.model_id, "stop")


    def _structured(self, messages: Sequence[Message], schema: dict[str, Any],
                    prompt: int, *, mode_override: str | None = None) -> Response:
        """按能力画像的 structured_output 字段模拟两种实现路径（第 3 章 3.2.3）。

        constrained：语法必然合法（token 掩码），但语义错误照旧可能发生；
        prompted  ：语法可能畸形——失败模式是"静默错误"，这正是守卫要拦的东西。
        """
        mode = mode_override or self.force_output_mode \
            or profile(self.model_id).structured_output
        fb = next((m.text_only() for m in reversed(messages)
                   if m.role == "user" and "不合法" in m.text_only()), "")
        props = schema.get("properties", {})
        obj: dict[str, Any] = {}
        for k, sch in props.items():
            enum = sch.get("enum")
            typ = sch.get("type", "string")
            if enum:
                obj[k] = enum[0]
            elif typ == "boolean":
                obj[k] = False
            elif typ in ("integer", "number"):
                obj[k] = 1
            else:
                obj[k] = f"工单正文中的证据片段（{k}）" if sch.get("minLength") else f"sample-{k}"
        # 语义错误：把必填文本字段留空（"语法合法但值不对"，约束解码拦不住的那一类）。
        # 带失败原因反馈的重试通常能修正——这就是守卫第三层的收益，也是 D-02 要观察的东西。
        if not fb:      # 新请求：重新决定它是否属于"反馈也修不好"的一类
            self._stuck = self._rng.random() < self.unrepairable_rate
        bad = self._rng.random() < self.semantic_error_rate and not fb
        if fb and self._stuck:
            bad = True      # 这一类请求"反馈也修不好"，会一路走到显式降级（守卫的第三层出口）
        if bad:
            for k, sch in props.items():
                if sch.get("minLength"):
                    obj[k] = ""
        if fb and not self._stuck:
            # 带失败原因的反馈重试：能修好的都修好（守卫第三层的收益来源）
            if "转人工" in fb and "need_human" in obj:
                obj["need_human"] = True
            for k, sch in props.items():
                if sch.get("minLength") and not str(obj.get(k, "")).strip():
                    obj[k] = f"工单正文第 {self.calls % 7 + 1} 段的原文片段，支持上述判断（{k}）"
            if "枚举" in fb:
                for k, sch in props.items():
                    if "enum" in sch and obj.get(k) not in sch["enum"]:
                        obj[k] = sch["enum"][0]
        text = json.dumps(obj, ensure_ascii=False)
        if mode == "prompted" and self._rng.random() < self.malform_rate and not fb:
            text = self._malform(text)       # 提示式路径：语法畸形（静默错误的来源）
            return Response(text, (), Usage(prompt, len(text) // 2), self.model_id,
                            "stop", data=None)
        return Response(text, (), Usage(prompt, len(text) // 2), self.model_id,
                        "stop" if not bad else "semantic_error", data=dict(obj))

    def _malform(self, text: str) -> str:
        """造三种真实世界常见的畸形输出：包解释、截断、单引号。"""
        kind = self._rng.randrange(3)
        if kind == 0:
            return f"好的，分类结果如下：\n{text}\n希望这对你有帮助！"
        if kind == 1:
            return text[: max(1, len(text) // 2)]
        return text.replace('"', "'")


def _mock_args(spec: ToolSpec, messages: Sequence[Message]) -> dict[str, Any]:
    """按 schema 造合规假参数，让 demo 的轨迹看起来像真的。"""
    props = spec.parameters.get("properties", {})
    out: dict[str, Any] = {}
    for k, sch in props.items():
        t = sch.get("type")
        out[k] = {"string": f"sample-{k}", "integer": 1, "number": 1.0,
                  "boolean": True, "array": [], "object": {}}.get(t, f"sample-{k}")
    return out


class OpenAICompatibleLLM:
    """真实供应商适配：任何 OpenAI 兼容的 /chat/completions 端点。

    密钥只从环境变量读取（示例代码中禁止出现任何真实密钥形态）。
    endpoint 与 model 都是参数，换供应商＝换两个字符串。
    """

    def __init__(self, model_id: str, *, endpoint: str | None = None,
                 api_key_env: str = "LLM_API_KEY", timeout: float = 60.0) -> None:
        self.model_id = model_id
        self.endpoint = endpoint or os.environ.get(
            "LLM_ENDPOINT", "https://api.openai.com/v1/chat/completions")
        self.api_key = os.environ.get(api_key_env, "")
        self.timeout = timeout
        profile(model_id)          # 接入门禁：未登记画像的模型不允许直调

    def chat(self, messages: Sequence[Message], *, tools: Sequence[ToolSpec] | None = None,
             max_tokens: int = 1024, temperature: float = 0.0,
             schema: dict[str, Any] | None = None) -> Response:
        if not self.api_key:
            raise RuntimeError(f"缺少 API key（环境变量 {os.environ.get('LLM_KEY_NAME', 'LLM_API_KEY')}）。"
                               f"离线运行请加 --mock。")
        payload: dict[str, Any] = {"model": self.model_id, "max_tokens": max_tokens,
                                   "temperature": temperature,
                                   "messages": [_serialize(m) for m in messages]}
        if tools:
            payload["tools"] = [t.to_openai() for t in tools]
        if schema is not None:      # 约束解码：语法非法的输出在机制上不可能产生（3.2.3）
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "structured_output", "schema": schema, "strict": True}}
        req = urllib.request.Request(
            self.endpoint, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"})
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            raise LLMError(f"上游 HTTP {e.code}", status=e.code,
                           latency_ms=(time.monotonic() - t0) * 1000) from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise LLMError(f"上游不可达：{e}", status=None,
                           latency_ms=(time.monotonic() - t0) * 1000) from e
        resp = _parse(data, self.model_id)
        if schema is not None and resp.text:
            try:                    # 解析失败即"显式拒绝"，交给守卫的重试层（3.8）
                resp = Response(resp.text, resp.tool_calls, resp.usage, resp.model,
                                resp.finish_reason, data=json.loads(resp.text))
            except json.JSONDecodeError:
                pass
        return resp


class LLMError(RuntimeError):
    """上游故障。status=None 表示网络层失败；FailoverLLM 据此决定是否切换。"""

    def __init__(self, msg: str, *, status: int | None = None, latency_ms: float = 0.0) -> None:
        super().__init__(msg)
        self.status = status
        self.latency_ms = latency_ms


class FailoverLLM:
    """故障转移钩子：按顺序尝试候选，记录每次切换（第 4 章 4.8 的最小形态）。

    刻意保持简单：熔断阈值、退避、按路由遥测在 D-04 的 gateway.py 里实现。
    这里只保证一件事——**降级不是改个模型名**：切换会被记录，因为换模型
    意味着换工具调用格式、换系统提示行为、换缓存命中（第 4 章 4.8）。
    """

    def __init__(self, candidates: Iterable[LLM]) -> None:
        self.candidates = list(candidates)
        if not self.candidates:
            raise ValueError("FailoverLLM 至少需要一个候选模型")
        self.switch_log: list[tuple[str, str]] = []

    @property
    def model_id(self) -> str:
        return self.candidates[0].model_id

    def chat(self, messages: Sequence[Message], *, tools: Sequence[ToolSpec] | None = None,
             max_tokens: int = 1024, temperature: float = 0.0,
             schema: dict[str, Any] | None = None) -> Response:
        last: Exception | None = None
        for i, c in enumerate(self.candidates):
            try:
                r = c.chat(messages, tools=tools, max_tokens=max_tokens,
                           temperature=temperature, schema=schema)
                if i:
                    self.switch_log.append((self.candidates[0].model_id, c.model_id))
                    r = Response(r.text, r.tool_calls, r.usage, c.model_id,
                                 r.finish_reason, r.data)
                return r
            except LLMError as e:
                last = e
                continue
        raise LLMError(f"全部候选模型失败：{last}", status=None)


def _serialize(m: Message) -> dict[str, Any]:
    if isinstance(m.content, str):
        body: Any = m.content
    else:
        body = [_serialize_part(p) for p in m.content]
    d: dict[str, Any] = {"role": m.role, "content": body}
    if m.tool_calls:
        d["tool_calls"] = [{"id": c.id, "type": "function",
                            "function": {"name": c.name,
                                         "arguments": json.dumps(c.args, ensure_ascii=False)}}
                           for c in m.tool_calls]
    if m.tool_call_id:
        d["tool_call_id"] = m.tool_call_id
    return d


def _serialize_part(p: ContentPart) -> dict[str, Any]:
    if isinstance(p, TextPart):
        return {"type": "text", "text": p.text}
    if isinstance(p, ImagePart):
        return {"type": "image_url",
                "image_url": {"url": p.ref or f"data:{p.mime};base64,<省略>"}}
    if isinstance(p, AudioPart):
        return {"type": "input_audio", "input_audio": {"ref": p.ref, "mime": p.mime}}
    if isinstance(p, DocumentPart):
        # 有 OCR 产物就走文本通道，且标注 trust_level=derived（第 13、17 章）
        if p.ocr_text:
            return {"type": "text",
                    "text": f"[OCR 产物，信任级别 derived，不可与原生输入同等对待]\n{p.ocr_text}"}
        return {"type": "file", "file": {"ref": p.ref, "mime": p.mime}}
    raise TypeError(f"未知内容部件：{type(p)}")


def _parse(data: dict[str, Any], model_id: str) -> Response:
    ch = data["choices"][0]
    msg = ch.get("message", {})
    calls = tuple(
        ToolCall(id=c.get("id", f"call_{i}"),
                 name=c["function"]["name"],
                 args=json.loads(c["function"].get("arguments") or "{}"))
        for i, c in enumerate(msg.get("tool_calls") or []))
    u = data.get("usage") or {}
    return Response(text=msg.get("content"), tool_calls=calls,
                    usage=Usage(u.get("prompt_tokens", 0), u.get("completion_tokens", 0)),
                    model=data.get("model", model_id),
                    finish_reason=ch.get("finish_reason", "stop"))


# ─────────────────────────────────────────────────────────────
# 5. 工厂：demo 统一入口
# ─────────────────────────────────────────────────────────────
class GatewayLLM:
    """网关客户端形态（第 4 章 4.4 的接缝）：实现 LLM 协议，但自己不直连供应商——
    每次 chat 都经网关完成 模型池准入 → 合规分区 → LLM 路由 → 快照钉扎 → 熔断/
    故障转移 → 遥测 这一整串决策。应用侧代码在"适配层阶段"与"网关阶段"之间迁移时，
    唯一变化就是 get_llm 多传一个 gateway= 参数。"""

    def __init__(self, gateway: Any, call_point: str = "reply", *,
                 tenant: str = "default", tier: str = "public") -> None:
        self.gateway, self.call_point = gateway, call_point
        self.tenant, self.tier = tenant, tier

    @property
    def model_id(self) -> str:
        return f"gateway:{self.call_point}"          # 实际模型由路由决定，逐次记录在遥测里

    def chat(self, messages: Sequence[Message], *, tools: Sequence[ToolSpec] | None = None,
             max_tokens: int = 1024, temperature: float = 0.0,
             schema: dict[str, Any] | None = None) -> Response:
        rec = self.gateway.call(self.call_point, list(messages),
                                tenant=self.tenant, tier=self.tier)   # type: ignore[attr-defined]
        if rec.get("degraded"):
            raise LLMError(f"网关降级：{rec.get('reason')}→{rec.get('action')}", status=None)
        text = rec.get("text") or ""
        return Response(text, (), Usage(rec.get("tokens", 0), 0),
                        rec.get("model") or "unknown")


def get_llm(model: str | None = None, *, mock: bool = False,
            fallback: Sequence[str] = (),
            gateway: Any | None = None, call_point: str = "reply") -> LLM:
    """demo 统一入口。

    mock=True          → MockLLM（无网络、无 key）
    mock=False         → OpenAICompatibleLLM；给了 fallback 则包一层 FailoverLLM
    model=None         → 环境变量 LLM_MODEL，否则 mock-flagship-1
    gateway=gw         → GatewayLLM：调用改道 code/gateway.py（模型池＋LLM 路由基座）
    """
    if gateway is not None:
        return GatewayLLM(gateway, call_point)
    model = model or os.environ.get("LLM_MODEL", "mock-flagship-1")
    if mock or os.environ.get("LLM_FORCE_MOCK") == "1":
        return MockLLM(model)
    primary: LLM = OpenAICompatibleLLM(model)
    if not fallback:
        return primary
    return FailoverLLM([primary, *(OpenAICompatibleLLM(m) for m in fallback)])
