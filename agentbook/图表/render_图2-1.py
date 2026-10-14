#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render_图2-1.py — 五层两轴总图（全书之锚）的 SVG 渲染器
=========================================================

为什么用脚本渲染而不是手绘：QWEN.md §九要求技术图"精确制图、可再生"。
Mermaid 源（图2-1_五层两轴总图.mmd）是语义真相，本脚本是印刷级排版的实现，
两者内容必须一致；改图时先改 .mmd，再改本脚本，最后重新渲染。

用法：python3 render_图2-1.py            # 输出 图2-1_五层两轴总图.svg
黑白印刷安全：不依赖颜色区分层级与轴，仅用线宽、填充明度与文字标注。
"""
from pathlib import Path

W, H = 1680, 1050          # 画布：主图区 60–1180，右侧注记栏 1214–1650
FONT = "Noto Sans CJK SC, Source Han Sans SC, PingFang SC, Microsoft YaHei, sans-serif"
INK = "#1a1a1a"
EDGE = "#33475b"
FILL_LAYER = "#ffffff"
FILL_AXIS = "#eef2f7"
FILL_NOTE = "#f7f9fb"

L, R = 60, 1180                     # 主图区左右边界
BOX_W = R - L


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def rect(x, y, w, h, fill, sw=1.4, dash=None, rx=6):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" '
            f'fill="{fill}" stroke="{EDGE}" stroke-width="{sw}"{d}/>')


def text(x, y, s, size=15, weight="normal", anchor="start", fill=INK, ls=0):
    sp = f' letter-spacing="{ls}"' if ls else ""
    return (f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" '
            f'font-weight="{weight}" text-anchor="{anchor}" fill="{fill}"{sp}>{esc(s)}</text>')


def layer(y, h, tag, title, body, chap, sw=1.4):
    """一个层：左侧竖排层号 + 标题 + 内容 + 右侧章节映射。"""
    out = [rect(L, y, BOX_W, h, FILL_LAYER, sw)]
    out.append(rect(L, y, 92, h, FILL_AXIS, sw))
    cx = L + 46
    for i, ch in enumerate(tag):                       # 竖排层号 L5 / L1a …
        out.append(text(cx, y + h / 2 - (len(tag) - 1) * 11 + i * 22 + 6, ch,
                        17, "bold", "middle"))
    out.append(text(L + 112, y + 31, title, 17, "bold"))
    for i, line in enumerate(body):
        out.append(text(L + 112, y + 57 + i * 23, line, 14.5))
    out.append(text(R - 16, y + 31, chap, 13.5, "normal", "end", "#44566b"))
    return "\n".join(out)


parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
         f'viewBox="0 0 {W} {H}" font-family="{FONT}">',
         f'<rect width="{W}" height="{H}" fill="#ffffff"/>']

# ── 标题与声明 ─────────────────────────────────────────────
parts.append(text(L, 34, "图 2-1　五层参考架构 × 两条横切轴（全书之锚）", 22, "bold"))
parts.append(text(L, 60, "本图为本书综合提出的组织方式，不是业界标准。它的价值不在于"
                         "正确，而在于提供一套可以指认问题位置的词汇。", 13.5, fill="#44566b"))

# ── 两条横切轴（画在层的上方，用引线表明"贯穿每一层"）──────
AY = 82
for i, (name, dim, items, marks) in enumerate([
    ("模态轴", "空间维度：信息以什么形式进出",
     "文本 · 图像 · 文档 · 音频 · 视频 · 屏幕",
     "L1a 支持哪些模态｜L2 非文本怎么表示与计价｜L3 感知管线与屏幕动作循环｜L5 多模态评估与注入防御"),
    ("时程轴", "时间维度：任务活多久",
     "秒级对话 · 分钟级任务 · 小时级流程 · 天级后台",
     "L2 跨天后的上下文重建｜L4 持久执行与状态外置｜L5 跨天预算与进度契约"),
]):
    y = AY + i * 96
    parts.append(rect(L, y, BOX_W, 84, FILL_AXIS, 1.2, dash="6 4"))
    parts.append(text(L + 18, y + 27, f"{name}（横切）", 16.5, "bold"))
    parts.append(text(L + 18, y + 49, dim, 13.5, fill="#44566b"))
    parts.append(text(L + 18, y + 72, items, 15, "bold"))
    parts.append(text(L + 430, y + 27, "在每一层留下的痕迹：", 13.5, fill="#44566b"))
    parts.append(text(L + 430, y + 50, marks[:46], 13.5))
    if len(marks) > 46:
        parts.append(text(L + 430, y + 71, marks[52:], 13.5))

# 轴 → 层 的贯穿引线
for x in (L + 150, L + 560, L + 970):
    parts.append(f'<line x1="{x}" y1="{AY + 2 * 96 - 12}" x2="{x}" y2="{AY + 214}" '
                 f'stroke="{EDGE}" stroke-width="1" stroke-dasharray="3 4" opacity="0.55"/>')
    parts.append(f'<path d="M {x - 5} {AY + 208} L {x + 5} {AY + 208} L {x} {AY + 218} Z" '
                 f'fill="{EDGE}" opacity="0.55"/>')

# ── 五层栈 ────────────────────────────────────────────────
SY = AY + 226                                          # 栈顶 y
parts.append(layer(SY, 78, "L5", "治理层",
                   ["安全 · 评估 · 可观测 · 成本 · 合规 · 跨天预算与进度契约"],
                   "第三部｜第 17–19 章"))
parts.append(layer(SY + 88, 78, "L4", "编排层",
                   ["循环 · 状态机 · 多 agent 拓扑 · 协议 · 持久执行"],
                   "第 10–12、16 章"))
parts.append(layer(SY + 176, 78, "L3", "能力层",
                   ["工具 · 执行环境 · 沙箱 · 感知管线 · computer use"],
                   "第 7、14、15 章"))
parts.append(layer(SY + 264, 90, "L2", "上下文层",
                   ["工作上下文 · 记忆 · 检索 · 压缩 · 模态预算 · 上下文重建",
                    "唯一状态载体：装不进窗口的东西，就等于不存在"],
                   "第 6、8、16 章"))

# L1 容器（内含 a/b 两半）
L1Y = SY + 364
parts.append(rect(L, L1Y, BOX_W, 214, FILL_LAYER, 1.8))
parts.append(rect(L, L1Y, 92, 214, FILL_AXIS, 1.8))
parts.append(text(L + 46, L1Y + 112, "L1", 19, "bold", "middle"))
parts.append(text(L + 112, L1Y + 28, "模型层", 17, "bold"))
parts.append(text(L + 200, L1Y + 28,
                  "内部分 a/b 两半——这一刀切的是“变化频率与治理主体”，不是技术分工",
                  13.5, fill="#44566b"))
parts.append(rect(L + 112, L1Y + 44, BOX_W - 132, 74, FILL_NOTE, 1.1))
parts.append(text(L + 128, L1Y + 68, "L1a　模型能力", 15.5, "bold"))
parts.append(text(L + 128, L1Y + 90, "推理 · 工具调用机制 · 上下文窗口 · 模态支持", 14))
parts.append(text(L + 128, L1Y + 110, "随模型代际变化｜由技术团队复核", 13, fill="#44566b"))
parts.append(text(R - 16, L1Y + 68, "第 3 章", 13.5, "normal", "end", "#44566b"))
parts.append(rect(L + 112, L1Y + 126, BOX_W - 132, 74, FILL_NOTE, 1.1))
parts.append(text(L + 128, L1Y + 150, "L1b　模型接入", 15.5, "bold"))
parts.append(text(L + 128, L1Y + 172, "网关 · 能力画像 · 路由 · 级联 · 故障转移 · 数据驻留", 14))
parts.append(text(L + 128, L1Y + 192, "随组织结构与供应商关系变化｜由平台团队与采购共同治理", 13, fill="#44566b"))
parts.append(text(R - 16, L1Y + 150, "第 4 章", 13.5, "normal", "end", "#44566b"))

# ── 右侧：四个放大方向 + 三个建模自辩 ──────────────────────
NX = R + 34
parts.append(rect(NX, AY, W - NX - 30, 268, FILL_NOTE, 1.1))
parts.append(text(NX + 18, AY + 30, "总图的四个放大方向", 16, "bold"))
for i, s in enumerate([
    "① L1a → 第 3 章：模型能力如何制约架构",
    "② L1b → 第 4 章：模型池、网关与能力路由",
    "③ 模态轴 → 第 13–14 章：多模态架构（上下）",
    "④ 时程轴 → 第 16 章：长时程与后台任务",
]):
    parts.append(text(NX + 18, AY + 62 + i * 27, s, 14))
parts.append(text(NX + 18, AY + 182, "后续每章都是这张图的一块放大；", 13.5, fill="#44566b"))
parts.append(text(NX + 18, AY + 205, "读到任何一章迷路时，回到这张图定位。", 13.5, fill="#44566b"))
parts.append(text(NX + 18, AY + 240, "（放大方向与章节映射由 prepub C4 校验）", 12.5, fill="#7a8899"))

parts.append(rect(NX, AY + 288, W - NX - 30, 300, FILL_NOTE, 1.1))
parts.append(text(NX + 18, AY + 318, "三个建模选择的自辩（第 2.6 节）", 15.5, "bold"))
for i, (q, a) in enumerate([
    ("为什么仍是五层，不加第六、第七层？",
     ["避免动摇“全书之锚”，也避免读者", "把它误当成又一套新框架。"]),
    ("为什么 L1 要切 a/b？",
     ["两半的变化频率与治理主体不同：a 随", "模型代际变，b 随组织与供应商关系变。"]),
    ("为什么模态与时程是轴不是层？",
     ["它们在每一层都留下痕迹。画成层会误导", "读者以为“多模态／长时程是一个可以", f"外包的模块”，实际是贯穿性约束。贯穿性约束。"]),
]):
    yy = AY + 348 + i * 78
    parts.append(text(NX + 18, yy, q, 13.5, "bold"))
    for j, ln in enumerate(a):
        parts.append(text(NX + 30, yy + 21 + j * 19, ln, 13, fill="#44566b"))

# ── 底部：体检用法 ────────────────────────────────────────
BY = L1Y + 236
parts.append(rect(L, BY, W - L - 30, 118, FILL_LAYER, 1.2, dash="6 4"))
parts.append(text(L + 18, BY + 28, "用法：这张图是词汇表和检查清单，不是必须照抄的蓝图（第 2.7 节）", 15.5, "bold"))
parts.append(text(L + 18, BY + 54,
                  "给存量系统做体检时，逐层问“这一层的决策由谁做、依据什么、失败会怎样”，再沿两条轴问三个新增体检位：",
                  14))
parts.append(text(L + 30, BY + 80,
                  "▸ 模型接入是否收口（L1b）　　▸ 非文本模态是否有预算（模态轴 × L2）　　▸ 任务是否有时程上限（时程轴 × L4/L5）",
                  14, "bold"))
parts.append(text(L + 18, BY + 104,
                  "三个体检位是 2026 年存量 agent 系统最普遍的三处缺口；对应的处方分别在第 4、13、16 章。",
                  13, fill="#44566b"))

parts.append("</svg>")

out = Path(__file__).with_name("图2-1_五层两轴总图.svg")
out.write_text("\n".join(parts), encoding="utf-8")
print(f"✓ 渲染完成：{out.name}（{out.stat().st_size:,} bytes，{W}×{H}）")
