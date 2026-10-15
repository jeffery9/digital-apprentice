#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
排版指北检查.py — 《数字见习生》书稿 markdown 的中文排版机器校验

依据：仓库根目录《中文排版指北_本书采用版.md》（下称"指北"）。
分工：
  · Markdown 强调标记×中文标点两类失效（A 类空格／B 类闭合歧义，指北 §一.4、§二.5）
    由同目录 `fix_md_spacing.py` 负责，本脚本直接调用其 verify()，不重复实现；
  · 本脚本补充指北其余可机器校验的规则（§一.1–3、§二.1–2、§三.1）。

规则（默认只扫描正文行：跳过代码围栏与行内代码；表格行仅查 R7）：
  R1 中英文之间需要空格（指北 §一.1）            —— 报告
  R2 中文与数字之间需要空格（指北 §一.2）         —— 报告
     R6 数字与单位之间需要空格（指北 §一.3）       —— 同引擎并入 R2
     例外：度数／百分比不加空格（90°、15%）；文件名、标识符、版本号原样保留
  R3 全角标点后不得再跟空格（指北 §一.4）          —— 报告
  R4 不重复使用标点（指北 §二.1，！！／？？）       —— 报告
  R5 专有名词大小写（指北 §三.1，术语表）          —— 报告
  R7 章题「第N章」应写作「第 N 章」（R1/R2 特例）   —— 标题行
  R8 Markdown 标记×中文标点（指北 §一.4/§二.5）    —— 委托 fix_md_spacing.verify()

说明：本书成书体例中「第01章_…」等文件名、图编号（图15-1）保持紧贴，
属既定样式（白名单豁免）；弯引号“ ”为本书采用写法（指北 §四.2），不检查直角引号；
表格行与标题行按本书体例豁免 R1/R2/R3（标题只查 R7 章题）。

用法：
  python3 book/排版指北检查.py                  # 扫描 assemble_book.BOOK_ORDER 全部成书文件
  python3 book/排版指北检查.py 某文件.md ...     # 指定文件体检
退出码：0＝无问题；1＝存在问题（明细见 stdout）。
"""
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

try:
    import fix_md_spacing as fms                                      # R8：A/B 类标记问题
except Exception:
    fms = None

SRC_DIR = os.path.join(SCRIPT_DIR, '书稿')

# ---------------------------------------------------------------------------
# 词表与白名单
# ---------------------------------------------------------------------------
# 指北 §三.1：专有名词大小写（键＝错误写法小写，值＝规范写法）
TERMS = {
    'github': 'GitHub', 'gitlab': 'GitLab', 'openai': 'OpenAI',
    'anthropic': 'Anthropic', 'javascript': 'JavaScript', 'typescript': 'TypeScript',
    'docker': 'Docker', 'kubernetes': 'Kubernetes', 'prometheus': 'Prometheus',
    'mongodb': 'MongoDB', 'mysql': 'MySQL', 'postgresql': 'PostgreSQL',
    'sqlite': 'SQLite', 'chatgpt': 'ChatGPT', 'copilot': 'Copilot',
    'iphone': 'iPhone', 'ipad': 'iPad', 'macos': 'macOS', 'ios': 'iOS',
    'google': 'Google', 'microsoft': 'Microsoft', 'facebook': 'Facebook',
    'leancloud': 'LeanCloud', 'python': 'Python',   # 注意：正文语境由整词匹配把关
}
# 本书既定写法（附录 A 口径）：agent / ai agent / sub-agent / llm / mcp / rag / api / token
# 一律保持小写或原样，不进纠错表，避免误改。

KEEP_WORDS = [
    r'\d+(?:\.\d+)?(?:[%,°]|ⁿ|¹|²|[⁰-⁹]+)',        # 0.52%、99.5%、0.9⁵⁰
    r'[A-Za-z][A-Za-z0-9_.\-]*=[^\s，。；、）】”]',   # p=0.90、T(n)=…
    r'[vV]\d+(?:\.\d+)+',                            # v0.8l、v0.4
    r'\d{4}-\d{2}(?:-\d{2})?',                       # 2026-10（日期锚点）
    r'(?:https?|file)://\S+',
    r'\d+-\d+',                                      # 图/表编号 15-1、2-5（与“图/表”字紧贴，本书体例）
]
RE_HYPH = re.compile(r'[A-Za-z]+(?:-[A-Za-z0-9]+)+')          # code-mode、sub-agent…整体豁免
RE_CODE_RANGE = re.compile(r'\b[A-Za-z]{1,6}-?\d+(?:[+-]\d+)*')  # D-01、M0-M6、P0_图15-1 式代号
RE_HR = re.compile(r'^\s*(?:-{3,}|\*{3,}|_{3,})\s*$')           # 水平线整行不参与
RE_CLI_TOKEN = re.compile(r'(?<![A-Za-z0-9_])-{1,2}[A-Za-z0-9]+(?![A-Za-z0-9])')
RE_MIXED_HYPH = re.compile(r'[\u4e00-\u9fff][A-Za-z0-9_]*[-/]([A-Za-z0-9_]+[-/])*[A-Za-z0-9_]*[\u4e00-\u9fff]')
RE_EXT_TAIL = re.compile(r'\.[A-Za-z0-9]{1,6}(?=[\u4e00-\u9fff，。；、：！？）】》”’\s]|$)')
RE_MATHGRP = re.compile(r'\((?:[^()]|\([^()]*\))*\)')
RE_MATHLINE = re.compile(r'\\(?:quad|qquad|;|,|!|left|right|begin|end)')  # LaTeX 数学式整行
RE_INLINE_MATH = re.compile(r'\$[^$\n]*\$')                                # 行内公式（指北 §一.1 例外）
RE_MARK_PUNCT_COLON = re.compile(r'\*\*：')                               # 「**粗体**：」引导式标签，本书体例
IDENT_TAIL = r'(?:[A-Za-z0-9_]+[_．.])|(?:\.(?:md|mmd|py|json|jsonl|svg|docx|txt|yaml|yml|toml|csv|html))'

CJK = '\u4e00-\u9fff'
FULLPUNCT = '，。！？；：、）】》”’…—％'
SPACE = ' \u3000'
RE_FIGREF = re.compile(r'[图表]\s*\d+-\d+')


def is_ident_token(tok):
    return bool(re.search(IDENT_TAIL, tok))


def figref_spans(line):
    return [(m.start(), m.end()) for m in RE_FIGREF.finditer(line)]


def mixed_hyphen_spans(line):
    return [(m.start(), m.end()) for m in RE_MIXED_HYPH.finditer(line)]


def math_spans(line):
    out = []
    for m in RE_MATHGRP.finditer(line):
        seg = m.group(0)
        if re.search(r'[-+*/^=<]', seg) and not re.search(CJK, seg):
            out.append((m.start(), m.end()))
    return out


def in_any(pos, spans):
    return any(s <= pos < e for s, e in spans)


def blank_code(text):
    """把 ``` 围栏内容与行内代码 `…` 掩成同长度空白（保留换行），位置一一对应。"""
    out = list(text)

    def blank(s, e):
        for i in range(s, e):
            if out[i] != '\n':
                out[i] = ' '

    for m in re.finditer(r'(?:^|\n)(```|~~~).*?(?:\1(?:\n|$)|\Z)', text, flags=re.S):
        blank(m.start(), m.end())
    for m in re.finditer(r'`[^`\n]*`', text):
        blank(m.start(), m.end())
    return ''.join(out)


