#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
排版指北检查.py — 《AI Agent 架构决策指南》书稿的中文排版机器校验／修复

依据：仓库根目录《中文排版指北_本书采用版.md》（下称"指北"）。
分工：
  · Markdown 强调标记×中文标点两类失效（A 类空格／B 类闭合歧义）由前作自带的
    `book/fix_md_spacing.py` 负责（指北 §一.4、§二.5），本脚本会直接调用它，不重复实现；
  · 本脚本补充指北其余可机器校验的规则（§一.1–3、§二.1–2、§三.1）。

规则（默认只扫描正文行：跳过代码围栏与行内代码；表格行仅查 R7）：
  R1 中英文之间需要空格（指北 §一.1）           —— 报告；--fix 自动加空格
  R2 中文与数字之间需要空格（指北 §一.2）        —— 报告；--fix 自动加空格
     R6 数字与单位之间需要空格（指北 §一.3）      —— 同引擎并入 R2
     例外：度数／百分比不加空格（90°、15%）；文件名、标识符、版本号原样保留
  R3 全角标点与其他字符之间不加空格（指北 §一.4） —— 报告；--fix 删除标点后的空格
     （代码块内的对齐缩进除外——示例输出排版不动）
  R4 不重复使用标点（指北 §二.1，！！／？？）      —— 只报告，人工定夺
  R5 专有名词大小写（指北 §三.1，术语表）         —— 报告；--fix 按词表纠正
  R7 章题「第N章」应写作「第 N 章」（R1/R2 特例）  —— 标题行；--fix 自动补空格
  R8 Markdown 标记×中文标点（指北 §一.4/§二.5）   —— 委托 fix_md_spacing.verify()

白名单（视为一个不可拆的整体 token，两侧不加空格、内部不动）：
  路径与文件名（含 / 、 . 扩展名或 _ 的片段）、版本/编号（v0.4、p=0.90、0.9⁵⁰、
  2026-10、D-01…）、百分号/度数前后粘连（85%）、CJK-CJK-拉丁专名（裸引成功率等
  "拉丁+中文后缀"整体）、以及 TERMS 中显式登记的复合术语。

用法：
  python3 工位/排版指北检查.py                 # 扫描 书稿/*.md＋模式卡，列问题（有则退出码 1）
  python3 工位/排版指北检查.py --fix           # 修复 R1/R2/R3/R7（迭代至收敛后写回）
  python3 工位/排版指北检查.py --fix --rebuild # 修复后重跑 assemble_agentbook.py 重建合并版/docx
  python3 工位/排版指北检查.py 某文件.md ...    # 指定文件体检／修复
