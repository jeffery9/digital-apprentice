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

依赖：python3、pypandoc-binary（自带 pandoc）、python-docx、pillow
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
from docx.shared import Cm, Pt, RGBColor

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
             'quotes_converted': 0, 'quotes_skipped': [], 'tables_spaced': 0}
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
# 第二步：图片压缩（审阅用副本，源图不动）
# ----------------------------------------------------------------------------
def resize_media(md_text, tmpdir, max_side=1600, quality=85):
    """
    将合并 md 引用到的 书稿/插画 大图压缩为副本，返回 (临时md文本, 图片映射数)。
    副本存于 tmpdir；扁平插画转 JPEG（q85），带透明通道的保留 PNG。
    """
    from PIL import Image

    refs = sorted(set(re.findall(r'^!\[[^\]]*\]\(([^)]+)\)', md_text, flags=re.M)))
    mapping = {}
    for rel in refs:
        src = os.path.normpath(os.path.join(BOOK_DIR, '输出', rel))
        if not os.path.isfile(src):
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

    # 3) 超宽插图缩放至版心内（等比）
    cap = Cm(IMG_CAP_CM)
    n_resized = 0
    for sh in doc.inline_shapes:
        if sh.width and sh.width > cap:
            ratio = cap / sh.width
            sh.height = int(sh.height * ratio)
            sh.width = cap
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
        f"清理草稿注释 {stats['notes_removed']} 行"
        + ('（--keep-notes：未清理）' if args.keep_notes else '')
        + ('（--final：含排版说明清理）' if args.final else ''))
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
            log('【3/5】压缩插图副本（源图不动）…')
            tmp_md, n_img = resize_media(merged, tmpdir)
            build_md = os.path.join(tmpdir, 'build.md')
            with open(build_md, 'w', encoding='utf-8') as f:
                f.write(tmp_md)
            total_mb = sum(os.path.getsize(os.path.join(tmpdir, x))
                           for x in os.listdir(tmpdir) if x != 'build.md') / 1e6
            log(f'  ✓ {n_img} 张插图 → 长边 1600px JPEG 副本，共 {total_mb:.1f} MB')
        else:
            log('【3/5】跳过图片压缩（--no-resize）')

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