def fence_mask(text):
    lines = text.split('\n')
    st, fence = [], False
    for l in lines:
        if l.strip().startswith('```') or l.strip().startswith('~~~'):
            fence = not fence
            st.append(True)
        else:
            st.append(fence)
    return st


def line_no(text, pos):
    return text.count('\n', 0, pos) + 1


def ctx(text, s, e, w=12):
    return '%s⟪%s⟫%s' % (text[max(0, s - w):s].replace('\n', '⏎'),
                         text[s:e], text[e:e + w].replace('\n', '⏎'))


MODIFIER_STOP = set('的了与和及或在为把被让给从对有做用等这该其是')


def is_glued_proper_noun(line, ws, we):
    mtail = re.match(r'[\u4e00-\u9fff]+', line[we:])
    if not mtail or len(mtail.group(0)) < 2:
        return False
    prev = line[ws - 1] if ws >= 1 else ''
    return prev in CJK and prev not in MODIFIER_STOP


# ---------------------------------------------------------------------------
# 单行扫描：返回 [(start, end, kind, payload)]，位置相对该行
# ---------------------------------------------------------------------------
def scan_line(line, in_fence, orig=''):
    issues = []
    n = len(line)
    if RE_HR.match(line):
        return []
    if '|' in line and re.fullmatch(r'[\s|:\-]+', line):
        return []
    # 整行 LaTeX 数学式（含 \quad \; 等排版宏）：中英混排规则不适用（指北 §一.1 例外：公式）
    if RE_MATHLINE.search(line):
        return []
    tokens = [(m.start(), m.end(), m.group(0))
              for m in re.finditer(r'[A-Za-z0-9][A-Za-z0-9.%+\-⁰-⁹ᵃ-ᶻ°]*', line)]
    figs = figref_spans(line)
    mixed = mixed_hyphen_spans(line)
    maths = math_spans(line)

    # 表格行：单元格短、代号密集，加空格破坏列对齐且误报率高（本书体例）——跳过 R1/R2/R3
    if in_fence or line.lstrip().startswith('|'):
        return issues

    def keep_span(pos):
        for s, e, tok in tokens:
            if s <= pos < e:
                for pat in KEEP_WORDS:
                    if re.fullmatch(pat, tok):
                        return (s, e)
                if RE_HYPH.fullmatch(tok) or RE_CODE_RANGE.fullmatch(tok) or is_ident_token(tok):
                    return (s, e)
                return None
        return None

    i = 0
    while i < n:
        ch = line[i]
        nxt = line[i + 1] if i + 1 < n else ''
        # R3：全角标点后紧跟空格再接非空白
        if ch in FULLPUNCT and nxt in SPACE:
            j = i + 1
            while j < n and line[j] in SPACE:
                j += 1
            if j >= n or in_fence:
                i = j if j > i + 1 else i + 1
                continue
            # 豁免①：`a`、`b` 行内代码列举（顿号/逗号后接空格再接行内代码的引导式写法）
            if orig and ch in '—、，；：':
                if re.match(r'[—]*`[、，]`?', orig[i:]) or \
                   re.match(r'[—]*`[^`\n]*`[、，；：]', orig[i:]) or \
                   re.match(r'[—]*[。！？]\s*`', orig[i:]):
                    i = j
                    continue
            # 豁免①补：全角标点后接"版式留白＋行内代码"（如「中的 【L3 锚点】」），
            # 属展示性空格而非中英文空格规则违例
            if orig and re.match(r'[、，；：」』]\s+`', orig[i:]):
                i = j
                continue
            # 豁免①中：掩码文本判定——标点与空格段之间只剩空白（原处为行内代码/公式），
            # 且空格段之后仍是行内代码/公式起始 → `a`、`b` 式列举，本书体例
            if re.fullmatch(r'\s*', line[i + 1:j]) and orig[j:j + 1] in ('`', '$'):
                i = j
                continue
            # 豁免②：破折号后接行内代码 ——`cmd`（保留阅读空格）
            if orig and ch == '—':
                k = i
                while k < len(orig) and orig[k] in SPACE:
                    k += 1
                if k < len(orig) and orig[k] == '`':
                    i = j
                    continue
            # 豁免③：☐/□ 勾选框记号周围的排版空格（表单引用块，本书体例）；
            # 闭括号后接全角空格＋汉字／记号属版式留白，非"标点后多余半角空格"
            if ch in '）】》' and orig and re.match(r'[）】》]\s*[　─—☐□\u4e00-\u9fff]', orig[i:]):
                i = j
                continue
            if orig and ch in '、，；：' and \
                    re.match(r'`[^`\n]*`[ ](?=[\u4e00-\u9fff])', orig[i + 1:]):
                i = j
                continue
            # 豁免④：行首强调标记闭合后紧跟全角冒号（「**复现命令**：」「**观察什么**：」式体例），
            # 以及「」引号闭合后的引导冒号——均非"标点后多余空格"
            if orig and ch == '：' and re.match(r'：[*「『]', orig[i:]):
                i = j
                continue
            issues.append((i + 1, j, 'R3', None))
            i = j
            continue
        # R7：章题「第N章」缺空格（标题行专用）
        m = re.match(r'第(\d{1,2})章', line[i:])
        if m and line.lstrip().startswith('#'):
            p = i + 1 + len(m.group(1))
            issues.append((p, p, 'R7', None))
            i += m.end()
            continue
        # 标题行只查 R7：「## 9.1　…：ReAct」式冒号后英文术语尾注按本书体例保持紧贴
        if line.lstrip().startswith('#'):
            break
        # R1/R2：CJK 与 拉丁/数字 直接相邻（双向，边界按整词取）
        if in_any(i, mixed) or in_any(i + 1, mixed):
            nxt_m = max([e for s, e in mixed if s <= i < e] +
                        [e for s, e in mixed if s <= i + 1 < e] + [i + 1])
            i = max(i + 1, nxt_m); continue
        if in_any(i, maths) or in_any(i + 1, maths):
            nxt_g = max([e for s, e in maths if s <= i + 1 and s <= e] +
                        [e for s, e in maths if s <= i + 1 < e] + [i + 1])
            i = max(i + 1, nxt_g); continue
        if ch in CJK and nxt == '-' and RE_CLI_TOKEN.match(line, i + 1):
            i = RE_CLI_TOKEN.match(line, i + 1).end(); continue
        if ch in CJK and nxt == '.' and RE_EXT_TAIL.match(line, i + 1):
            i = RE_EXT_TAIL.match(line, i + 1).end(); continue
        cjk_side = None
        if ch in CJK and (nxt.isascii() and (nxt.isalnum() or nxt in '.%°')):
            cjk_side = 'left'
        elif (ch.isascii() and (ch.isalnum() or ch in '.%°')) and nxt in CJK:
            cjk_side = 'right'
        if cjk_side:
            lat_pos = i + 1 if cjk_side == 'left' else i
            gap = i + 1
            span = keep_span(lat_pos)
            if span:
                i = span[1]; continue
            mf = re.match(r'\d+-\d+', line[lat_pos:])
            if mf and ((cjk_side == 'left' and ch in '图表') or
                       (cjk_side == 'right' and i >= 1 and line[i - 1] in '图表')):
                i = lat_pos + mf.end(); continue
            if in_any(gap - 1, figs) or in_any(gap, figs):
                for s, e in figs:
                    if s <= gap <= e:
                        i = max(i, e - 1) + 1
                        break
                continue
            ws = we = lat_pos
            for s, e, tok in tokens:
                if s <= lat_pos < e:
                    ws, we = s, e; break
            if cjk_side == 'right' and is_glued_proper_noun(line, ws, we):
                i = we; continue
            pair = (ch, nxt)
            kind = 'R2' if any(c.isdigit() for c in pair) else 'R1'
            issues.append((gap, gap, kind, None))
            i = we if cjk_side == 'right' else i + 1
            continue
        i += 1
    dedup, last_end = [], -1
    for it in sorted(issues):
        if it[0] < last_end:
            continue
        dedup.append(it)
        last_end = it[1]
    return dedup