退出码：0＝无问题（或修复后无残留）；1＝仍有未处理问题。
"""
import argparse
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # agentbook/
BOOK_FIXER = '/workspace/book'                                        # 前作 fix_md_spacing.py 所在目录
sys.path.insert(0, BOOK_FIXER)
try:
    import fix_md_spacing as fms                                      # R8：A/B 类标记问题
except Exception:
    fms = None

# ---------------------------------------------------------------------------
# 词表与白名单
# ---------------------------------------------------------------------------
# 指北 §三.1：专有名词大小写（键＝错误写法小写，值＝规范写法）
TERMS = {
    'github': 'GitHub', 'gitlab': 'GitLab', 'openai': 'OpenAI',
    'anthropic': 'Anthropic', 'javascript': 'JavaScript', 'typescript': 'TypeScript',
    'python': 'Python', 'docker': 'Docker', 'kubernetes': 'Kubernetes',
    'prometheus': 'Prometheus', 'datadog': 'Datadog', 'newman': 'Newman',
    'mongodb': 'MongoDB', 'mysql': 'MySQL', 'postgresql': 'PostgreSQL',
    'sqlite': 'SQLite', 'webapp': 'WebApp',
}
# 本书既定写法（附录 A 口径）：agent / ai agent / sub-agent / llm / mcp / a2a / rag / api / token …
# 一律保持小写或原样，不进纠错表，避免误改。

# 明确不参与 R1/R2 加空格的"整词"（标识符、专名、度量记法）
KEEP_WORDS = [
    r'\d+(?:\.\d+)?(?:[%,°]|ⁿ|¹|²|[⁰-⁹]+)',      # 0.52%、99.5%、pⁿ、0.9⁵⁰
    r'[A-Za-z][A-Za-z0-9_.\-]*=[^\s，。；、）】”]',  # p=0.90、T(n)=…
    r'[vV]\d+(?:\.\d+)+',                          # v0.4
    r'\d{4}-\d{2}(?:-\d{2})?',                     # 2026-10（日期锚点）
    r'[DＤ]-?\d{2}',                               # D-01 / D01
    r'(?:https?|file)://\S+',
    r'\d+-\d+',                                    # 图/表编号 1-1、2-5（与“图/表”字紧贴，本书体例）
]
# 连字符复合英文词整体豁免：code-mode、Self-Refine、plan-then-execute、sub-agent……
RE_HYPH = re.compile(r'[A-Za-z]+(?:-[A-Za-z0-9]+)+')
# CJK–拉丁–CJK 混排连字符复合词（步数-token-恢复／准确率-token-延迟）：
# 三侧均紧贴为本书既定样式；--fix 把残留的"字内空格"归一为紧贴（幂等，不再反复报修）
RE_MIXED_HYPH = re.compile(r'[\u4e00-\u9fff][A-Za-z0-9]*\s*-\s*(?:[A-Za-z0-9]+\s*-\s*)+[\u4e00-\u9fff]')
# 数学式整体豁免：括号配对且含运算符的拉丁式段（如 (1 -(1 -p)^K)），按公式原样保留
RE_MATHGRP = re.compile(r'\((?:[^()]|\([^()]*\))*\)')
# 数字区间「200-400ms」：fix 时把 --fix 误插的区间两侧空格收回（区间号与单位保持紧贴）
RE_NUMRANGE_FIX = re.compile(r'(?<=\d)\s+-(?=\d)|(?<=\d)-\s+(?=\d\d)')
# 文件名／路径片段：出现 .ext 或 _ 或 / 即视为标识符整体，不拆开加空格
IDENT_TAIL = r'(?:[A-Za-z0-9_]+[_．.])|(?:\.(?:md|mmd|py|json|jsonl|svg|docx|txt|yaml|yml|toml|csv|html))'


def is_ident_token(tok):
    """判断一个拉丁/数字串是否属于"不可拆标识符"（文件名、路径、代号）。"""
    if re.search(IDENT_TAIL, tok):
        return True
    # 形如 render_图2-1、code/D-01-最小agent循环 的相邻上下文在行级已保护；此处保守
    return False


CJK = '\u4e00-\u9fff'
FULLPUNCT = '，。！？；：、）】》”’…—％'          # 全角标点后不得再跟空格（§一.4）
SPACE = ' \u3000'

# 图/表编号整体豁免：图 1-1、表 2-5、【图 8-1｜——本书体例中编号与“图/表”字保持紧贴
RE_FIGREF = re.compile(r'[图表]\s*\d+-\d+')


def figref_spans(line):
    return [(m.start(), m.end()) for m in RE_FIGREF.finditer(line)]


def mixed_hyphen_spans(line):
    """CJK–拉丁–CJK 混排连字符复合词（步数-token-恢复）的整段豁免区间。"""
    return [(m.start(), m.end()) for m in RE_MIXED_HYPH.finditer(line)]


def math_spans(line):
    """括号配对且含运算符/字母的数学式段（如 (1 -(1 -p)^K)），整段不参与 R1/R2。"""
    out = []
    for m in RE_MATHGRP.finditer(line):
        seg = m.group(0)
        if re.search(r'[-+*/^=<]', seg) and not re.search(CJK, seg):
            out.append((m.start(), m.end()))
    return out


def in_any(pos, spans):
    return any(s <= pos < e for s, e in spans)


# R3 豁免①：行内代码之间的顿号/逗号排版（`a`、`b`）属合法列举样式
RE_CODE_SEP = re.compile(r'(?<=`)[、，](?=`)')
# R3 豁免②：破折号后紧跟行内代码（——`cmd`），代码片段前保留阅读空格
RE_DASH_CODE = re.compile(r'—{1,2}(?=`)')
# R1/R2 豁免：拉丁词以 ≥2 个汉字收尾的粘连专名（重构 agent／指数级 token 等按整词处理见 scan）

def blank_code(text):
    """把 ``` 围栏内容与行内代码 `…` 掩成同长度空白（保留换行），位置一一对应。"""
    out = list(text)

    def blank(s, e, keep_nl=True):
        for i in range(s, e):
            if out[i] != '\n' or not keep_nl:
                out[i] = ' ' if out[i] != '\n' else '\n'

    for m in re.finditer(r'(?:^|\n)(```|~~~).*?(?:\1(?:\n|$)|\Z)', text, flags=re.S):
        blank(m.start(), m.end())
    for m in re.finditer(r'`[^`\n]*`', text):
        blank(m.start(), m.end())
    return ''.join(out)


