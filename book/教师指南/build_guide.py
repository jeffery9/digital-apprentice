#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
《数字见习生》教师指南 · 独立成册构建脚本
==========================================

用途：把 教师指南_源稿.md 组装为出版级 docx（与主书同一管线：
pandoc + 中文样式模板 + 静态目录 + 表格边框 + 页脚页码）。

定位（源稿卷首自述）：教师指南是附录 E／F 的展开与落地，
**独立成册、不并入学生用书**——因此本脚本独立于 assemble_book.py，
不改动 BOOK_ORDER，不影响主书版本号。

用法：
  python3 build_guide.py            # 生成 《数字见习生》教师指南.docx
  python3 build_guide.py --check    # 生成后与 收件原件 做全文比对（只读校验）

依赖：python3、pypandoc-binary、python-docx（与主书相同）
纪律：源稿神圣——本脚本对 教师指南_源稿.md 与 收件原件.docx 只读；
      请勿直接编辑产物 docx，改内容一律改 md 源稿后重跑本脚本。
"""

import argparse
import os
import sys

import pypandoc
from docx import Document

GUIDE_VERSION = 'v1.0'          # 教师指南自身版本，与主书版本号无关
HERE = os.path.dirname(os.path.abspath(__file__))
BOOK_DIR = os.path.dirname(HERE)
SRC_MD = os.path.join(HERE, '教师指南_源稿.md')
OUT_DOCX = os.path.join(HERE, '《数字见习生》教师指南.docx')
REF_DOCX = os.path.join(HERE, '_样式模板_reference.docx')
ORIGINAL_DOCX = os.path.join(HERE, '《数字见习生》教师指南_收件原件.docx')

sys.path.insert(0, BOOK_DIR)
from assemble_book import build_reference_docx, postprocess_docx, log  # noqa: E402


def linear_text(docx_path, skip_toc=True):
    """按文档顺序抽取段落与表格的线性文本（跳过目录区），用于比对。"""
    doc = Document(docx_path)
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    out = []
    for child in doc.element.body.iterchildren():
        tag = child.tag.split('}')[1]
        if tag == 'p':
            p = Paragraph(child, doc)
            style = p.style.name if p.style is not None else ''
            if skip_toc and style.startswith('TOC'):
                continue
            if p.text.strip():
                out.append(p.text.strip())
        elif tag == 'tbl':
            t = Table(child, doc)
            for row in t.rows:
                cells = [c.text.strip().replace('\n', ' ') for c in row.cells]
                if any(cells):
                    out.append(' | '.join(cells))
    return out


def build():
    log(f'【1/4】样式模板 …')
    build_reference_docx(REF_DOCX)
    log('【2/4】pandoc 转换 …')
    extra = ['--quiet', '--reference-doc', REF_DOCX,
             '--toc', '--toc-depth', '3']
    pypandoc.convert_file(
        SRC_MD, 'docx', format='markdown+east_asian_line_breaks-smart',
        outputfile=OUT_DOCX, extra_args=extra)
    log('【3/4】出版级后处理（静态目录/表格边框/页脚页码/A4）…')
    pp = postprocess_docx(OUT_DOCX, toc=True, toc_depth=3)
    log(f"  ✓ 表格 {pp['tables']} 张｜静态目录 {pp['toc_entries']} 条")
    log('【4/4】写元数据 …')
    doc = Document(OUT_DOCX)
    cp = doc.core_properties
    cp.title = '《数字见习生》教师指南'
    cp.subject = f'教师指南 {GUIDE_VERSION}（独立成册，不并入学生用书）'
    cp.comments = ('由 build_guide.py 组装生成；源文件为同目录 教师指南_源稿.md，'
                   '请勿直接编辑本文件。')
    doc.save(OUT_DOCX)
    size_kb = os.path.getsize(OUT_DOCX) / 1e3
    log(f'  ✓ {os.path.basename(OUT_DOCX)}（{size_kb:.0f} KB，{GUIDE_VERSION}）')
    return OUT_DOCX


def check():
    """与收件原件逐段比对（内容层面应完全一致；目录/页脚为构建层不计）。"""
    a = linear_text(ORIGINAL_DOCX)
    b = linear_text(OUT_DOCX)
    log(f'比对：原件 {len(a)} 段行 vs 重建 {len(b)} 段行')
    diffs = 0
    for i in range(max(len(a), len(b))):
        ta = a[i] if i < len(a) else '<缺>'
        tb = b[i] if i < len(b) else '<缺>'
        if ta != tb:
            diffs += 1
            log(f'  ✗ 第 {i + 1} 段不一致：')
            log(f'    原件：{ta[:60]}')
            log(f'    重建：{tb[:60]}')
            if diffs >= 8:
                log('  …（其余差异从略）')
                break
    if diffs == 0:
        log('  ✓ 全文逐段一致（0 差异）')
    return diffs


def main():
    ap = argparse.ArgumentParser(description='教师指南独立构建')
    ap.add_argument('--check', action='store_true',
                    help='构建后与收件原件做全文比对')
    args = ap.parse_args()
    build()
    if args.check:
        return 1 if check() else 0
    return 0


if __name__ == '__main__':
    sys.exit(main())