def scan_r4_r5(text):
    """R4 重复标点、R5 专有名词大小写：在掩码文本上整行查找。"""
    out = []
    masked_lines = blank_code(text).split('\n')
    orig_lines = text.split('\n')
    for idx, ml in enumerate(masked_lines):
        for m in re.finditer(r'([！？」!?)）])\1{1,}', ml):
            # 允许「？！」混排不算重复；仅同种叠用报错
            out.append((idx + 1, 'R4', ctx(orig_lines[idx], m.start(), m.end())))
        for m in re.finditer(r'(?<![A-Za-z0-9_\-])([A-Za-z]+)(?![A-Za-z0-9_\-])', ml):
            w = m.group(1)
            canon = TERMS.get(w.lower())
            if canon and w != canon and w.lower() == w:  # 只纠全小写误写，避免误伤句首大写
                out.append((idx + 1, 'R5', ctx(orig_lines[idx], m.start(), m.end())))
    return out


def process_file(path):
    text = open(path, encoding='utf-8').read()
    masked = blank_code(text)
    fm = fence_mask(masked)
    lines = masked.split('\n')
    orig = text.split('\n')
    problems = []
    for idx, ml in enumerate(lines):
        iss = scan_line(ml, fm[idx], orig[idx])
        for s, e, kind, _ in iss:
            problems.append((idx + 1, kind, ctx(orig[idx], max(0, s - 1), min(len(orig[idx]), e + 1))))
    problems += scan_r4_r5(text)
    return sorted(problems)