def fence_mask(text):
    """返回每行是否处于代码围栏内的布尔列表（用于 R3 区分围栏内外）。"""
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


# 拉丁词紧跟 ≥2 个汉字、且左侧也是汉字时，视为粘连专名整体（如“裸引成功率”）；
# 但“重构 agent”“指数级 token”这类 1–2 字修饰＋术语要正常加空格。
MODIFIER_STOP = set('的了与和及或在为把被让给从对有做用等这该其是')


def is_glued_proper_noun(line, ws, we):
    """ws/we＝拉丁词边界。若其后紧跟 ≥2 汉字、且其前一字符也是汉字，判为专名粘连。"""
    mtail = re.match(r'[\u4e00-\u9fff]+', line[we:])
    if not mtail or len(mtail.group(0)) < 2:
        return False
    prev = line[ws - 1] if ws >= 1 else ''
    if prev in CJK and prev not in MODIFIER_STOP:
        # “判定决策树.mmd”式文件名已由 IDENT_TAIL 处理；此处针对纯文本粘连
        return True
    return False


# ---------------------------------------------------------------------------
# 单行扫描：返回 [(start, end, kind, payload)]，位置相对该行
# ---------------------------------------------------------------------------
def scan_line(line, in_fence, orig=''):
    """line 为掩码行（行内代码已置空、与原文等位）；orig 为同位原稿行，
    供 R3 豁免判断 ` 边界使用。"""
    issues = []
    n = len(line)
    i = 0
    # token：拉丁/数字串（不含 CJK），用于白名单整体豁免判断
    tokens = [(m.start(), m.end(), m.group(0))
              for m in re.finditer(r'[A-Za-z0-9][A-Za-z0-9.%+\-⁰-⁹ᵃ-ᶻ]*', line)]
    figs = figref_spans(line)
    mixed = mixed_hyphen_spans(line)
    maths = math_spans(line)

    def keep_span(pos):
        """pos 落在某个白名单 token 内则返回该 token 区间。"""
        for s, e, tok in tokens:
            if s <= pos < e:
                for pat in KEEP_WORDS:
                    mm = re.fullmatch(pat, tok)
                    if mm:
                        return (s, e)
                if RE_HYPH.fullmatch(tok):      # code-mode / Self-Refine 等复合词整体
                    return (s, e)
                if is_ident_token(tok):
                    return (s, e)
                return None
        return None

    while i < n:
        ch = line[i]
        nxt = line[i + 1] if i + 1 < n else ''
        # R3：全角标点后紧跟空格再接非空白（代码围栏内的对齐缩进除外）
        if ch in FULLPUNCT and nxt in SPACE:
            j = i + 1
            while j < n and line[j] in SPACE:
                j += 1
            if j >= n or in_fence:
                i = j if j > i + 1 else i + 1
                continue
            # 豁免①：`…`、`…`／`…`，`…` 行内代码列举（按原稿字符判断）
            if orig and re.match(r'`[、，]`?', orig[i:i + 4]) and orig[i] == ch:
                i = j
                continue
            # 豁免②：破折号后接行内代码 ——`cmd`／—— `cmd`（保留阅读空格）
            if orig and ch == '—':
                k = i
                while k < len(orig) and orig[k] in SPACE:
                    k += 1
                if k < len(orig) and orig[k] == '`':
                    i = j
                    continue
            issues.append((i + 1, j, 'R3', None))
            i = j
            continue
        # R7：章题「第N章」缺空格（标题行专用，R1/R2 在该处跳过以免重复插空格）
        m = re.match(r'第(\d{1,2})章', line[i:])
        if m and line.lstrip().startswith('#'):
            p = i + 1 + len(m.group(1))       # 「第」占 1 字，数字紧随其后
            issues.append((p, p, 'R7', None))
            i += m.end()
            continue
        # R1/R2：CJK 与 拉丁/数字 直接相邻（双向检查，边界按整词取）
        # 豁免：混排连字符复合词（步数-token-恢复）与数学式段（(1 -(1 -p)^K)）整段不动
        if in_any(i, mixed) or in_any(i + 1, mixed):
            nxt_m = max([e for s, e in mixed if s <= i < e] +
                        [e for s, e in mixed if s <= i + 1 < e] + [i + 1])
            i = max(i + 1, nxt_m); continue
        if in_any(i, maths) or in_any(i + 1, maths):
            nxt_g = max([e for s, e in maths if s <= i < e] +
                        [e for s, e in maths if s <= i + 1 < e] + [i + 1])
            i = max(i + 1, nxt_g); continue
        cjk_side = None
        if ch in CJK and (nxt.isascii() and (nxt.isalnum() or nxt in '.%°')):
            cjk_side = 'left'
        elif (ch.isascii() and (ch.isalnum() or ch in '.%°')) and nxt in CJK:
            cjk_side = 'right'
        if cjk_side:
            lat_pos = i + 1 if cjk_side == 'left' else i
            gap = i + 1                       # 候选插入位点（二者之间）
            span = keep_span(lat_pos)
            if span:                          # 命中白名单：整个 token 连同两侧不动
                i = span[1]
                continue
            # 图/表编号体例（图1-1、表2-5、图表/图1-1_…）：编号与“图/表”字保持紧贴
            mf = re.match(r'\d+-\d+', line[lat_pos:])
            if mf and ((cjk_side == 'left' and ch in '图表') or
                       (cjk_side == 'right' and i >= 1 and line[i - 1] in '图表')):
                i = lat_pos + mf.end()
                continue
            if in_any(gap - 1, figs) or in_any(gap, figs):
                for s, e in figs:
                    if s <= gap <= e:
                        i = max(i, e - 1) + 1
                        break
                continue
            # 取拉丁整词边界；若词尾粘连 ≥2 个汉字（专名后缀，如“裸引成功率”），整体豁免
            ws = we = lat_pos
            for s, e, tok in tokens:
                if s <= lat_pos < e:
                    ws, we = s, e
                    break
            if cjk_side == 'right' and is_glued_proper_noun(line, ws, we):
                i = we                        # 粘连专名整体豁免（如“裸引成功率”）
                continue
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


