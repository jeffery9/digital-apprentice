"""_mockscript.py — D-13～D-20 共用的脚本化模型替身（零依赖，只用标准库）

与 ../llm.py 的 MockLLM 的区别：MockLLM 面向"工具循环"演示；本替身面向
**判别式调用**——按任务类型返回预置的结构化判断（分类、抽取、评审结论），
用于第 13–15、18 章的多模态管线对比与安全攻防 demo。

设计约束（策划案§十）：
  · 无网络、无 API key；输出完全由脚本决定 → 读者复现结果逐字节一致；
  · mock 只演示**机制与相对形状**，不代表任何真实模型的准确率（各 README 均免责）。
"""
from __future__ import annotations

import json
import re
from typing import Any


class ScriptedLLM:
    """按 system prompt 中的 [task:NAME] 标记分派到对应脚本处理器。"""

    def __init__(self, model_id: str = "mock-flagship-1") -> None:
        self.model_id = model_id
        self.calls = 0
        self.tokens_out = 0

    def chat(self, messages, *, tools=None, max_tokens: int = 1024,
             temperature: float = 0.0, schema: dict[str, Any] | None = None):
        from llm import Response, Usage          # 基座同级导入
        self.calls += 1
        sys_txt = next((m.text_only() for m in messages if m.role == "system"), "")
        user_txt = "\n".join(m.text_only() for m in messages if m.role == "user")
        task = (re.search(r"\[task:(\w+)\]", sys_txt) or [None, "echo"])[1]
        handler = getattr(self, f"_t_{task}", self._t_echo)
        payload = handler(user_txt)
        text = json.dumps(payload, ensure_ascii=False)
        self.tokens_out += len(text) // 2
        return Response(text, (), Usage(len(user_txt) // 2, len(text) // 2),
                        self.model_id, "stop", data=payload)

    # ── 任务脚本 ──────────────────────────────────────────────
    def _t_echo(self, u: str) -> dict:
        return {"reply": u[:60]}

    def _t_classify_intent(self, u: str) -> dict:
        kw = {"退款": "refund", "发票": "invoice", "投诉": "complaint"}
        intent = next((v for k, v in kw.items() if k in u), "general")
        return {"intent": intent, "confidence": 0.92}

    def _t_summarize(self, u: str) -> dict:
        return {"summary": u[:40].replace("\n", " ") + "……（脚本摘要）"}

    def _t_handoff_decide(self, u: str) -> dict:
        need = "退款" in u or "发票" in u
        return {"action": "handoff" if need else "answer",
                "to": "billing-agent" if need else None,
                "reason": "命中专业域关键词" if need else "通用问题可自答"}

    def _t_supervisor_route(self, u: str) -> dict:
        kw = {"退款": "billing-agent", "发票": "billing-agent", "政策": "policy-agent"}
        w = next((v for k, v in kw.items() if k in u), None)
        return {"next_agent": w or "finish", "rationale": "按注册表关键词匹配"}

    def _t_vlm_read(self, u: str) -> dict:
        return {"fields": {"甲方": "恒信贸易有限公司", "金额": "¥128,000",
                           "签订日期": "2026-03-11"}, "modality": "native-image"}

    def _t_ocr_extract(self, u: str) -> dict:
        return {"fields": {"甲方": "恒信贸易有限公可", "金额": "¥128,000",
                           "签订日期": "2026-03-11"}, "modality": "ocr-text",
                "note": "OCR 将'司'误读为'可'——分离管线的典型字损"}

    def _t_judge(self, u: str) -> dict:
        bad = "注入" in u or "IGNORE" in u.upper()
        return {"pass": not bad, "score": 0.4 if bad else 0.9,
                "violations": ["untrusted_content_leaked"] if bad else []}

    def _t_injection_probe(self, u: str) -> dict:
        attacked = "ignore previous" in u.lower() or "忽略上面" in u
        return {"executed_instruction": attacked,
                "evidence": u[-30:] if attacked else ""}

    def _t_plan_decompose(self, u: str) -> dict:
        return {"steps": ["读取来源", "抽取字段", "交叉校验", "产出报告"]}
