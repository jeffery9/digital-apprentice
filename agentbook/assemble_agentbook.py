#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
《AI Agent 架构决策指南》全书稿组装与 docx 转换脚本
=====================================================

改造自《数字见习生》assemble_book.py（v0.8l，已在 24 文件 / 21 万字 / 97 表 / 61 图规模上验证），
按本书体例调整：20 章三部结构、附录 A–E、图表源为 Mermaid（渲染前的图位块允许缺图）。

功能：
  1. 按成书顺序组装 书稿/ 下的 markdown（前言 + 三部扉页 + 20 章 + 附录 A–E + 后记），
     插入部扉页（H1 部名），清理草稿注释行，重写图片相对路径，
     输出单一合并版 markdown：输出/《AI Agent 架构决策指南》全书稿_合并版.md
  2. 定制中文排版样式模板（reference.docx：宋体正文/黑体标题/楷体引用块、A4、1.5 倍行距、
     一级标题自动另起页），经 pandoc 转换为 docx。
  3. 转换后对 docx 做出版级后处理：表格加边框并按版心等比重排、图与图题居中、
     超宽插图缩至版心宽、目录页另起页并题头改"目　录"、页脚居中页码、文档元数据。

写作期约定（与《数字见习生》的差异）：
  · **尚未写就的书稿文件默认跳过并告警**（增量写作期用），--strict 时改为报错中止。
  · **图位块**：正文以 `> 【图 X-Y｜图题】… ｜源文件：图表/….mmd` 形式占位，
    Mermaid 渲染成 SVG 后再改为真实图片引用；两种形态组装脚本都接受。
  · 源稿引号若为 ASCII 直引号，按行配对规范化为中文弯引号（pandoc 禁用 -smart）。

用法：
  python3 assemble_agentbook.py                # 默认：跳过缺文件、清理草稿注释、弯引号、带静态目录
  python3 assemble_agentbook.py --strict       # 缺书稿文件即报错（付印前用）
  python3 assemble_agentbook.py --final        # 追加清理设计师「排版说明」块（送印构建）
  python3 assemble_agentbook.py --keep-notes   # 保留草稿注释行
  python3 assemble_agentbook.py --no-cn-quotes # 保留 ASCII 直引号
  python3 assemble_agentbook.py --no-toc       # 不生成目录页
  python3 assemble_agentbook.py --toc-depth N  # 目录收录到 N 级标题（默认 2）
  python3 assemble_agentbook.py --outdir DIR   # 自定义输出目录（默认 agentbook/输出/）

依赖：python3、pypandoc-binary（自带 pandoc）、python-docx、pillow、cairosvg
     （cairosvg 可选：缺用时 SVG→PNG 走 pandoc 回退。Word 的 SVG 渲染引擎不支持
     中文字体回退，插图 SVG 在构建时预栅格化为 300DPI PNG，见 svg_to_png）