def apply_fixes(line, issues):
    chars = list(line)
    for s, e, kind, _ in sorted(issues, reverse=True):
        if kind == 'R3':
            del chars[s:e]
        elif kind == 'R7':
            # 问题点 s＝数字结束位；「第N章」→「第 N 章」：数字前后各插一个空格
            digits = re.search(r'(\d+)$', line[:s]).group(1)
            d = s - len(digits)                   # 数字起点（紧随「第」）
            chars.insert(s, ' ')                  # 数字与「章」之间
            chars.insert(d, ' ')                  # 「第」与数字之间
        else:  # R1 / R2
            chars.insert(s, ' ')
    out = ''.join(chars)
    # 数字区间归一：~200 -400ms → ~200-400ms（区间号两侧不加空格，指北 §一.2 例外）
    out = RE_NUMRANGE_FIX.sub('', out)
    return out


def process_file(path, fix=False):
    text = open(path, encoding='utf-8').read()
    masked = blank_code(text)
    fm = fence_mask(masked)
    lines = masked.split('\n')
    orig = text.split('\n')
    problems = []
    new_lines = list(orig)
    for idx, ml in enumerate(lines):
        if ml.strip().startswith('|'):
            continue                      # 表格行不动（列宽对齐属版式约定）
        in_fence = fm[idx]
        iss = scan_line(ml, in_fence, orig[idx])
        for s, e, kind, _ in iss:
            problems.append((idx + 1, kind, ctx(orig[idx], max(0, s - 1), min(len(orig[idx]), e + 1))))
        if fix and iss:
            new_lines[idx] = apply_fixes(orig[idx], iss)
    if fix and new_lines != orig:
        open(path, 'w', encoding='utf-8').write('\n'.join(new_lines))
        return problems
    return problems


