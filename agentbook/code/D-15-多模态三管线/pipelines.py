"""D-15 多模态三管线：同一份扫描合同，三条理解路径的成本-保真对照

对应书中：**第 13 章**（模态轴）。文档理解的三种根本管线：
  A. OCR→文本 LLM   ：便宜、快，但感知错误会**静默进入推理**（字损不可见）；
  B. 视觉检索→VLM  ：版面/印章/手写保真高，token 贵，跨页聚合弱；
  C. 混合（滑动落点）：先 A 抽取、字段级置信度低时升级 B 复核——按需付费。

本 demo 用一份脚本化"扫描合同"跑三条管线，输出字段级差异表与成本计数，
让 13.4 的"管线在哪切"变成可观察的数字。

用法：
    python pipelines.py --mock
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from llm import ImagePart, DocumentPart, system, user_multimodal   # noqa: E402
from _mockscript import ScriptedLLM                                 # noqa: E402

GROUND_TRUTH = {"甲方": "恒信贸易有限公司", "金额": "¥128,000", "签订日期": "2026-03-11"}


def score(fields: dict) -> tuple[int, int]:
    ok = sum(1 for k, v in GROUND_TRUTH.items() if fields.get(k) == v)
    return ok, len(GROUND_TRUTH)


def pipe_ocr(llm):
    """A：OCR 转文本 → 文本模型抽取。成本记 1 次文本调用；OCR 字损在此不可见。"""
    ocr = llm.chat([system("[task:ocr_extract]"),
                    user_multimodal(DocumentPart(ref="contract_scan.pdf"))])
    f = ocr.data["fields"]
    return f, {"calls": 1, "unit": "text×1", "cost_idx": 1}


def pipe_vlm(llm):
    """B：原图直接进 VLM。保真高但每页都烧图像 token。"""
    r = llm.chat([system("[task:vlm_read]"),
                  user_multimodal(ImagePart(ref="contract_scan_p1.png"))])
    f = r.data["fields"]
    return f, {"calls": 1, "unit": "image×1", "cost_idx": 4}


def pipe_hybrid(llm):
    """C：先 A 后按字段置信度升级 B——只对可疑字段付图像的钱。"""
    fa, ca = pipe_ocr(llm)
    suspect = [k for k in GROUND_TRUTH if fa.get(k) != GROUND_TRUTH[k]]
    cost = dict(ca, unit="text×1")
    if suspect:                       # 真实系统里由 OCR 置信度触发，这里用对账演示
        fb, cb = pipe_vlm(llm)
        for k in suspect:
            fa[k] = fb[k]
        cost = {"calls": 2, "unit": "text×1+image×1", "cost_idx": ca["cost_idx"] + cb["cost_idx"]}
    return fa, cost


def main() -> None:
    ap = argparse.ArgumentParser(description="D-15 多模态三管线（第 13 章）")
    ap.add_argument("--mock", action="store_true", required=True, help="离线脚本替身")
    ap.parse_args()
    llm = ScriptedLLM()
    rows = [("A OCR→文本", *pipe_ocr(llm)), ("B 视觉直读", *pipe_vlm(llm)),
            ("C 混合滑动", *pipe_hybrid(llm))]
    print(f"{'管线':<10} {'字段准确率':<8} {'成本指数':<6} 备注")
    for name, f, c in rows:
        ok, tot = score(f)
        note = "" if ok == tot else "｜错字段：" + ",".join(
            k for k in GROUND_TRUTH if f.get(k) != GROUND_TRUTH[k])
        print(f"{name:<10} {ok}/{tot:<12} {c['cost_idx']:<9} {c['unit']}{note}")
    print("\n观察什么（13.4）：A 的字损'司→可'不报错、静默通过——这是分离管线的根病；"
          "B 全对但成本指数 4；C 只为可疑字段付图像钱，以 5 以内成本换全对。"
          "落点不是站队问题，是每类文档的置信度分布问题。mock 只演示机制形状。")


if __name__ == "__main__":
    main()
