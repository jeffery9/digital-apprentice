#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成《AI Agent 实战》全书插图（matplotlib，强制嵌入中文字体，杜绝中文变方框）。

关键防方框措施：
1) 显式注册系统中文 TTF/TTC 字体文件到 matplotlib 字体管理器；
2) font.sans-serif 首选 WenQuanYi Zen Hei，并关闭 findfont 回退告警前先验证；
3) axes.unicode_minus=False，避免负号变方框；
4) savefig 使用 dpi>=200 的 PNG，文字为矢量渲染后位图，不依赖 Word 端字体。
"""
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

BASE = Path(__file__).resolve().parent
FIGDIR = BASE / "figures"
FIGDIR.mkdir(exist_ok=True)

CANDIDATE_FONT_FILES = [
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "C:/Windows/Fonts/msyh.ttc",
]


def setup_cjk_font() -> str:
    """注册并返回一个确认可用的中文字体名。失败则抛异常（宁可报错也不出方框）。"""
    chosen = None
    for p in CANDIDATE_FONT_FILES:
        if Path(p).exists():
            try:
                font_manager.fontManager.addfont(p)
            except Exception as e:  # noqa
                print(f"[warn] addfont failed for {p}: {e}")
            name = Path(p).stem if not p.endswith(".ttc") else None
            # 查询真实字体族名
            try:
                f = font_manager.FontProperties(fname=p)
                chosen = f.get_name()
            except Exception:
                chosen = name
            break
    if chosen is None:
        # 遍历全部已安装字体找中文名
        for f in font_manager.fontManager.ttflist:
            if any(k in f.name for k in ("WenQuanYi", "Noto Sans CJK", "SimHei", "Microsoft YaHei", "PingFang")):
                chosen = f.name
                break
    if chosen is None:
        raise RuntimeError("未找到任何中文字体，终止生成以避免中文变方框。请安装 fonts-wqy-zenhei 或 Noto CJK。")

    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["font.sans-serif"] = [chosen, "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    print(f"[ok] CJK font in use: {chosen}")
    return chosen


FONT = setup_cjk_font()

BLUE = "#2563eb"; GREEN = "#059669"; ORANGE = "#d97706"; PURPLE = "#7c3aed"
GRAY = "#6b7280"; LIGHT = "#eff6ff"; DARK = "#1f2937"


def box(ax, x, y, w, h, text, fc=LIGHT, ec=BLUE, fs=13, weight="bold", tc=DARK, style="round,pad=0.02"):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=style,
                                fc=fc, ec=ec, lw=1.6))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
            fontsize=fs, weight=weight, color=tc, wrap=True)


def arrow(ax, x1, y1, x2, y2, color=GRAY, lw=2.2, style="-|>", connection="arc3,rad=0.0"):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle=style,
                                 mutation_scale=18, color=color, lw=lw,
                                 connectionstyle=connection))


def canvas(w=10, h=6):
    fig, ax = plt.subplots(figsize=(w, h), dpi=200)
    ax.set_xlim(0, 10); ax.set_ylim(0, 6)
    ax.axis("off")
    return fig, ax


def save(fig, name):
    out = FIGDIR / name
    fig.savefig(out, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"[saved] {out}")


# ---------------- 图1-1 能力演进阶梯 ----------------
def fig1():
    fig, ax = canvas(10, 5.6)
    steps = [("Chatbot\n单轮问答", 0.6, 1.0, GRAY),
             ("Copilot\n人主导+补全", 2.8, 1.9, BLUE),
             ("Tool-Use Agent\n函数调用", 5.0, 2.8, GREEN),
             ("Autonomous Agent\n自主规划循环", 7.2, 3.7, PURPLE)]
    for label, x, h, c in steps:
        ax.add_patch(FancyBboxPatch((x, 0.8), 2.1, h, boxstyle="round,pad=0.02",
                                    fc=c, ec="white", alpha=0.88))
        ax.text(x + 1.05, 0.8 + h / 2, label, ha="center", va="center",
                fontsize=12.5, color="white", weight="bold")
    for i in range(len(steps) - 1):
        x1 = steps[i][1] + 2.1; x2 = steps[i + 1][1]
        arrow(ax, x1, 1.4 + steps[i][2] / 2, x2, 1.4 + steps[i + 1][2] / 2, DARK)
    ax.text(5, 5.25, "从 Chatbot 到 AI Agent 的能力演进", ha="center",
            fontsize=16, weight="bold", color=DARK)
    ax.text(5, 0.35, "能力轴：记忆 · 工具 · 规划 · 自主性 逐级增强",
            ha="center", fontsize=11.5, color=GRAY)
    save(fig, "fig1_evolution.png")


# ---------------- 图2-1 五层架构 ----------------
def fig2():
    fig, ax = canvas(10, 6.4)
    layers = [("编排层 Orchestration", "循环控制 · 并行调度 · 人机协同", PURPLE),
              ("规划层 Planning", "任务分解 · 反思 · 重规划", BLUE),
              ("记忆层 Memory", "上下文 · 向量库 · 状态存储", GREEN),
              ("工具层 Tools", "函数调用 · 代码沙箱 · MCP", ORANGE),
              ("模型层 Model", "基础大模型 · 推理引擎", GRAY)]
    y = 4.9
    for name, desc, c in layers:
        ax.add_patch(FancyBboxPatch((1.2, y), 6.2, 0.86, boxstyle="round,pad=0.02",
                                    fc=c, ec="white", alpha=0.9))
        ax.text(1.5, y + 0.43, name, fontsize=13, weight="bold", color="white", va="center")
        ax.text(7.1, y + 0.43, desc, fontsize=11, color="white", va="center", ha="right")
        y -= 1.05
    arrow(ax, 8.1, 1.0, 8.1, 5.6, DARK, lw=2.6)
    ax.text(8.35, 3.3, "统一消息协议\n可观测 · 可回滚", fontsize=11, color=DARK, va="center")
    ax.text(5, 6.05, "AI Agent 五层总体架构", ha="center", fontsize=16, weight="bold", color=DARK)
    save(fig, "fig2_architecture.png")


# ---------------- 图3-1 ReAct 循环 ----------------
def fig3():
    fig, ax = canvas(8.6, 6.2)
    import math
    cx, cy, r = 4.3, 3.0, 1.9
    nodes = [("思考 Thought", BLUE, 90), ("行动 Action", GREEN, -30), ("观察 Observation", ORANGE, 210)]
    pos = {}
    for label, c, deg in nodes:
        a = math.radians(deg)
        x, y = cx + r * math.cos(a), cy + r * math.sin(a)
        pos[label] = (x, y)
        box(ax, x - 1.15, y - 0.42, 2.3, 0.84, label, fc="white", ec=c, fs=13.5)
        ax.add_patch(plt.Circle((x, y), 0.06, color=c))
    order = [n[0] for n in nodes]
    for i in range(3):
        x1, y1 = pos[order[i]]; x2, y2 = pos[order[(i + 1) % 3]]
        arrow(ax, x1, y1, x2, y2, DARK, connection="arc3,rad=-0.35")
    ax.text(cx, cy, "循环迭代\n直到给出\n最终回答", ha="center", va="center",
            fontsize=12, color=GRAY)
    box(ax, 3.15, 0.15, 2.3, 0.7, "Final Answer 输出", fc="#fef3c7", ec=ORANGE, fs=12)
    arrow(ax, pos["观察 Observation"][0] + 0.6, pos["观察 Observation"][1] - 0.5,
          4.0, 0.85, ORANGE, connection="arc3,rad=0.2")
    ax.text(4.3, 5.75, "ReAct：思考–行动–观察 工作循环", ha="center",
            fontsize=16, weight="bold", color=DARK)
    save(fig, "fig3_react_loop.png")


# ---------------- 图4-1 三层记忆 ----------------
def fig4():
    fig, ax = canvas(10, 6)
    box(ax, 0.5, 4.4, 2.6, 1.1, "工作记忆\n（上下文窗口）", fc="#dbeafe", ec=BLUE, fs=12.5)
    box(ax, 3.7, 4.4, 2.6, 1.1, "情景记忆\n（轨迹向量库）", fc="#d1fae5", ec=GREEN, fs=12.5)
    box(ax, 6.9, 4.4, 2.6, 1.1, "语义记忆\n（知识与画像）", fc="#fef3c7", ec=ORANGE, fs=12.5)
    box(ax, 3.2, 1.6, 3.6, 1.2, "Agent 核心推理\n（LLM 大脑）", fc="#ede9fe", ec=PURPLE, fs=13)
    for x in (1.8, 5.0, 8.2):
        arrow(ax, x, 4.4, 5.0, 2.8, PURPLE)
        arrow(ax, 5.0, 2.8, x, 4.4, GRAY, connection="arc3,rad=0.25")
    ax.text(2.2, 3.5, "检索回灌", fontsize=10.5, color=GRAY)
    ax.text(6.6, 3.62, "写入/沉淀", fontsize=10.5, color=GRAY)
    box(ax, 0.5, 0.2, 9.0, 0.7, "外部数据源：文档 · 数据库 · 网页 —— RAG：切块 → Embedding → 召回 → 重排 → 引用溯源",
        fc="#f3f4f6", ec=GRAY, fs=11)
    arrow(ax, 5.0, 0.9, 5.0, 1.6, GRAY)
    ax.text(5, 5.85, "Agent 三层记忆体系", ha="center", fontsize=16, weight="bold", color=DARK)
    save(fig, "fig4_memory.png")


# ---------------- 图5-1 MCP 拓扑 ----------------
def fig5():
    fig, ax = canvas(10, 6.2)
    box(ax, 3.6, 2.5, 2.8, 1.2, "MCP Host\n（Agent 应用）", fc="#ede9fe", ec=PURPLE, fs=13)
    servers = [("文件系统 Server", 0.4, 4.6, GREEN), ("数据库 Server", 3.0, 5.0, BLUE),
               ("搜索引擎 Server", 6.0, 5.0, ORANGE), ("浏览器 Server", 8.0, 4.6, GRAY),
               ("代码解释器 Server", 1.2, 0.6, GRAY), ("邮件 Server", 4.0, 0.3, BLUE),
               ("地图 Server", 6.8, 0.6, GREEN)]
    for label, x, y, c in servers:
        box(ax, x, y, 2.0, 0.75, label, fc="white", ec=c, fs=11)
        sx, sy = x + 1.0, y + (0.75 if y < 2.5 else 0)
        arrow(ax, 5.0, 3.1 if y < 2.5 else 3.7, sx, sy, c, lw=1.8)
    ax.text(5.0, 5.95, "MCP 协议：一次接入，处处复用（Host ↔ Client ↔ Server）",
            ha="center", fontsize=14.5, weight="bold", color=DARK)
    ax.text(5.0, 2.15, "Tools · Resources · Prompts", ha="center", fontsize=10, color=GRAY)
    save(fig, "fig5_mcp.png")


# ---------------- 图6-1 评估仪表盘 ----------------
def fig6():
    fig, axes = plt.subplots(1, 3, figsize=(11, 3.8), dpi=200)
    cats = ["结果层", "过程层", "成本层"]
    vals = [[82], [76, 88, 71], [65, 79]]
    colors = [BLUE, GREEN, ORANGE]
    names = [["任务成功率"], ["步数效率", "工具调用准确率", "重试率"], ["Token 预算达成", "时延达标率"]]
    bars = [[100], [100, 100, 100], [100, 100]]
    for i, axx in enumerate(axes):
        for j, v in enumerate(vals[i]):
            axx.bar([j], [100], color="#e5e7eb", width=0.55, zorder=0)
            axx.bar([j], [v], color=colors[i], width=0.55, zorder=1)
            axx.text(j, v + 3, f"{v}%", ha="center", fontsize=11, weight="bold", color=DARK)
        axx.set_xticks(range(len(vals[i])))
        axx.set_xticklabels(names[i], fontsize=9.5)
        axx.set_ylim(0, 118)
        axx.set_title(cats[i] + "指标", fontsize=13, weight="bold", color=DARK)
        axx.spines[["top", "right"]].set_visible(False)
        axx.tick_params(left=False)
    fig.suptitle("Agent 评估仪表盘示意（黄金测试集回归）", fontsize=15, weight="bold", color=DARK)
    fig.tight_layout(rect=[0, 0, 1, 0.92])
    save(fig, "fig6_eval.png")


if __name__ == "__main__":
    fig1(); fig2(); fig3(); fig4(); fig5(); fig6()
    print("All figures generated.")
    sys.exit(0)