def main():
    ap = argparse.ArgumentParser(description='按《中文排版指北（本书采用版）》校验/修复书稿')
    ap.add_argument('paths', nargs='*', help='要处理的 md 文件（默认：书稿全部正稿＋模式卡）')
    ap.add_argument('--fix', action='store_true', help='执行修复（默认只验证）')
    ap.add_argument('--rebuild', action='store_true', help='修复后重跑 assemble_agentbook.py')
    args = ap.parse_args()

    if args.paths:
        targets = [os.path.abspath(p) for p in args.paths]
    else:
        d = os.path.join(ROOT, '书稿')
        targets = sorted([os.path.join(d, f) for f in os.listdir(d)
                          if f.endswith('.md') and not f.startswith('00')])
        targets += sorted(os.path.join(d, '模式卡', f) for f in os.listdir(os.path.join(d, '模式卡'))
                          if f.endswith('.md') and not f.startswith('_'))

    KIND_CN = {'R1': '中英文缺空格', 'R2': '中文数字缺空格', 'R3': '全角标点后多空格',
               'R7': '章题缺空格'}
    total_p = 0
    for t in targets:
        probs = process_file(t, fix=args.fix)
        rel = os.path.relpath(t, ROOT)
        if probs:
            print('%s：%d 处%s' % (rel, len(probs), '（已修复）' if args.fix else ''))
            if not args.fix:
                for ln, k, c in probs[:20]:
                    print('  L%-4d [%s] %s' % (ln, KIND_CN[k], c))
                if len(probs) > 20:
                    print('  …共 %d 处' % len(probs))
            total_p += len(probs)
        if args.fix:                       # 二次收敛检查
            left = process_file(t, fix=False)
            if left:
                print('  ! %s：修复后仍余 %d 处（请人工复核）' % (rel, len(left)))

    # R8：Markdown 标记×中文标点（委托前作脚本，只验证）
    r8 = 0
    if fms is not None:
        for t in targets:
            r8 += len(fms.verify(t, quiet=True))
    if r8:
        print('\nR8（Markdown 标记×中文标点，fix_md_spacing 口径）：%d 处，请运行 book/fix_md_spacing.py --fix' % r8)

    if args.fix and args.rebuild and total_p:
        print('\n== 重建合并版 md/docx ==')
        p = subprocess.run([sys.executable, os.path.join(ROOT, 'assemble_agentbook.py')], cwd=ROOT)
        if p.returncode != 0:
            print('  ! 组装失败'); return 1

    if total_p or r8:
        print('\n合计待处理 %d 处%s。' % (total_p + r8, '' if args.fix else '；运行 --fix 自动修复 R1/R2/R3/R7'))
        return 1 if not args.fix else (1 if total_p else 0)
    print('全部干净 ✓（R1–R3/R7/R8）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
