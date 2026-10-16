#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""组装 agentbook.docx：Markdown 章节 + matplotlib 插图 -> Word 文档。

中文防方框策略（docx 文本层）：
1) 所有样式字体显式设为"微软雅黑"，并同时写入 w:eastAsia / w:cs 属性；
2) Normal/标题/正文/表格/代码逐 run 设置 eastAsia 字体；
3) 若打开方无微软雅黑，Word/WPS 会按回退链自动选系统默认中文字体，不会出方框
   （方框只发生在字体存在但不含 CJK 字形时，如把 Arial 写进 eastAsia）。
插图防方框策略：图片为 PNG 位图，文字已烘焙，与阅读端字体无关。
"""
import re
from pathlib import Path

from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

BASE = Path(__file__).resolve().parent
CHAPTERS = sorted((BASE / "chapters").glob("ch*.md"))
OUT = BASE / "agentbook.docx"

CJK_FONT = "微软雅黑"      # Microsoft YaHei：Windows/WPS 通用且含全量 GB 字符
LATIN_FONT = "Calibri"


def set_run_fonts(run, latin=LATIN_FONT, cjk=CJK_FONT):
    run.font.name = latin
    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.append(rFonts)
    rFonts.set(qn("w:ascii"), latin)
    rFonts.set(qn("w:hAnsi"), latin)
    rFonts.set(qn("w:eastAsia"), cjk)   # 关键：东亚字体必须显式为中文字体
    rFonts.set(qn("w:cs"), latin)


def style_font(doc, name, size=None, bold=None, color=None, latin=LATIN_FONT, cjk=CJK_FONT):
    st = doc.styles[name]
    st.font.name = latin
    if size: st.font.size = Pt(size)
    if bold is not None: st.font.bold = bold
    if color: st.font.color.rgb = RGBColor(*color)
    rPr = st.element.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.append(rFonts)
    rFonts.set(qn("w:ascii"), latin)
    rFonts.set(qn("w:hAnsi"), latin)
    rFonts.set(qn("w:eastAsia"), cjk)
    rFonts.set(qn("w:cs"), latin)


INLINE_RE = re.compile(r"(\*\*.+?\*\*|`[^`]+`)")


def add_runs(p, text):
    """处理 **加粗** 与 `code` 内联标记。"""
    for part in INLINE_RE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            r = p.add_run(part[2:-2]); r.bold = True; set_run_fonts(r)
        elif part.startswith("`") and part.endswith("`"):
            r = p.add_run(part[1:-1])
            set_run_fonts(r, latin="Consolas", cjk=CJK_FONT)
            r.font.color.rgb = RGBColor(0xC7, 0x25, 0x4E)
        else:
            r = p.add_run(part); set_run_fonts(r)


def add_table(doc, rows):
    header = [c.strip() for c in rows[0].strip("|").split("|")]
    body = [[c.strip() for c in ln.strip("|").split("|")] for ln in rows[2:]]
    t = doc.add_table(rows=1 + len(body), cols=len(header))
    t.style = "Table Grid"
    for j, h in enumerate(header):
        cell = t.rows[0].cells[j]
        cell.text = ""
        p = cell.paragraphs[0]; r = p.add_run(h); r.bold = True; set_run_fonts(r)
        shd = OxmlElement("w:shd"); shd.set(qn("w:val"), "clear")
        shd.set(qn("w:fill"), "DCE6F1"); cell._tc.get_or_add_tcPr().append(shd)
    for i, row in enumerate(body):
        for j in range(len(header)):
            cell = t.rows[i + 1].cells[j]
            cell.text = ""
            add_runs(cell.paragraphs[0], row[j] if j < len(row) else "")
    doc.add_paragraph()


def render_md(doc, md_text, base_dir):
    lines = md_text.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i]
        s = ln.strip()
        if not s:
            i += 1; continue
        if s.startswith("```"):
            block = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i]); i += 1
            i += 1
            for code_ln in block:
                p = doc.add_paragraph()
                pf = p.paragraph_format
                pf.left_indent = Cm(0.6); pf.space_after = Pt(0)
                r = p.add_run(code_ln.replace("\t", "    "))
                set_run_fonts(r, latin="Consolas", cjk=CJK_FONT)
                r.font.size = Pt(9.5)
                r.font.color.rgb = RGBColor(0x33, 0x41, 0x55)
                shd = OxmlElement("w:shd"); shd.set(qn("w:val"), "clear")
                shd.set(qn("w:fill"), "F5F5F5")
                p._p.get_or_add_pPr().append(shd)
            doc.add_paragraph()
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", s)
        if m:
            level = len(m.group(1))
            h = doc.add_heading("", level=level)
            add_runs(h, m.group(2))
            i += 1; continue
        img = re.match(r"^!\[(.*?)\]\((.*?)\)$", s)
        if img:
            alt, rel = img.group(1), img.group(2)
            path = (base_dir / rel).resolve()
            if not path.exists():
                raise FileNotFoundError(f"插图缺失: {path}")
            p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.add_run().add_picture(str(path), width=Cm(14.5))
            cap = doc.add_paragraph(); cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
            r = cap.add_run(alt); r.font.size = Pt(9); r.italic = True
            r.font.color.rgb = RGBColor(0x60, 0x60, 0x60); set_run_fonts(r)
            i += 1; continue
        if s.startswith("|"):
            tbl = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                tbl.append(lines[i].strip()); i += 1
            add_table(doc, tbl)
            continue
        if s.startswith(">"):
            p = doc.add_paragraph(style="Intense Quote")
            add_runs(p, s.lstrip("> ").strip()); i += 1; continue
        lm = re.match(r"^[-*]\s+(.*)$", s)
        nm = re.match(r"^(\d+)\.\s+(.*)$", s)
        if lm or nm:
            style = "List Bullet" if lm else "List Number"
            p = doc.add_paragraph(style=style)
            add_runs(p, (lm.group(1) if lm else nm.group(2)))
            i += 1; continue
        p = doc.add_paragraph()
        p.paragraph_format.first_line_indent = Cm(0.74)  # 中文首行缩进2字符
        p.paragraph_format.space_after = Pt(4)
        add_runs(p, s)
        i += 1


def add_toc_field(doc):
    p = doc.add_paragraph()
    r = p.add_run()
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), r'TOC \o "1-2" \h \\* MERGEFORMAT')
    inner = OxmlElement("w:r"); t = OxmlElement("w:t")
    t.text = "（目录：在 Word 中右键 → 更新域，即可自动生成）"
    inner.append(t); fld.append(inner)
    p._p.append(fld)


def force_east_asia_cjk(doc, cjk=CJK_FONT):
    """把文档中所有 rFonts（styles.xml + document.xml，含表格/页眉）的 eastAsia
    统一改为中文字体，清除 python-docx 默认模板遗留的 eastAsia="en-US"，
    避免某些 Word 版本据此用非 CJK 字体渲染中文而出现方框。"""
    from docx.document import Document as _Doc
    part = doc.part if isinstance(doc, _Doc) else None
    roots = []
    import docx.oxml as oxml_module
    # styles.xml
    styles_el = doc.styles.element
    roots.append(styles_el)
    # document body（含表格、图片题注等全部 run）
    roots.append(doc.element.body)
    for root in roots:
        # 1) 所有 rFonts：eastAsia 统一为 CJK 字体，并移除 eastAsiaTheme 引用
        for rf in root.iter(qn("w:rFonts")):
            v = rf.get(qn("w:eastAsia"))
            if v is None or v == "en-US" or not any("\u4e00" <= ch <= "\u9fff" for ch in v):
                rf.set(qn("w:eastAsia"), cjk)
            if rf.get(qn("w:eastAsiaTheme")) is not None:
                del rf.attrib[qn("w:eastAsiaTheme")]
        # 2) docDefaults 的 w:lang：把东亚语言标记改为 zh-CN，避免按 en-US 处理中文
        for lang in root.iter(qn("w:lang")):
            if lang.get(qn("w:eastAsia")) in ("en-US", None):
                lang.set(qn("w:eastAsia"), "zh-CN")


def main():
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)
    sec.left_margin = sec.right_margin = Cm(2.4)

    style_font(doc, "Normal", size=11)
    style_font(doc, "Title", size=28, bold=True, color=(0x1F, 0x3B, 0x73))
    for lvl, sz in ((1, 17), (2, 14), (3, 12), (4, 11)):
        style_font(doc, f"Heading {lvl}", size=sz, bold=True,
                   color=((0x1F, 0x3B, 0x73) if lvl == 1 else (0x2E, 0x5A, 0x9E)))
    for s in ("List Bullet", "List Number", "Intense Quote", "Caption"):
        try: style_font(doc, s)
        except KeyError: pass

    # ---- 封面 ----
    for _ in range(6): doc.add_paragraph()
    t = doc.add_paragraph(style="Title"); t.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = t.add_run("AI Agent 实战"); set_run_fonts(r)
    sub = doc.add_paragraph(); sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = sub.add_run("从架构原理到工程落地"); r.font.size = Pt(15)
    r.font.color.rgb = RGBColor(0x55, 0x55, 0x55); set_run_fonts(r)
    v = doc.add_paragraph(); v.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = v.add_run("v1.0 · 2026年10月"); r.font.size = Pt(11)
    r.font.color.rgb = RGBColor(0x88, 0x88, 0x88); set_run_fonts(r)
    doc.add_page_break()

    # ---- 目录页 ----
    h = doc.add_heading("", level=1); add_runs(h, "目录")
    add_toc_field(doc)
    doc.add_page_break()

    # ---- 正文 ----
    for idx, ch in enumerate(CHAPTERS):
        render_md(doc, ch.read_text(encoding="utf-8"), BASE)
        if idx != len(CHAPTERS) - 1:
            doc.add_page_break()

    force_east_asia_cjk(doc)
    doc.save(OUT)
    print(f"[saved] {OUT}")


if __name__ == "__main__":
    main()