"""

import argparse
import datetime
import os
import re
import shutil
import sys
import tempfile

import pypandoc
from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Emu, Pt, RGBColor

# ----------------------------------------------------------------------------
# 路径与常量
# ----------------------------------------------------------------------------
BOOK_DIR   = os.path.dirname(os.path.abspath(__file__))          # 项目根（agentbook）
SRC_DIR    = os.path.join(BOOK_DIR, '书稿')                       # 书稿源目录（只读）
FIG_DIR    = os.path.join(BOOK_DIR, '图表')                       # Mermaid 源 + SVG 导出
VERSION    = 'v0.5'                                               # 与 书稿/00_进度表.md 同步
BOOK_TITLE = 'AI Agent 架构决策指南'



# 成书顺序：('file', 文件名) 或 ('part', 部标题, 扉页图文件名或 None)
# 增量写作期：尚未写就的文件默认跳过并告警（--strict 时报错）。
BOOK_ORDER = [
    ('file', '前言.md'),
    ('part', '第一部　认知与基础（第 1–4 章）', None),
    ('file', '第01章.md'),
    ('file', '第02章.md'),
    ('file', '第03章.md'),
    ('file', '第04章.md'),
    ('part', '第二部　设计模式深潜：九个模式族（第 5–14 章）', None),
    ('file', '第05章.md'),
    ('file', '第06章.md'),
    ('file', '第07章.md'),
    ('file', '第08章.md'),
    ('file', '第09章.md'),
    ('file', '第10章.md'),
    ('file', '第11章.md'),
    ('file', '第12章.md'),
    ('file', '第13章.md'),
    ('file', '第14章.md'),
    ('part', '第三部　生产与决策（第 15–20 章）', None),
    ('file', '第15章.md'),
    ('file', '第16章.md'),
    ('file', '第17章.md'),
    ('file', '第18章.md'),
    ('file', '第19章.md'),
    ('file', '第20章.md'),
    ('file', '附录A_设计模式速查目录.md'),
    ('file', '附录B_术语表.md'),
    ('file', '附录C_架构师工具箱.md'),
    ('file', '附录D_时效说明与版本锚点表.md'),
    ('file', '附录E_延伸阅读.md'),
    ('file', '后记.md'),
]

# 章文件的特殊预处理规则：{文件名: 从哪一行开始保留（该行本身保留）}
# 第 7 章文件首部有一个"第二篇使用指南 + 第 7 章"的元标题与状态块，属于写作期脚手架，
# 成书组装时从真正的"# 第二篇使用指南"起保留。
# 章文件特殊预处理规则：{文件名: 从哪一行开始保留}（本书暂无）
SPECIAL_RULES = {}

# 草稿注释行的识别标记（仅对引用块 "> " 行生效；--keep-notes 时全部保留）
DRAFT_NOTE_MARKERS = (
    '书稿状态', '本章图表：', '排版与标注说明', '标注位说明',
    '设计师用', '定稿前移除', '目标字数',
)
# 定稿构建（--final）才追加清理的标记：设计师专用「排版说明」引用块。
# 审阅稿默认保留（设计师排版 45 张表时要用）；送印前用 --final 构建即自动移除。
FINAL_NOTE_MARKERS = ('排版说明',)

# 版心（A4 21cm − 左右边距 2.8cm×2 = 15.4cm）
PAGE_W_CM, PAGE_H_CM = 21.0, 29.7
MARGIN_TB_CM, MARGIN_LR_CM = 2.54, 2.8
TEXT_W_CM = PAGE_W_CM - 2 * MARGIN_LR_CM          # 15.4
TEXT_W_TWIPS = int(TEXT_W_CM * 567)               # twip = 1/20 pt，1cm ≈ 567 twip
IMG_CAP_CM = 15.0                                  # 插图宽度上限（略小于版心）

# 中文字体方案
F_BODY_EA,  F_BODY_ASCII  = '宋体', 'Times New Roman'   # 正文
F_HEAD_EA,  F_HEAD_ASCII  = '黑体', 'Arial'             # 标题
F_QUOTE_EA, F_QUOTE_ASCII = '楷体', 'Times New Roman'   # 引用块（手册/模板/设计师注）
BLACK = RGBColor(0, 0, 0)
GRAY  = RGBColor(0x59, 0x59, 0x59)


def log(msg):
    print(msg, flush=True)


# ----------------------------------------------------------------------------
# 第一步：组装 markdown
# ----------------------------------------------------------------------------
def strip_draft_notes(text, final=False):
    """删除引用块中的草稿注释行，返回 (清理后文本, 删除行数)。
    final=True 时追加清理设计师「排版说明」块（FINAL_NOTE_MARKERS）——定稿构建用。"""
    markers = DRAFT_NOTE_MARKERS + FINAL_NOTE_MARKERS if final else DRAFT_NOTE_MARKERS
    kept, removed = [], 0
    for line in text.splitlines():
        s = line.lstrip()
        if s.startswith('>') and any(m in s for m in markers):
            removed += 1
            continue
        kept.append(line)
    # 压缩因删行产生的 3+ 连续空行
    out = re.sub(r'\n{4,}', '\n\n\n', '\n'.join(kept))
    return out.strip('\n'), removed


def apply_special_rule(filename, text):
    """按 SPECIAL_RULES 截去章首脚手架（如第 7 章的组合元标题）。"""
    marker = SPECIAL_RULES.get(filename)
    if not marker:
        return text
    idx = text.find('\n' + marker)
    if idx == -1 and text.startswith(marker):
        return text
    if idx == -1:
        raise RuntimeError(f'{filename}: 未找到特殊规则锚点 "{marker}"，请人工检查')
    return text[idx + 1:].strip('\n')


def normalize_cn_quotes(text):
    """
    把成对的 ASCII 直引号规范化为中文弯引号：" → “/”，' → ‘/’。
    按行独立配对（本书段落均为单行，引号不跨行）；
    某行内某种引号出现奇数次则整行跳过并计入告警，避免张冠李戴。
    返回 (转换后文本, 转换行数, 跳过的行列表)。
    """
    out, n_conv, skipped = [], 0, []
    for line in text.splitlines():
        new = line
        ok = True
        for ch, (lo, hi) in {'"': ('\u201c', '\u201d'), "'": ('\u2018', '\u2019')}.items():
            if new.count(ch) % 2 == 1:
                ok = False
                break
        if ok and ('"' in new or "'" in new):
            def _pair(s, ch, lo, hi):
                res, open_ = [], True
                for c in s:
                    if c == ch:
                        res.append(lo if open_ else hi)
                        open_ = not open_
                    else:
                        res.append(c)
                return ''.join(res)
            new = _pair(new, '"', '\u201c', '\u201d')
            new = _pair(new, "'", '\u2018', '\u2019')
            if new != line:
                n_conv += 1
        elif not ok and ('"' in line or "'" in line):
            skipped.append(line.strip()[:36])
        out.append(new)
    return '\n'.join(out), n_conv, skipped


# 单个图位块行：> 【图 X-Y｜图题】导读…｜源文件：图表/….mmd
# （兼容第 8/9 章"渲染源："变体与无"｜"直接衔接的写法；逐行匹配，勿用 DOTALL——
#   否则同一引用块内多个 > 续行会被贪婪合并成一次匹配而漏嵌。）
FIG_LINE_RE = re.compile(
    r'^[ \t]*(?:>[ \t]*)?【图[ \t]*(\d+-\d+)[|｜]([^】\n]+)】(.+?)'
    r'(?:[|｜][ \t]*)?(?:源文件|渲染源)[：:]'
    r'[ \t]*(图表/[^\s|｜）)]+\.mmd)[ \t]*$', re.M)


def embed_figures(text, fig_dir, rel_prefix):
    """把图位块接线为真实图片引用：【图 X-Y…】→ ![..](../图表/….svg)＋图注行。

    规则（M7 图表嵌入，2026-10-15）：
      · 逐行匹配 `> 【图 X-Y｜图题】…｜源文件：图表/….mmd`（含第 8/9 章
        "渲染源："变体、以及第 8/9 章多块共享一个引用块的 `>` 续行形态）；
      · 派生同名 .svg，**文件存在才接线**；缺 SVG 时保留原图位块并计数告警
        （维持"渲染前允许缺图"的既有约定）；
      · 成图为三行：图片引用在前、空行、▲ 图注在后（图注保留 mmd 溯源，
        B4 检查不破）；引用块内部的 `>` 续行块自动拆为独立图组。
    返回 (新文本, 嵌入数, 未嵌入清单)。
    """
    embedded, pending = [], []
    seen = set()

    def _repl(m):
        figno, title, lead, src = (m.group(1), m.group(2).strip(),
                                   m.group(3), m.group(4))
        svg_name = os.path.basename(src)[:-len('.mmd')] + '.svg'
        if not os.path.isfile(os.path.join(fig_dir, svg_name)):
            pending.append(f'{figno}（缺 {svg_name}）')
            return m.group(0)
        if figno in seen:                      # 同编号只嵌一次，其余保留原样
            pending.append(f'{figno}（重复图位块）')
            return m.group(0)
        seen.add(figno)
        title_clean = title.replace('（全书之锚）', '').strip()
        lead = lead.strip()
        if lead:
            lead = lead.rstrip('。') + '。'
        cap = (f'> ▲ 图 {figno}｜{title}｜{lead}'
               f'源文件：{src}（Mermaid 源码可 diff 可再生，出版前交设计重绘）')
        embedded.append(figno)
        return (f'![图 {figno} {title_clean}]({rel_prefix}{svg_name})'
                f'\n\n{cap}')

    out = FIG_LINE_RE.sub(_repl, text)
    # 共享引用块（第 8/9 章 `>` 续行形态）拆块后残留的孤立 `>` 空引用行：清除
    out = re.sub(r'\n[ \t]*>[ \t]*(?=\n[ \t]*!\[图 )', '\n', out)
    return out, len(embedded), pending


def rewrite_image_paths(text, old_prefix, new_prefix):
    """重写图片相对路径：](插画/x.png) → ](new/x.png)。"""
    return text.replace(f']({old_prefix}', f']({new_prefix}')


def _is_table_delim(line):
    """判断是否为管道表分隔行，如 |---|---| 或 | :-- | :-: |。"""
    s = line.strip()
    if not s or '|' not in s or '-' not in s:
        return False
    return not (set(s) - set('|-: '))


def ensure_table_blank_lines(text):
    """保证管道表块前后有空行（pandoc 要求表格独立成块）。

    若表题行（如【表 1-1｜…】）或正文行与表体紧贴，pandoc 会把整张表
    当成普通段落，管道符原样输出。返回 (规范化文本, 补空行数)。
    """
    lines = text.split('\n')
    out = []
    n = len(lines)
    i = 0
    fixed = 0
    while i < n:
        line = lines[i]
        if line.lstrip().startswith('|') and i + 1 < n and _is_table_delim(lines[i + 1]):
            if out and out[-1].strip():
                out.append('')
                fixed += 1
            while i < n and lines[i].lstrip().startswith('|'):
                out.append(lines[i])
                i += 1
            if i < n and lines[i].strip():
                out.append('')
                fixed += 1
            continue
        out.append(line)
        i += 1
    return '\n'.join(out), fixed


STRICT = False          # --strict 时置 True：缺书稿文件即报错


def assemble_markdown(outdir, keep_notes, cn_quotes=True, final=False):
    """组装全书 markdown，返回 (合并md路径, 统计字典)。"""
    today = datetime.date.today().isoformat()
    parts = []

    # YAML 元数据（书名页 + 目录题头）
    parts.append('---\n'
                 'title: AI Agent 架构决策指南\n'
                 f'subtitle: 全书合并审阅版 · {VERSION} · {today}\n'
                 'lang: zh-CN\n'
                 '---\n')

    stats = {'files': 0, 'notes_removed': 0, 'images': 0, 'part_pages': 0,
             'quotes_converted': 0, 'quotes_skipped': [], 'tables_spaced': 0,
             'figures_embedded': 0, 'figures_pending': []}
    rel_illust = '../图表/'               # 合并 md 位于 输出/，图在 图表/

    for item in BOOK_ORDER:
        if item[0] == 'part':
            _, part_title, cover_png = item
            if cover_png:
                parts.append(f'# {part_title}\n\n'
                             f'![{part_title.split("　")[0]}扉页]({rel_illust}{cover_png})\n')
            else:
                parts.append(f'# {part_title}\n')
            stats['part_pages'] += 1
        else:
            fname = item[1]
            path = os.path.join(SRC_DIR, fname)
            if not os.path.isfile(path):
                if STRICT:
                    raise RuntimeError(f'缺少书稿文件：{path}')
                stats.setdefault('missing', []).append(fname)
                log(f'  · 跳过（尚未写就）：{fname}')
                continue
            with open(path, encoding='utf-8') as f:
                text = f.read()
            text = apply_special_rule(fname, text)
            if not keep_notes:
                text, n = strip_draft_notes(text, final)
                stats['notes_removed'] += n
                # 章首状态块已删：连同紧随章名的脚手架分隔线一并移除
                text = re.sub(r'\A(# [^\n]+\n)\n+---+\n', r'\1\n', text)
            if cn_quotes:
                text, nq, skipped = normalize_cn_quotes(text)
                stats['quotes_converted'] += nq
                stats['quotes_skipped'] += skipped
            text = rewrite_image_paths(text, '../图表/', rel_illust)
            # M7 图表嵌入：图位块 → 真实 SVG 引用（缺 SVG 保留占位并告警）
            text, n_fig, pend = embed_figures(text, FIG_DIR, rel_illust)
            stats['figures_embedded'] += n_fig
            stats['figures_pending'] += [(fname, p) for p in pend]
            parts.append(text.strip('\n') + '\n')
            stats['files'] += 1

    merged = '\n\n'.join(p.strip('\n') for p in parts) + '\n'

    # 表格块边界规范化：表题/正文与表体紧贴时补空行，否则 pandoc 不认表格
    merged, n_tbl = ensure_table_blank_lines(merged)
    stats['tables_spaced'] = n_tbl
    if n_tbl:
        log(f'  ✓ 表格块边界规范化：补空行 {n_tbl} 处')

    # 图引用完整性校验（只统计真实引用行，排除示例/说明行）
    img_refs = re.findall(r'^!\[[^\]]*\]\(([^)]+)\)', merged, flags=re.M)
    stats['images'] = len(img_refs)
    missing = [p for p in img_refs if not os.path.isfile(os.path.join(outdir, p))]
    if missing:
        raise RuntimeError('以下图片引用无法解析：\n  ' + '\n  '.join(missing))

    # setext 标题隐患检查：非空文本行后紧跟 ---（会被解析成二级标题而非分隔线）
    # 跳过文件头部的 YAML 元数据块（其结束标记 --- 是合法语法）
    body_text = re.sub(r'\A---\n.*?\n---\n', '', merged, flags=re.S)
    risky = re.findall(r'^(?!#)(\S.*?)\n(---+)\s*$', body_text, flags=re.M)
    risky = [(a, b) for a, b in risky if not a.lstrip().startswith(('|', '>', '-'))]
    if risky:
        log(f'  ! 警告：发现 {len(risky)} 处"文本行紧跟 ---"（将按分隔线语义人工复核）：')
        for a, _ in risky[:5]:
            log(f'    · {a[:40]}…')

    md_path = os.path.join(outdir, f'《{BOOK_TITLE}》全书稿_合并版.md')
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(merged)
    stats['chars'] = len(merged)
    return md_path, stats


# ----------------------------------------------------------------------------
# 第二步：图片预处理（审阅用副本，源图不动）
#   · SVG → 300 DPI PNG 预栅格化（Word 对 SVG 的中文支持缺失，见函数文档）
#   · 位图 → 长边限 1600px 压缩副本
# ----------------------------------------------------------------------------
# 注意：顺序即优先级。Pango（cairosvg 底层）的字体回退不可靠——若把无中文字形的
# 西文字体（如 DejaVu Sans，fc-match 对 "Microsoft YaHei"/"PingFang SC" 的兜底）
# 排在前面，中文会被渲染成 .notdef 空心方框（docx 插图中文变方框的直接根因）。
# 因此构建端必须把本机真实存在的中文字体置于链首；末尾仍保留 Word 端友好名称。
def _fc_zh_families():
    """返回本机 fontconfig 认可"含中文字形"的字体家族名集合。"""
    import subprocess
    try:
def _fc_zh_families():
    """返回本机 fontconfig 认可"含中文字形"的字体家族名集合。"""
    import subprocess
    try:
        out = subprocess.run(['fc-list', ':lang=zh', 'family'],
                             capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return set()
    fams = set()
    for line in out.splitlines():
        for f in line.split(','):
            f = f.strip()
            if f:
                fams.add(f)
    return fams


def _fc_has_zh(family):
    """校验 family 是否真能取到中文字形：fc-match 解析结果的家族名必须出现在
    fc-list :lang=zh 输出里。直接 fc-match 'Microsoft YaHei' 会静默兜底到
    DejaVu Sans（无中文字形）——这正是"链首写了中文族名却仍出方框/丢字"的根因，
    因此探测必须以"解析结果是否真的含中文字形"为准，而非族名写得像不像中文。"""
    import subprocess
    try:
        fam = subprocess.run(['fc-match', '-f', '%{family[0]}', family],
                             capture_output=True, text=True,
                             timeout=10).stdout.split(',')[0].strip()
        return fam if fam in _fc_zh_families() else None
    except Exception:
        return None


def _build_cjk_font():
    """返回构建栅格化时使用的 CJK 字体族名（本机真实有中文字形的第一个）。

    候选顺序：优先无衬线黑体族（与图面风格一致），逐一经 _fc_has_zh 实证；
    全部落空才退回 fc-match sans-serif 的结果（若其恰好含中文字形）。
    """
    for cand in ('Noto Sans CJK SC', 'Source Han Sans SC', 'WenQuanYi Zen Hei',
                 'Microsoft YaHei', 'PingFang SC', 'SimHei', '宋体'):
        fam = _fc_has_zh(cand)
        if fam:
            return fam
    import subprocess
    try:
        fam = subprocess.run(['fc-match', '-f', '%{family[0]}', 'sans-serif'],
                             capture_output=True, text=True,
                             timeout=10).stdout.split(',')[0].strip()
        return fam if fam in _fc_zh_families() else None
    except Exception:
        return None
        return set()
    fams = set()
    for line in out.splitlines():
        for f in line.split(','):
            f = f.strip()
            if f:
                fams.add(f)
    return fams


def _fc_has_zh(family):
    """校验 family 是否真能取到中文字形：fc-match 解析结果的家族名必须出现在
    fc-list :lang=zh 输出里。直接 fc-match 'Microsoft YaHei' 会静默兜底到
    DejaVu Sans（无中文字形）——这正是"链首写了中文族名却仍出方框/丢字"的根因，
    因此探测必须以"解析结果是否真的含中文字形"为准，而非族名写得像不像中文。"""
    import subprocess
    try:
        fam = subprocess.run(['fc-match', '-f', '%{family[0]}', family],
                             capture_output=True, text=True,
                             timeout=10).stdout.split(',')[0].strip()
        return fam if fam in _fc_zh_families() else None
    except Exception:
        return None


def _build_cjk_font():
    """返回构建栅格化时使用的 CJK 字体族名（本机真实有中文字形的第一个）。

    候选顺序：优先无衬线黑体族（与图面风格一致），逐一经 _fc_has_zh 实证；
    全部落空才退回 fc-match sans-serif 的结果（若其恰好含中文字形）。
    """
    for cand in ('Noto Sans CJK SC', 'Source Han Sans SC', 'WenQuanYi Zen Hei',
                 'Microsoft YaHei', 'PingFang SC', 'SimHei', '宋体'):
        fam = _fc_has_zh(cand)
        if fam:
            return fam
    import subprocess
    try:
        fam = subprocess.run(['fc-match', '-f', '%{family[0]}', 'sans-serif'],
                             capture_output=True, text=True,
                             timeout=10).stdout.split(',')[0].strip()
        return fam if fam in _fc_zh_families() else None
    except Exception:
        return None


# 回退链首项必须是**本机真实存在**的中文字体（动态探测）：若以 sans-serif 或
# 不存在的 Noto 开头，Pango 会落到无中文字形的西文字体 → .notdef 空心方框。
# 末尾仍保留 Word 端友好名称，供 docx 直通场景使用。
_ZH_HEAD = _build_cjk_font()
# 去重拼接：本机探测到的首项若已在默认链中出现，则不重复追加。
# Word 端友好名（YaHei/PingFang/SimHei/宋体）保留在链尾作直通场景兜底；
# 构建端真正取字形的是链首——由 svg_to_png 的方框检测保证不会静默放行。
# 去重拼接：本机探测到的首项若已在默认链中出现，则不重复追加。
# Word 端友好名（YaHei/PingFang/SimHei/宋体）保留在链尾作直通场景兜底；
# 构建端真正取字形的是链首——由 svg_to_png 的方框检测保证不会静默放行。
_DEFAULT_CHAIN = ('Noto Sans CJK SC,Source Han Sans SC,'
                  'Microsoft YaHei,PingFang SC,'
                  'WenQuanYi Zen Hei,SimHei,宋体')
SVG_CJK_FALLBACK = (_ZH_HEAD + ',' + _DEFAULT_CHAIN
                    if _ZH_HEAD and _ZH_HEAD not in _DEFAULT_CHAIN.split(',')
                    else _DEFAULT_CHAIN)

SVG_BUILD_CJK_FONT = _ZH_HEAD


def _ensure_zh_font_assets():
    """构建端中文字形兜底：fontconfig 查无含中文字形的可解析族时，拉取
    Noto Sans CJK SC 注册进 ~/.fonts 并重建字体缓存，随后刷新模块内的字体链
    全局量。保证 SVG→PNG 栅格化永远有中文字形可用——既不空白也不方框。"""
    global _ZH_HEAD, SVG_CJK_FALLBACK, SVG_BUILD_CJK_FONT
    if _build_cjk_font():
        return True
    import subprocess
    import urllib.request
    urls = (
        'https://github.com/notofonts/noto-cjk/raw/main/Sans/OTF/'
        'SimplifiedChinese/NotoSansCJKsc-Regular.otf',
        'https://cdn.jsdelivr.net/gh/notofonts/noto-cjk@main/Sans/OTF/'
        'SimplifiedChinese/NotoSansCJKsc-Regular.otf',
    )
    fdir = os.path.join(os.path.expanduser('~'), '.fonts')
    os.makedirs(fdir, exist_ok=True)
    dst = os.path.join(fdir, 'NotoSansCJKsc-Regular.otf')
    for u in urls:
        try:
            log(f'  · 本机无中文字体，拉取 Noto Sans CJK SC：{u[:60]}…')
            req = urllib.request.Request(u, headers={'User-Agent': 'curl/8'})
            with urllib.request.urlopen(req, timeout=180) as r, \
                    open(dst, 'wb') as f:
                shutil.copyfileobj(r, f)
            if os.path.getsize(dst) > 1_000_000:
                subprocess.run(['fc-cache', '-f', fdir],
                               capture_output=True, timeout=60)
                # 刷新模块级字体链（此前探测为空）
                _ZH_HEAD = _build_cjk_font()
                SVG_BUILD_CJK_FONT = _ZH_HEAD
                SVG_CJK_FALLBACK = ((_ZH_HEAD + ',' + _DEFAULT_CHAIN)
                                    if _ZH_HEAD else _DEFAULT_CHAIN)
                if _ZH_HEAD:
                    log(f'  ✓ 中文字形兜底就绪：{_ZH_HEAD}')
                    return True
            os.remove(dst)
        except Exception as e:
            log(f'    ! 字体下载失败：{e}')
    return False


def _ensure_zh_font_assets():
    """构建端中文字形兜底：fontconfig 查无含中文字形的可解析族时，拉取
    Noto Sans CJK SC 注册进 ~/.fonts 并重建字体缓存，随后刷新模块内的字体链
    全局量。保证 SVG→PNG 栅格化永远有中文字形可用——既不空白也不方框。"""
    global _ZH_HEAD, SVG_CJK_FALLBACK, SVG_BUILD_CJK_FONT
    if _build_cjk_font():
        return True
    import subprocess
    import urllib.request
    urls = (
        'https://github.com/notofonts/noto-cjk/raw/main/Sans/OTF/'
        'SimplifiedChinese/NotoSansCJKsc-Regular.otf',
        'https://cdn.jsdelivr.net/gh/notofonts/noto-cjk@main/Sans/OTF/'
        'SimplifiedChinese/NotoSansCJKsc-Regular.otf',
    )
    fdir = os.path.join(os.path.expanduser('~'), '.fonts')
    os.makedirs(fdir, exist_ok=True)
    dst = os.path.join(fdir, 'NotoSansCJKsc-Regular.otf')
    for u in urls:
        try:
            log(f'  · 本机无中文字体，拉取 Noto Sans CJK SC：{u[:60]}…')
            req = urllib.request.Request(u, headers={'User-Agent': 'curl/8'})
            with urllib.request.urlopen(req, timeout=180) as r, \
                    open(dst, 'wb') as f:
                shutil.copyfileobj(r, f)
            if os.path.getsize(dst) > 1_000_000:
                subprocess.run(['fc-cache', '-f', fdir],
                               capture_output=True, timeout=60)
                # 刷新模块级字体链（此前探测为空）
                _ZH_HEAD = _build_cjk_font()
                SVG_BUILD_CJK_FONT = _ZH_HEAD
                SVG_CJK_FALLBACK = ((_ZH_HEAD + ',' + _DEFAULT_CHAIN)
                                    if _ZH_HEAD else _DEFAULT_CHAIN)
                if _ZH_HEAD:
                    log(f'  ✓ 中文字形兜底就绪：{_ZH_HEAD}')
                    return True
            os.remove(dst)
        except Exception as e:
            log(f'    ! 字体下载失败：{e}')
    return False


def _mermaid_fo_to_native_text(text):
    """把 Mermaid SVG 里的 <foreignObject>（HTML 标签）改写为原生 SVG <text>。

    根因补充：mmdc 默认 htmlLabels:true，节点/边标签全部以 XHTML foreignObject
    嵌入 SVG——cairosvg 完全不渲染 foreignObject、Word 的 SVG 引擎也不支持，
    导致预栅格化后的 PNG 里中文照样丢失（旧版仅做 font-family 注入无效）。
    本函数按 foreignObject 的 width/height 与父级 translate 定位，抽取 <p> 文本、
    以 <br> 断行、按 line-height/text-align 还原多行居中排版，转为带显式
    font-family（含 CJK 回退链）的 <text><tspan>。返回 (新文本, 转换数)。
    """
    import html as _html

    def conv(mm):
        attrs, inner = mm.group(1), mm.group(2)
        mw = re.search(r'width="([\d.]+)"', attrs)
        mh = re.search(r'height="([\d.]+)"', attrs)
        if not (mw and mh):
            return mm.group(0)          # 无法定位，原样保留
        w, h = float(mw.group(1)), float(mh.group(1))
        sm = re.search(r'<div[^>]*style="([^"]*)"', inner)
        style = sm.group(1) if sm else ''
        fs = 16.0
        fsm = re.search(r'font-size:\s*([\d.]+)px', style)
        if fsm:
            fs = float(fsm.group(1))
        lhm = re.search(r'line-height:\s*([\d.]+)', style)
        lh = fs * (float(lhm.group(1)) if lhm else 1.5)
        anchor = 'middle' if 'text-align: center' in style else 'start'
        pm = re.search(r'<p>(.*?)</p>', inner, flags=re.S)
        raw = _html.unescape(pm.group(1) if pm
                              else re.sub(r'<[^>]+>', '', inner))
        lines = [ln.strip() for ln in re.split(r'<br\s*/?>', raw)
                 if ln.strip()] or ['']
        y0 = max((h - lh * len(lines)) / 2 + lh * 0.78, lh * 0.78)
        x = w / 2 if anchor == 'middle' else 0
        tspans = ''.join('<tspan x="%g" y="%g">%s</tspan>'
                         % (x, y0 + i * lh, _html.escape(ln))
                         for i, ln in enumerate(lines))
        family = (SVG_BUILD_CJK_FONT + ',' + SVG_CJK_FALLBACK
                  if SVG_BUILD_CJK_FONT else SVG_CJK_FALLBACK)
        # font-weight 显式透传：fontconfig 的 family 级匹配不识别 "Bold" 风格名，
        # 若节点标签是 <strong>/<b>（Mermaid htmlLabels 常见），不加粗则与图面
        # 语义不符；Pango 对含中文实体字重的家族会正常取粗体 face。
        fw = '700' if re.search(r'<(?:strong|b)\b', inner) else None
        weight_attr = f' font-weight="{fw}"' if fw else ''
        return ('<text font-family="%s"%s font-size="%g" text-anchor="%s">'
                '%s</text>' % (family, weight_attr, fs, anchor, tspans))
        # font-weight 显式透传：fontconfig 的 family 级匹配不识别 "Bold" 风格名，
        # 若节点标签是 <strong>/<b>（Mermaid htmlLabels 常见），不加粗则与图面
        # 语义不符；Pango 对含中文实体字重的家族会正常取粗体 face。
        fw = '700' if re.search(r'<(?:strong|b)\b', inner) else None
        weight_attr = f' font-weight="{fw}"' if fw else ''
        return ('<text font-family="%s"%s font-size="%g" text-anchor="%s">'
                '%s</text>' % (family, weight_attr, fs, anchor, tspans))

    out, n = re.subn(r'<foreignObject([^>]*)>(.*?)</foreignObject>',
                     conv, text, flags=re.S)
    return out, n


def _inject_cjk_font_fallback(text):
    """向 SVG 的 CSS/属性 font-family 追加 CJK 回退链（幂等）。

    注意：Mermaid 的字体值本身带引号（如 font-family:"trebuchet ms",verdana,…），
    旧正则 `[^;"}]+` 遇首个引号即失配、注入静默失效，故此处允许值中含引号；
    负向前瞻跳过已注入过的声明，保证重复调用不叠加。
    """
    pat = re.compile(r'font-family:(?![^;{}]*Microsoft YaHei)\s*'
                     r'((?:[^;"{}]|"[^"]*")+)', re.S)
    text, _ = pat.subn(lambda mm: 'font-family:' + mm.group(1).rstrip() + ','
                       + SVG_CJK_FALLBACK, text)
    text = re.sub(r"font-family='([^']*)'",
                  lambda mm: "font-family='%s,%s'"
                             % (mm.group(1), SVG_CJK_FALLBACK), text)
    text = re.sub(r'font-family="([^"]*)"',
                  lambda mm: 'font-family="%s,%s"'
                             % (mm.group(1), SVG_CJK_FALLBACK), text)
    return text


def _png_has_notdef_box(png_path):
    """检测栅格结果里是否存在 .notdef 空心方框（中文丢字/变方框的直接证据）。

    实测（cairosvg+DejaVu Sans 渲染"中文测试方框"）：.notdef 是壁厚约 0.12em
    的**粗边空心矩形**——顶/底边为整幅实心横带，左右为整列实心竖带，内部全白。
    判据据此设计，逐条排除误报源：
      · 四角黑 + 顶/底行覆盖率 ≥95%、左/右列覆盖率 ≥95%（汉字笔画团不满足）；
      · 内部全白（实心色块不满足）；
      · 壁厚/尺寸 ∈ [8%, 35%]（Mermaid 圆角节点框壁仅 1~2px ≈1%，不满足）；
      · 宽高比 ∈ [0.7, 1.4]（长条装饰框不满足）；
      · 同一图内 ≥3 个才判存在（个别命中视为噪声）。
    搜索半径按字号自适应：正文 16px×300DPI≈50px，上限取 90px 兼容标题字号。
    """
    try:
        from PIL import Image
    except ImportError:
        return False
    with Image.open(png_path) as im:
        if 'A' in im.getbands():
            bg = Image.new('RGB', im.size, 'white')
            bg.paste(im.convert('RGB'), mask=im.getchannel('A'))
            im = bg
        # 缩到宽 ≤2400 再检测：方框尺寸同步折算，降采样抹掉抗锯齿细线不影响
        # 封闭轮廓判定（.notdef 边框厚达数像素，远大于缩放步长）
        if im.width > 2400:
            ds = 2400.0 / im.width
            im = im.resize((2400, max(1, int(im.height * ds))), Image.LANCZOS)
        g = im.convert('L').point(lambda p: 0 if p < 200 else 255)
        w, h = g.size
        flat = list(g.getdata())
        black = [[flat[y * w + x] < 200 for x in range(w)] for y in range(h)]

    def is_dark(x, y):
        return 0 <= x < w and 0 <= y < h and black[y][x]

    def row_all(y, x0, x1):
        row = black[y]
        for xx in range(x0, x1 + 1):
            if not row[xx]:
                return False
        return True

    def col_all(x, y0, y1):
        for yy in range(y0, y1 + 1):
            if not black[yy][x]:
                return False
        return True

    def interior_white(x0, y0, x1, y1):
        for yy in range(y0 + 1, y1):
            row = black[yy]
            for xx in range(x0 + 1, x1):
                if row[xx]:
                    return False
        return True

    boxes = []
    max_side = 90                      # 正文字号 300DPI≈50px，留标题字号余量
    min_side = 12

    def record(x, y, bw, bh):
        """找到一个合格空心粗框，记录并去重。"""
        for ux, uy, uw in boxes:
            if abs(x - ux) <= 3 and abs(y - uy) <= 3 and abs(bw - uw) <= 3:
                return
        boxes.append((x, y, bw))

    def try_box(x, y):
        """左上角锚定精判：从 (x,y) 的实心顶行连续段推出候选框。

        沿顶行走线（步距 1）收集所有"可能右缘" e（走线上每一点都是一个候选
        右边界——.notdef 相邻排列时顶行跨多框连成横带，真右缘藏在带内）；对每个
        满足尺寸/宽高比的候选右缘做全套校验（底边整幅实心、右边整列实心、壁厚
        8%~35%、四角实心、空腔全白），取**最窄合格窗**——最窄窗即单框宽度，避免
        宽窗把邻框的空腔切成"实心块+白隙"的假空心态而误报。
        """
        row = black[y]
        if not row[x]:
            return False
        e = x
        while e + 1 < w and row[e + 1]:
            e += 1
        # 左边向下走满连续段定高度（左壁是框的真实边界，不受横带影响）
        ye = y
        while ye + 1 < h and black[ye + 1][x]:
            ye += 1
        # 左边向下走满连续段定高度。粘连排布下该走线会被邻框横带截短（实测
        # dv.png：字高 74px，左列走线在共用底带处断为 bh=19）——若 bh < min_side
        # 直接拒绝将漏检；故 bh 过小视为"被截断"，改用顶行走线长作高度参考，
        # 窗校验里再以 ye 处的实心底带兜真值（假锚找不到闭合底边自然落空）。
        bh = ye - y + 1
        if bh > max_side:
            return False
        h_ref = max(bh, e - x + 1)          # 高度参考：走线被截时取顶行段宽近似
        if h_ref < min_side:
            return False
        # 顶行中点列探测真实顶壁厚。锚点落在横带下沿（相邻粘连框的第二排左上角、
        # 或 .notdef 底边与下一字顶边粘连的共用带）时，向下探测得 tt0≈0——须改向
        # 上探：锚行自身即某框顶带时，向上走满该带同样得到壁厚。两方向皆 ≤1px
        # 才是"穿过粘连点的假锚"，拒绝之。（实测 dv.png：.notdef 底边与下一字
        # 顶边粘连成 11px 共用带，向下探 tt0=0 被误拒导致漏检。）
        # 参考长度 ref 取 min(顶行走线长, 左列走线长)：粘连排布下二者都被邻框
        # 截短（dv.png 真字高 74px 但两走线各截为 42/19），比例阈值按较小者定，
        # 保证 5px 真壁厚不被上限误杀；下限仍按 bh（防细线噪声）。
        midc = x + min(e - x + 1, max_side) // 2
        tt0 = 0
        while y + tt0 < h and black[y + tt0][midc]:
            tt0 += 1
        if tt0 <= 1:
            tu0 = 0
            while y - tu0 - 1 >= 0 and black[y - tu0 - 1][midc]:
                tu0 += 1
            tt0 = tu0
        ref = min(e - x + 1, bh)
        if not (bh * 0.08 <= tt0 <= min(ref, max_side) * 0.35):
            return False
        best = None
        for xe in range(x + min_side, e + 1):
            bw = xe - x + 1
            if bw > max_side:
                break
            if not (0.7 <= bw / bh <= 1.4):
                continue
            # 底/右整幅实心带 + 角部实心（排除横带截出的假窗）
            if not (row_all(ye, x, xe)                   # 底边整幅实心带
                    and col_all(xe, y, ye)):             # 右边整列实心带
                continue
            if not black[y][x + bw // 2]:                # 顶行中点须为黑
                continue
            # 壁厚检查：顶边中点列、左边中点行的暗像素深度须在字尺寸的 8%~35%
            # （Mermaid 节点细框壁 ≈1% 被排除；实心色块 >35% 被排除）。
            # 粘连排布下 ye 可能被邻框横带截短致 bh 偏小，故比例上限取
            # max(bh, bw)*0.35 兜容（实测 dv.png：字高 74px、bh 被截为 19px、
            # 真壁厚 5px，按 bh*0.35=6.6 仍可通过；此处保留原口径并显式写明）。
            tmax = min(bw, bh) * 0.35                    # 壁厚上限
            top_t = 0
            while top_t < bh and black[y + top_t][x + bw // 2]:
                top_t += 1
            if not (bh * 0.08 <= top_t <= tmax):         # 顶壁厚按高度比例（原误用 bw）
                continue
            left_t = 0
            while left_t < bw and black[y + bh // 2][x + left_t]:
                left_t += 1
            if not (bw * 0.08 <= left_t <= tmax):        # 左壁厚按宽度比例（原误用 bh）
                continue
            # 内腔必须存在且全白（从边框内侧起跳）
            ix0, iy0 = x + left_t, y + top_t
            ix1, iy1 = xe - left_t, ye - top_t           # 粗略对称假设由白检兜底
            if ix1 - ix0 < 3 or iy1 - iy0 < 3:
                continue
            # 右/左壁厚可能不等：分别探测右边与底边厚度再定内腔
            rt = 0
            while rt < bw and black[y + bh // 2][xe - rt]:
                rt += 1
            bt = 0
            while bt < bh and black[ye - bt][x + bw // 2]:
                bt += 1
            ix1, iy1 = xe - rt + 1, ye - bt + 1
            if ix1 - ix0 < 3 or iy1 - iy0 < 3:
                continue
            if not interior_white(ix0, iy0, ix1, iy1):
                continue
            # .notdef 空心矩形是"顶行+底行+左列+右列"四条**细带**，四角为孤立
            # 黑像素；汉字部件（日/口/曰等）被笔画贯穿——顶行中点列、左边中点行
            # 的暗像素连续段必然深入内腔。要求这两条过中线连续段 ≤ 壁厚+2px，
            # 即可排除字腔误报（实测图1-2"能力"的"日/厶"部件曾被判成框）。
            seg = y
            while is_dark(x + bw // 2, seg):
                seg += 1
            if seg - y > top_t + 2:
                continue
            seg = x
            while is_dark(seg, y + bh // 2):
                seg += 1
            if seg - x > left_t + 2:
                continue
            best = (x, y, bw, bh)
            break                                        # 最窄合格窗即单框
        if best is None:
            return False
        record(*best)
        return True

    # 扫描 A：左上角锚定——(x,y) 黑且"上邻非黑 **或** 左邻非黑"（OR 而非 AND）。
    # 真框左上角两条件皆成立；.notdef 粘连排布时，后续各框左上角的左邻被前框
    # 右壁染黑（AND 会全部丧失锚点资格、整行只出一个锚），但其上方仍是白隙，
    # "上邻非黑"单独成立即可成锚。误报由 try_box 内 tt0 顶壁厚预探测（拒绝横带
    # 下沿假锚）+ 空腔全白校验兜底。顶行走线在 try_box 内完成，可处理横带形态。
    for y in range(h):
        row = black[y]
        for x in range(w):
            if not row[x]:
                continue
            if is_dark(x, y - 1) and is_dark(x - 1, y):
                continue
            try_box(x, y)

    # 扫描 B：右下角锚定——覆盖抗锯齿/粘连导致左上角特征不纯的情形。
    # (x,y) 黑、下/右邻非黑 → 左上角必为同一条顶行连续段的起点 xs，
    # 与扫描 A 等价，故无需独立路径（保留注释说明设计决策）。

    return len(boxes) >= 3


def _sharp_render_png(svg_bytes, png_path, out_w, out_h):
    """cairosvg 缺失时的回退光栅化：node + sharp（内嵌 librsvg）。

    librsvg 原生支持 foreignObject 与 fontconfig 中文回退，质量不低于 cairosvg；
    依赖本机 node 与项目 node_modules/sharp（npm i sharp）。找不到即返回 False。
    SVG 经 stdin 传入，避免临时文件与编码问题。
    """
    import subprocess
    script = (
        "const sharp=require('sharp');"
        "let d=[];process.stdin.on('data',c=>d.push(c));"
        "process.stdin.on('end',()=>{sharp(Buffer.concat(d),"
        "{density:300}).resize({width:%d,height:%d,fit:'fill'})"
        ".png().toFile(process.argv[1])"
        ".then(()=>process.exit(0)).catch(e=>{console.error(String(e));"
        "process.exit(1)})});" % (out_w, out_h)
    )
    for nm in (os.path.join(BOOK_DIR, 'node_modules'),
               os.path.join(os.path.dirname(BOOK_DIR), 'node_modules')):
        if os.path.isfile(os.path.join(nm, 'sharp', 'package.json')):
            try:
                r = subprocess.run(['node', '-e', script, png_path],
                                   input=svg_bytes,
                                   env={**os.environ, 'NODE_PATH': nm},
                                   capture_output=True, timeout=120)
                if r.returncode == 0 and os.path.isfile(png_path):
                    return True
                log(f'    ! sharp 栅格化报错：{r.stderr.decode()[:200]}')
            except Exception as e:
                log(f'    ! sharp 栅格化异常：{e}')
            return False
    return False


def svg_to_png(svg_path, png_path, dpi=300):
    """把 Mermaid SVG 预栅格化为高清 PNG，解决 docx 插图中文丢失问题。

    中文丢失有两层原因，缺一不可地都要处理：
    ① Word 端：pandoc 若将 SVG 直通嵌入 docx，Word 的 SVG 渲染引擎（IE/Trident）
       不解析 <style> 里的 font-family CSS、西文字体链无中文字形且不回退 → 空白；
    ② 构建端：mmdc 默认用 HTML <foreignObject> 承载标签文字，cairosvg 不支持
       foreignObject——即便预栅格化，PNG 里中文同样是空的（旧版脚本即败在此，
       其 font-family 注入还因 Mermaid 字体值带引号而从未生效）。
    对策：先把 foreignObject 改写为带 CJK 回退字体链的原生 SVG <text>，再对
    其余 CSS/属性注入同一回退链，最后按 viewBox 尺寸 ×300DPI 栅格化；PNG 自带
    字形，任何阅读器显示一致。cairosvg 不可用时回退 pandoc 原生 SVG→PNG。
    栅格化后做双重像素校验：源 SVG 含中文而 PNG 几乎全白 → 丢字判失败；
    PNG 中存在成批 .notdef 空心方框 → 字体环境缺中文字形判失败（防"方框"静默出厂）。
    cairosvg 缺失时走 node+sharp 光栅化（Sharp 内嵌 librsvg，支持 foreignObject
    原生渲染与 fontconfig 回退）——**不再用 pandoc 转 PNG**：pandoc 的 svg2png
    经 opticon/Imager 渲染，实测会把 CJK 文本整段丢弃（PNG 里连方框都没有），
    是旧版"SVG 嵌入 docx 中文丢失"的直接根因。全部路径失败则返回 False，
    由调用方决定回退策略。返回是否成功。
    栅格化后做双重像素校验：源 SVG 含中文而 PNG 几乎全白 → 丢字判失败；
    PNG 中存在成批 .notdef 空心方框 → 字体环境缺中文字形判失败（防"方框"静默出厂）。
    cairosvg 缺失时走 node+sharp 光栅化（Sharp 内嵌 librsvg，支持 foreignObject
    原生渲染与 fontconfig 回退）——**不再用 pandoc 转 PNG**：pandoc 的 svg2png
    经 opticon/Imager 渲染，实测会把 CJK 文本整段丢弃（PNG 里连方框都没有），
    是旧版"SVG 嵌入 docx 中文丢失"的直接根因。全部路径失败则返回 False，
    由调用方决定回退策略。返回是否成功。
    """
    try:
        import cairosvg
    except ImportError:
        cairosvg = None
    try:
        with open(svg_path, encoding='utf-8') as f:
            text = f.read()
        m = re.search(r'<svg\b[^>]*>', text)
        if not m:
            return False
        tag = m.group(0)
        mvb = re.search(r'viewBox="([\d.\s-]+)"', tag)
        if mvb:
            vb = [float(x) for x in mvb.group(1).split()]
            vw, vh = vb[2], vb[3]
        else:
            mw = re.search(r'\bwidth="([\d.]+)', tag)
            mh = re.search(r'\bheight="([\d.]+)', tag)
            if not (mw and mh):
                return False
            vw, vh = float(mw.group(1)), float(mh.group(1))
        has_cjk_src = any('\u4e00' <= c <= '\u9fff' for c in text)
        # 关键一步：foreignObject(HTML 标签) → 原生 SVG <text>（否则中文不可见）
        text, n_fo = _mermaid_fo_to_native_text(text)
        # 其余 CSS 声明 + font-family 属性补注 CJK 回退（修复版正则，见文档）
        text = _inject_cjk_font_fallback(text)
        scale = dpi / 96.0
        out_w = max(1, int(round(vw * scale)))
        out_h = max(1, int(round(vh * scale)))
        rendered_by = None
        out_w = max(1, int(round(vw * scale)))
        out_h = max(1, int(round(vh * scale)))
        rendered_by = None
        if cairosvg is not None:
            cairosvg.svg2png(bytestring=text.encode('utf-8'), write_to=png_path,
                             output_width=out_w, output_height=out_h)
            rendered_by = 'cairosvg'
                             output_width=out_w, output_height=out_h)
            rendered_by = 'cairosvg'
        else:
            # sharp（librsvg）：支持 foreignObject 与 fontconfig 中文回退；
            # 明确排除 pandoc——其 svg2png 渲染器会整段丢弃 CJK 文本（见文档）
            if _sharp_render_png(text.encode('utf-8'), png_path, out_w, out_h):
                rendered_by = 'sharp(librsvg)'
        if rendered_by is None:
            log(f'  ! SVG 栅格化失败（{os.path.basename(svg_path)}）：'
                f'cairosvg 与 node+sharp 均不可用，不退回 pandoc'
                f'（其渲染器丢 CJK 文本），请 pip install cairosvg 或 npm i sharp')
            return False
            # sharp（librsvg）：支持 foreignObject 与 fontconfig 中文回退；
            # 明确排除 pandoc——其 svg2png 渲染器会整段丢弃 CJK 文本（见文档）
            if _sharp_render_png(text.encode('utf-8'), png_path, out_w, out_h):
                rendered_by = 'sharp(librsvg)'
        if rendered_by is None:
            log(f'  ! SVG 栅格化失败（{os.path.basename(svg_path)}）：'
                f'cairosvg 与 node+sharp 均不可用，不退回 pandoc'
                f'（其渲染器丢 CJK 文本），请 pip install cairosvg 或 npm i sharp')
            return False
        ok = os.path.isfile(png_path) and os.path.getsize(png_path) > 0
        # 像素校验：源含中文但栅格结果几乎全白 → 文字被渲染器丢弃，判失败告警。
        # 注意先合成到白底再测墨色：cairosvg 输出 RGBA，若按原始灰度取像素，
        # 透明区 alpha=0 会被误读为"黑"，导致空图也能通过校验（假阳性）。
        if ok and has_cjk_src:
            try:
                from PIL import Image
                with Image.open(png_path) as im:
                    if 'A' in im.getbands():          # 透明通道合成到白底
                        bg = Image.new('RGB', im.size, 'white')
                        bg.paste(im.convert('RGB'), mask=im.getchannel('A'))
                        im = bg
                    im = im.convert('L').resize((200, 200))
                    px = list(im.getdata())
                ink = sum(1 for p in px if p < 200) / len(px)
                if ink < 0.005:
                    log(f'  ! SVG 栅格化校验失败（{os.path.basename(svg_path)}）：'
                        f'PNG 近乎全白，中文可能未渲染，请检查 cairosvg 字体环境')
                    return False
            except Exception:
                pass
        # 方框校验：源含中文且 PNG 里成批出现 .notdef 空心方框 → 字体链取到了
        # 无中文字形的西文字体（"不显示"修好了但"变方框"没修好），判失败。
        if ok and has_cjk_src:
            try:
                if _png_has_notdef_box(png_path):
                    log(f'  ! SVG 栅格化校验失败（{os.path.basename(svg_path)}）：'
                        f'检测到 .notdef 空心方框，中文字形缺失；'
                        f'当前构建字体={SVG_BUILD_CJK_FONT!r}，'
                        f'请安装 Noto Sans CJK/WenQuanYi 或联网自动拉取后重试')
                    return False
            except Exception:
                pass
        # 方框校验：源含中文且 PNG 里成批出现 .notdef 空心方框 → 字体链取到了
        # 无中文字形的西文字体（"不显示"修好了但"变方框"没修好），判失败。
        if ok and has_cjk_src:
            try:
                if _png_has_notdef_box(png_path):
                    log(f'  ! SVG 栅格化校验失败（{os.path.basename(svg_path)}）：'
                        f'检测到 .notdef 空心方框，中文字形缺失；'
                        f'当前构建字体={SVG_BUILD_CJK_FONT!r}，'
                        f'请安装 Noto Sans CJK/WenQuanYi 或联网自动拉取后重试')
                    return False
            except Exception:
                pass
        if n_fo:
            log(f'    · {os.path.basename(svg_path)}：foreignObject→原生文本 '
                f'{n_fo} 处')
        return ok
    except Exception as e:
        log(f'  ! SVG 栅格化失败（{os.path.basename(svg_path)}）：{e}')
        return False


def resize_media(md_text, tmpdir, max_side=1600, quality=85):
    """
    将合并 md 引用到的图片做构建副本：SVG 预栅格化为 300DPI PNG（Word 中文
    兼容），位图大图压缩为 JPEG/PNG 副本，返回 (临时md文本, 图片映射数)。
    副本存于 tmpdir；源图零改动。
    """
    from PIL import Image

    refs = sorted(set(re.findall(r'^!\[[^\]]*\]\(([^)]+)\)', md_text, flags=re.M)))
    mapping = {}
    n_svg = 0
    for rel in refs:
        src = os.path.normpath(os.path.join(BOOK_DIR, '输出', rel))
        if not os.path.isfile(src):
            continue
        # SVG（Mermaid 渲染图）→ 预栅格化 PNG：Word 的 SVG 引擎丢中文，不再直通
        if src.lower().endswith('.svg'):
            stem = os.path.splitext(os.path.basename(src))[0]
            dst = os.path.join(tmpdir, stem + '.png')
            if svg_to_png(src, dst):
                mapping[rel] = dst
                n_svg += 1
            else:                       # 栅格化失败：退回直通（保构建可用）
                mapping[rel] = src
            continue
        with Image.open(src) as im:
            im.load()
            w, h = im.size
            scale = min(1.0, max_side / max(w, h))
            if scale < 1.0:
                im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))),
                               Image.LANCZOS)
            has_alpha = (im.mode in ('RGBA', 'LA', 'P')
                         and 'transparency' in im.info or im.mode in ('RGBA', 'LA'))
            stem = os.path.splitext(os.path.basename(src))[0]
            if has_alpha:
                dst = os.path.join(tmpdir, stem + '.png')
                im.save(dst, 'PNG', optimize=True, dpi=(300, 300))
            else:
                dst = os.path.join(tmpdir, stem + '.jpg')
                im.convert('RGB').save(dst, 'JPEG', quality=quality,
                                       optimize=True, dpi=(300, 300))
        mapping[rel] = dst

    tmp_md = md_text
    for rel, dst in mapping.items():
        tmp_md = tmp_md.replace(f']({rel})', f']({dst})')
    if n_svg:
        log(f'  ✓ SVG 预栅格化 {n_svg} 幅 → 300DPI PNG（Word 中文兼容）')
    return tmp_md, len(mapping)


# ----------------------------------------------------------------------------
# 第三步：定制中文样式模板 reference.docx
# ----------------------------------------------------------------------------
def _set_style_fonts(style, ea_font, ascii_font):
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.find(qn('w:rFonts'))
    if rfonts is None:
        rfonts = OxmlElement('w:rFonts')
        rpr.insert(0, rfonts)
    rfonts.set(qn('w:ascii'), ascii_font)
    rfonts.set(qn('w:hAnsi'), ascii_font)
    rfonts.set(qn('w:eastAsia'), ea_font)
    rfonts.set(qn('w:cs'), ascii_font)


def build_reference_docx(ref_path):
    """生成 pandoc 默认模板并改为中文书籍审阅版式。"""
    pandoc = pypandoc.get_pandoc_path()
    import subprocess
    with open(ref_path, 'wb') as f:
        subprocess.run([pandoc, '--print-default-data-file', 'reference.docx'],
                       stdout=f, check=True)

    doc = Document(ref_path)
    st = doc.styles

    def tune(name, ea=None, ascii_=None, size=None, bold=None, color=None,
             align=None, before=None, after=None, line=None, page_break=None,
             indent_l=None, indent_r=None):
        try:
            s = st[name]
        except KeyError:
            return False
        if ea or ascii_:
            _set_style_fonts(s, ea or F_BODY_EA, ascii_ or F_BODY_ASCII)
        if size is not None:
            s.font.size = Pt(size)
        if bold is not None:
            s.font.bold = bold
        if color is not None:
            s.font.color.rgb = color
        pf = s.paragraph_format
        if align is not None:
            pf.alignment = align
        if before is not None:
            pf.space_before = Pt(before)
        if after is not None:
            pf.space_after = Pt(after)
        if line is not None:
            pf.line_spacing = line
        if page_break is not None:
            pf.page_break_before = page_break
        if indent_l is not None:
            pf.left_indent = Cm(indent_l)
        if indent_r is not None:
            pf.right_indent = Cm(indent_r)
        return True

    C = WD_ALIGN_PARAGRAPH.CENTER
    L = WD_ALIGN_PARAGRAPH.LEFT

    # 正文族：宋体 11pt，1.5 倍行距
    for name in ('Normal', 'Body Text', 'First Paragraph', 'Compact'):
        tune(name, ea=F_BODY_EA, ascii_=F_BODY_ASCII, size=11, line=1.5,
             color=BLACK)
    # 标题族：黑体，黑色（覆盖 pandoc 默认蓝色），一级标题居中且另起页
    tune('Heading 1', ea=F_HEAD_EA, ascii_=F_HEAD_ASCII, size=20, bold=True,
         color=BLACK, align=C, before=0, after=18, line=1.2, page_break=True)
    tune('Heading 2', ea=F_HEAD_EA, ascii_=F_HEAD_ASCII, size=15, bold=True,
         color=BLACK, align=L, before=16, after=8, line=1.2)
    tune('Heading 3', ea=F_HEAD_EA, ascii_=F_HEAD_ASCII, size=13, bold=True,
         color=BLACK, align=L, before=12, after=6, line=1.2)
    tune('Heading 4', ea=F_HEAD_EA, ascii_=F_HEAD_ASCII, size=12, bold=True,
         color=BLACK, before=10, after=4)
    for i in range(5, 10):
        tune(f'Heading {i}', ea=F_HEAD_EA, ascii_=F_HEAD_ASCII, size=11,
             bold=True, color=BLACK)
    # 书名页
    tune('Title', ea=F_HEAD_EA, ascii_=F_HEAD_ASCII, size=30, bold=True,
         color=BLACK, align=C, after=10)
    tune('Subtitle', ea=F_BODY_EA, ascii_=F_BODY_ASCII, size=14, bold=False,
         color=GRAY, align=C, after=6)
    # 引用块（手册/模板/设计师注）：楷体 10.5pt，双侧缩进，深灰
    tune('Block Text', ea=F_QUOTE_EA, ascii_=F_QUOTE_ASCII, size=10.5,
         color=RGBColor(0x40, 0x40, 0x40), line=1.3, indent_l=0.74,
         indent_r=0.74, before=4, after=4)
    # 图题/表题：楷体 9.5pt 居中灰
    tune('Image Caption', ea=F_QUOTE_EA, ascii_=F_QUOTE_ASCII, size=9.5,
         color=GRAY, align=C, before=4, after=10)
    tune('Table Caption', ea=F_QUOTE_EA, ascii_=F_QUOTE_ASCII, size=9.5,
         color=GRAY, align=C, before=6, after=4)
    # 目录
    tune('TOC Heading', ea=F_HEAD_EA, ascii_=F_HEAD_ASCII, size=16, bold=True,
         color=BLACK, align=C)
    # pandoc 默认模板不带 TOC 1–9 条目样式，自建三级（供静态目录使用）
    for lvl, (sz, ind) in {1: (11, 0.0), 2: (10.5, 0.74), 3: (10, 1.48)}.items():
        name = f'TOC {lvl}'
        try:
            st[name]
        except KeyError:
            s = st.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
            s.base_style = st['Normal']
        tune(name, ea=F_BODY_EA, ascii_=F_BODY_ASCII, size=sz, color=BLACK,
             line=1.3, after=3)
        st[name].paragraph_format.left_indent = Cm(ind)

    # 页面：A4 + 审阅版页边距
    for sec in doc.sections:
        sec.page_width, sec.page_height = Cm(PAGE_W_CM), Cm(PAGE_H_CM)
        sec.top_margin = sec.bottom_margin = Cm(MARGIN_TB_CM)
        sec.left_margin = sec.right_margin = Cm(MARGIN_LR_CM)

    doc.save(ref_path)
    return ref_path


# ----------------------------------------------------------------------------
# 第四步：docx 出版级后处理
# ----------------------------------------------------------------------------
def _remove_field_toc(doc):
    """移除 pandoc 生成的 Word 域式目录（sdt 块，不更新则空白）。"""
    body = doc.element.body
    removed = 0
    for sdt in body.findall(qn('w:sdt')):
        instrs = sdt.findall('.//' + qn('w:instrText'))
        if any('TOC' in (i.text or '') for i in instrs):
            body.remove(sdt)
            removed += 1
    return removed


def _build_static_toc(doc, depth):
    """
    用 Heading 1..depth 生成静态目录（题头"目　录"+ 条目，不含页码），
    插入到第一个一级标题（前言）之前。返回条目数。
    """
    heading_styles = {f'Heading {i}': i for i in range(1, depth + 1)}
    entries = [(heading_styles[p.style.name], p.text)
               for p in doc.paragraphs
               if p.style is not None and p.style.name in heading_styles
               and p.text.strip()]
    if not entries:
        return 0
    first_h1 = next(p for p in doc.paragraphs
                    if p.style is not None and p.style.name == 'Heading 1')

    def _style_or(name, fallback):
        try:
            doc.styles[name]
            return name
        except KeyError:
            return fallback

    head = first_h1.insert_paragraph_before(
        '目　录', style=_style_or('TOC Heading', 'Heading 1'))
    head.alignment = WD_ALIGN_PARAGRAPH.CENTER
    head.paragraph_format.page_break_before = True
    anchor = first_h1
    for level, text in entries:
        style = _style_or(f'TOC {level}', 'Normal')
        p = anchor.insert_paragraph_before(text, style=style)
        if level == 1:
            for r in p.runs:
                r.font.bold = True
    return len(entries)


def _set_table_borders(tbl_el):
    """给表格注入单线边框（0.5pt 灰），不依赖样式是否存在。"""
    tblPr = tbl_el.find(qn('w:tblPr'))
    if tblPr is None:
        tblPr = OxmlElement('w:tblPr')
        tbl_el.insert(0, tblPr)
    old = tblPr.find(qn('w:tblBorders'))
    if old is not None:
        tblPr.remove(old)
    borders = OxmlElement('w:tblBorders')
    for edge in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
        el = OxmlElement(f'w:{edge}')
        el.set(qn('w:val'), 'single')
        el.set(qn('w:sz'), '4')
        el.set(qn('w:space'), '0')
        el.set(qn('w:color'), '7F7F7F')
        borders.append(el)
    tblPr.append(borders)


def _fit_table_to_page(tbl_el):
    """表格宽度设为版心 100%，列宽按原比例重排，防溢出。"""
    tblPr = tbl_el.find(qn('w:tblPr'))
    tblW = tblPr.find(qn('w:tblW')) if tblPr is not None else None
    if tblW is None and tblPr is not None:
        tblW = OxmlElement('w:tblW')
        tblPr.append(tblW)
    if tblW is not None:
        tblW.set(qn('w:type'), 'pct')
        tblW.set(qn('w:w'), '5000')            # 5000 = 100%
    grid = tbl_el.find(qn('w:tblGrid'))
    if grid is not None:
        cols = grid.findall(qn('w:gridCol'))
        widths = [int(c.get(qn('w:w')) or 0) for c in cols]
        total = sum(widths)
        if total > 0:
            for c, w in zip(cols, widths):
                c.set(qn('w:w'), str(max(300, round(w / total * TEXT_W_TWIPS))))


def _mark_header_row(tbl_el):
    """首行标记为跨页重复表头（附录 A/B 长表受益）。"""
    tr = tbl_el.find(qn('w:tr'))
    if tr is None:
        return
    trPr = tr.find(qn('w:trPr'))
    if trPr is None:
        trPr = OxmlElement('w:trPr')
        tr.insert(0, trPr)
    if trPr.find(qn('w:tblHeader')) is None:
        el = OxmlElement('w:tblHeader')
        el.set(qn('w:val'), 'true')
        trPr.append(el)


def _add_page_number_footer(section):
    """页脚居中页码（PAGE 域）。"""
    footer = section.footer
    footer.is_linked_to_previous = False
    p = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for r in list(p.runs):
        r._r.getparent().remove(r._r)
    run = p.add_run()
    run.font.size = Pt(9)
    run.font.name = F_BODY_ASCII
    run._r.get_or_add_rPr()
    fld1 = OxmlElement('w:fldChar'); fld1.set(qn('w:fldCharType'), 'begin')
    instr = OxmlElement('w:instrText'); instr.set(qn('xml:space'), 'preserve')
    instr.text = ' PAGE '
    fld2 = OxmlElement('w:fldChar'); fld2.set(qn('w:fldCharType'), 'end')
    run._r.append(fld1); run._r.append(instr); run._r.append(fld2)


def postprocess_docx(docx_path, toc=True, toc_depth=2):
    doc = Document(docx_path)
    n_tables = n_fig_c = n_img_c = 0

    # 0) 目录：移除域式目录（空白不可读），替换为静态目录
    field_toc_removed = _remove_field_toc(doc)
    toc_entries = _build_static_toc(doc, toc_depth) if toc else 0

    # 1) 表格：边框 + 版心等宽 + 重复表头
    for t in doc.tables:
        _set_table_borders(t._tbl)
        _fit_table_to_page(t._tbl)
        _mark_header_row(t._tbl)
        n_tables += 1

    # 2) 图/图题居中：Figure、Image Caption 样式段，以及只含图片的段
    for p in doc.paragraphs:
        style = p.style.name if p.style is not None else ''
        has_drawing = p._p.findall('.//' + qn('w:drawing')) or \
                      p._p.findall('.//' + qn('w:pict'))
        if style in ('Figure', 'Image Caption', 'Captioned Figure'):
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            n_fig_c += 1
        elif has_drawing and not p.text.strip():
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            n_img_c += 1

    # 3) 超宽插图缩放至版心内（等比）；SVG 另设兜底宽（viewBox 无显式尺寸时 pandoc 给默认值）
    cap = Cm(IMG_CAP_CM)
    n_resized = 0
    for sh in doc.inline_shapes:
        if not sh.width or sh.width <= Emu(1):
            sh.width, sh.height = cap, int(cap * 0.62)   # SVG 无尺寸信息时的兜底
            n_resized += 1
            continue
        if sh.width > cap:
            ratio = cap / sh.width
            sh.height = int(sh.height * ratio)
            sh.width = cap
            n_resized += 1
        max_h = Cm(PAGE_H_CM - 2 * MARGIN_TB_CM - 1.5)   # 高图保护：不超一页版心高
        if sh.height and sh.height > max_h:
            ratio = max_h / sh.height
            sh.width = int(sh.width * ratio)
            sh.height = max_h
            n_resized += 1

    # 4) 页脚页码 + 页面 A4（样式模板已设，双保险）
    for sec in doc.sections:
        sec.page_width, sec.page_height = Cm(PAGE_W_CM), Cm(PAGE_H_CM)
        sec.top_margin = sec.bottom_margin = Cm(MARGIN_TB_CM)
        sec.left_margin = sec.right_margin = Cm(MARGIN_LR_CM)
        _add_page_number_footer(sec)

    # 5) 文档元数据
    cp = doc.core_properties
    cp.title = BOOK_TITLE
    cp.subject = f'全书合并审阅版 {VERSION}'
    cp.comments = ('由 assemble_book.py 自动组装生成；'
                   '源文件见 book/书稿/，请勿直接编辑本文件。')

    doc.save(docx_path)
    return {'tables': n_tables, 'fig_centered': n_fig_c,
            'img_centered': n_img_c, 'img_resized': n_resized,
            'field_toc_removed': field_toc_removed, 'toc_entries': toc_entries}


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description='《AI Agent 架构决策指南》组装 markdown 并转换 docx')
    ap.add_argument('--keep-notes', action='store_true',
                    help='保留草稿注释行（书稿状态/排版与标注说明等）')
    ap.add_argument('--final', action='store_true',
                    help='定稿构建：追加移除设计师专用「排版说明」引用块（审阅稿默认保留；与 --keep-notes 同用时以 --keep-notes 为准）')
    ap.add_argument('--no-cn-quotes', action='store_true',
                    help='保留 ASCII 直引号，不做中文弯引号规范化')
    ap.add_argument('--no-toc', action='store_true', help='不生成目录页')
    ap.add_argument('--toc-depth', type=int, default=2, choices=[1, 2, 3],
                    help='静态目录收录标题层级（默认 2）')
    ap.add_argument('--strict', action='store_true',
                    help='缺书稿文件即报错（付印前用；默认跳过并告警）')
    ap.add_argument('--no-resize', action='store_true',
                    help='不压缩图片（docx 约 170MB，慎用）')
    ap.add_argument('--outdir', default=os.path.join(BOOK_DIR, '输出'),
                    help='输出目录（默认 book/输出/）')
    args = ap.parse_args()

    outdir = os.path.abspath(args.outdir)
    os.makedirs(outdir, exist_ok=True)

    # -- 1. 组装 markdown ---------------------------------------------------
    log('【1/5】组装全书 markdown …')
    global STRICT
    STRICT = args.strict

    md_path, stats = assemble_markdown(outdir, args.keep_notes,
                                       cn_quotes=not args.no_cn_quotes,
                                       final=args.final)
    log(f"  ✓ {os.path.basename(md_path)}：{stats['chars']:,} 字符｜"
        f"文件 {stats['files']} 个 + 篇扉页 {stats['part_pages']} 页｜"
        f"图引用 {stats['images']} 处（全部可解析）｜"
        f"图位块接线嵌入 SVG {stats['figures_embedded']} 幅｜"
        f"清理草稿注释 {stats['notes_removed']} 行"
        + ('（--keep-notes：未清理）' if args.keep_notes else '')
        + ('（--final：含排版说明清理）' if args.final else ''))
    if stats['figures_pending']:
        log(f"  ! 未接线图位块 {len(stats['figures_pending'])} 处（保留占位）："
            + '；'.join(f'{f}:{p}' for f, p in stats['figures_pending'][:8]))
    if not args.no_cn_quotes:
        log(f"  ✓ 中文弯引号规范化：{stats['quotes_converted']} 行；"
            f"奇数引号跳过 {len(stats['quotes_skipped'])} 行")
        for s in stats['quotes_skipped'][:5]:
            log(f'    ! 未转换（引号不成对，请人工复核）：{s}…')

    with open(md_path, encoding='utf-8') as f:
        merged = f.read()

    # -- 2. 样式模板 --------------------------------------------------------
    log('【2/5】生成中文样式模板 reference.docx …')
    ref_path = os.path.join(outdir, '样式模板_reference.docx')
    build_reference_docx(ref_path)
    log(f'  ✓ {os.path.basename(ref_path)}（宋体正文/黑体标题/楷体引用，A4，1.5 倍行距）')

    # -- 3. 图片压缩副本（临时目录，不入工作区） -----------------------------
    tmpdir = tempfile.mkdtemp(prefix='bookmedia_')
    build_md = md_path
    try:
        if not args.no_resize:
            log('【3/5】图片预处理（SVG→300DPI PNG，位图压缩副本）…')
            tmp_md, n_img = resize_media(merged, tmpdir)
            build_md = os.path.join(tmpdir, 'build.md')
            with open(build_md, 'w', encoding='utf-8') as f:
                f.write(tmp_md)
            total_mb = sum(os.path.getsize(os.path.join(tmpdir, x))
                           for x in os.listdir(tmpdir) if x != 'build.md') / 1e6
            log(f'  ✓ {n_img} 张插图 → 构建副本（位图长边 1600px），共 {total_mb:.1f} MB')
        else:
            log('【3/5】跳过图片预处理（--no-resize，SVG 将原样嵌入 docx——'
                'Word 显示时插图中文会丢失，仅调试用）')

        # -- 4. pandoc 转换 -------------------------------------------------
        log('【4/5】pandoc 转换 docx …')
        docx_path = os.path.join(outdir, f'《{BOOK_TITLE}》全书稿_合并版.docx')
        extra = ['--quiet',
                 '--reference-doc', ref_path,
                 '--resource-path', f'{outdir}{os.pathsep}{SRC_DIR}{os.pathsep}{FIG_DIR}']
        if not args.no_toc:
            extra += ['--toc', '--toc-depth', str(args.toc_depth)]
        # -smart：禁用弯引号自动转换（中文语境下会把直引号误转为两个右引号，
        # 弯引号规范化已在组装阶段按行配对完成）
        pypandoc.convert_file(
            build_md, 'docx', format='markdown+east_asian_line_breaks-smart',
            outputfile=docx_path, extra_args=extra)
        size_mb = os.path.getsize(docx_path) / 1e6
        log(f'  ✓ {os.path.basename(docx_path)}（{size_mb:.1f} MB）')
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    # -- 5. 后处理 + 校验 ----------------------------------------------------
    log('【5/5】docx 出版级后处理 …')
    pp = postprocess_docx(docx_path, toc=not args.no_toc,
                          toc_depth=args.toc_depth)
    log(f"  ✓ 表格 {pp['tables']} 张（边框+版心等宽+重复表头）｜"
        f"图/图题居中 {pp['fig_centered'] + pp['img_centered']} 处｜"
        f"超宽图缩放 {pp['img_resized']} 张｜"
        f"静态目录 {pp['toc_entries']} 条（替换域式目录 {pp['field_toc_removed']} 处）｜"
        f"页脚页码、A4 版心已设置")

    # 复核
    doc = Document(docx_path)
    h1 = [p.text for p in doc.paragraphs
          if p.style is not None and p.style.name == 'Heading 1']
    final_mb = os.path.getsize(docx_path) / 1e6
    log('\n========== 构建报告 ==========')
    log(f'  合并版 md  ：{md_path}（{stats["chars"]:,} 字符）')
    log(f'  合并版 docx：{docx_path}（{final_mb:.1f} MB）')
    log(f'  一级标题 {len(h1)} 个（书名页/目录不计）：')
    for t in h1:
        log(f'    · {t[:46]}')
    log(f'  表格 {pp["tables"]} 张｜内嵌图 {len(doc.inline_shapes)} 张｜'
        f'段落 {len(doc.paragraphs):,} 个')
    log('  提示：源文件零改动；重跑本脚本即可再生成。样式微调请改脚本常量后重跑。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