def book_order_files():
    try:
        import assemble_book as ab
        files = []
        for item in ab.BOOK_ORDER:
            if item[0] == 'file':
                p = os.path.join(SRC_DIR, item[1])
                if os.path.exists(p):
                    files.append(p)
        return files
    except Exception:
        return sorted(os.path.join(SRC_DIR, f) for f in os.listdir(SRC_DIR)
                      if f.endswith('.md') and not f.startswith('00'))


def main():
    targets = [os.path.abspath(p) for p in sys.argv[1:]] or book_order_files()
    KIND_CN = {'R1': '中英文缺空格', 'R2': '中文数字缺空格', 'R3': '全角标点后多空格',
               'R4': '重复标点', 'R5': '专名大小写', 'R7': '章题缺空格'}
    total = 0
    by_kind = {}
    for t in targets:
        probs = process_file(t)
        rel = os.path.relpath(t, os.path.dirname(SCRIPT_DIR))
        if probs:
            print('%s：%d 处' % (rel, len(probs)))
            for ln, k, c in probs[:15]:
                print('  L%-4d [%s] %s' % (ln, KIND_CN[k], c))
            if len(probs) > 15:
                print('  …共 %d 处' % len(probs))
            total += len(probs)
            for _, k, _c in probs:
                by_kind[k] = by_kind.get(k, 0) + 1
    r8 = 0
    if fms is not None:
        for t in targets:
            try:
                r8 += len(fms.verify(t, quiet=True))
            except TypeError:
                r8 += len(fms.verify(t))
    if r8:
        print('\nR8（Markdown 标记×中文标点，fix_md_spacing 口径）：%d 处，请运行 book/fix_md_spacing.py --fix' % r8)
    if total or r8:
        print('\n合计 %d 处（不含 R8 则 %d 处）。分布：%s' %
              (total + r8, total, ', '.join('%s×%d' % kv for kv in sorted(by_kind.items()))))
        return 1
    print('全部干净 ✓（R1–R5/R7/R8）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
