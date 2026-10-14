#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_md_spacing.py — 验证并修复 Markdown 强调标记在中文语境下的两类失效问题

问题一（空格，A 类）：标记与中文之间夹空格
  源稿约定强调标记紧贴内容书写（**重点**），但历史稿件中混入了
  “** 重点**”“重点 **”这类标记与中文之间夹空格（半角或全角）的写法。
  CommonMark 规定 ** 后紧跟空白时不构成加粗起始，导致渲染出裸星号。

问题二（闭合歧义，B 类）：闭合 ** 后紧跟 CJK 汉字
  形如 “**用得更好。**懂原理的人…”：闭合 ** 前是全角句号（标点）、
  后紧跟汉字。Python-Markdown 3.x 的 DelimiterProcessor 与 CommonMark 的
  left/right-flanking 规则都把这种组合判为“非闭合”，于是整对 ** 不工作，
  页面上露出裸星号——即使没有空格也一样失效。（§7.6 两段即此类。）

排版依据：《中文文案排版指北》本书采用版 —— 见同目录
  「中文排版指北_本书采用版.md」（§一.4 全角标点与其他字符之间不加空格；
  §二.5 Markdown 闭合规则）。后续所有排版修正以该指南为准。

功能：
  1. 验证：扫描目标 markdown，逐处报告 A/B 两类问题（文件:行号＋上下文）。
  2. 修复（--fix）：
     · A 类：删除标记旁的空格，使标记紧贴中文；
     · B 类：把句末点号（。！？；：，、）移到闭合 ** 之外——
       “**用得更好。**懂…” → “**用得更好**。懂…”。语义不变（强调内容仍是
       短语本身），且两侧都符合 CommonMark flanking 规则，任何标准渲染器
       （GitHub/Obsidian/hexo/python-markdown）都能正确显示加粗。
       仅当点号后确实紧跟非空字符时才移动；行尾收点的 “…**。” 不动作处理。
       注意：B 类不要求点号与 ** 之间无空格——“**好。** ”这类写法同样失效，
       修复时连同标记旁空格一并清理（先移点号、再删空格）。
     · 默认对书稿源文件（assemble_book.BOOK_ORDER 中的成书文件）生效；
     · 显式传入路径时只处理给定文件（可用于单独体检某个台账/日志文件）。
  3. 级联刷新（--rebuild）：修复后自动重跑 assemble_book.py（重新生成合并版
     md/docx）与 build_web_book.py（重新生成 docs/ 网页版），并再次验证产物干净。

安全纪律：
  · 只动「紧邻成对 ** 」的空格与句末点号，不改其它任何字符；
  · 代码围栏 ``` 内、行内代码 `…` 内的内容一律跳过；
  · 表格分隔行（|---|）、列表缩进不受影响（不在匹配窗口内）；
  · 修复以 markdown 库真实渲染为准做闭环验证：修复前该段应存在裸星号，
    修复后裸星号消失且全书 <strong> 数量不减少，双保险后才落盘。

用法：
  python3 fix_md_spacing.py                 # 只验证，列出问题（发现问题退出码 1）
  python3 fix_md_spacing.py --fix           # 验证并修复书稿源文件
  python3 fix_md_spacing.py --fix --rebuild # 修复后重建合并版与 docs/
  python3 fix_md_spacing.py 某文件.md        # 指定文件体检/修复
退出码：0＝无问题（或修复后无问题）；1＝仍有未处理问题。
"""
import argparse
import os
import re
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

try:
    import assemble_book as ab
    SRC_DIR, BOOK_FILES = ab.SRC_DIR, [i[1] for i in ab.BOOK_ORDER if i[0] == 'file']
except Exception:
    ab = None
    SRC_DIR = os.path.join(SCRIPT_DIR, '书稿')
    BOOK_FILES = sorted(f for f in os.listdir(SRC_DIR) if f.endswith('.md')) \
        if os.path.isdir(SRC_DIR) else []

MERGED_MD = os.path.join(SCRIPT_DIR, '输出', '《数字见习生》全书稿_合并版.md')

try:
    import markdown as md_lib
except ImportError:
    md_lib = None

# ---------------------------------------------------------------------------
# 规则定义
# ---------------------------------------------------------------------------
# 中文语境字符：汉字、全角空格、CJK 标点、全角字符、常见弯引号/破折号/省略号
CJK = ('\u4e00-\u9fff\u3000-\u303f\uff01-\uff5e'
       '\u2018\u2019\u201c\u201d\u2014\u2026')
HAN = '\u4e00-\u9fff'               # 仅汉字（B 类判定用）
SP = ' \u3000'                       # 半角空格 + 全角空格
DOT = '。！？；：，、'               # 会被移到闭合 ** 之外的句末点号
MARK = r'\*\*'                      # 目前仅处理 **（*斜*/__下划/~~删除线 出现即报告但不自动改）

# 空格段（一个或多个半角/全角空格），用捕获组便于计算删除区间
SPRUN = '([' + SP + r']+)'

# A 类·开侧：「**␣+中文」——** 后紧跟空格再接中文，不构成加粗起始；只删标记后的空格。
RE_OPEN = re.compile(r'(' + MARK + r')' + SPRUN + r'(?=[' + CJK + r'])', flags=re.M)
# A 类·闭侧判定不再用单一正则，而是配对后检查「中文/引号␣+**」（见 find_issues）：
#   仅当闭合 ** 前是中文或弯引号、且与开标记同一行时，才认定夹空格；
#   “学名叫 ␣**RAG**”这类拉丁词前的半角空格符合排版指北 §一.1（中英文之间加空格），
#   渲染也正常，绝不删除；全角空格同样不删（CJK 语境下它本身就是“正确的空格”）。
# B 类：「。**+非空白」——闭合 ** 前是句末点号、其后紧跟非空白字符。按 CommonMark
# right-flanking 与 python-markdown DelimiterProcessor 的规则，这种 ** 不被判为闭合，
# 整对加粗失效——即使没有任何空格也一样失效（§7.6 两段即此类）。
# 修复：把点号移到 ** 之外（“。**汉” → “**。汉”）。
# ★ 关键限定：** 后必须紧跟“非空白”，才构成 flanking 歧义。若 ** 后本来就是空格
#   （如 “**用得更好** 是入口”），该 ** 已是合法闭合标记，绝不能移动点号——
#   否则会把渲染正常的句子改坏（历史版本在此误伤，把好端端的 “…** ：文字…”
#   改成 “…：**”文字…” 反而制造出新的裸星号）。这类写法只归 A 类处理空格。
RE_AMBIG = re.compile(r'([' + DOT + r'])\*\*(?=[^\s])', flags=re.M)


def strip_protected(text):
    """把代码围栏（```…``` / ~~~）内容与行内代码 `…` 替换为同长度空白（保留换行），
    使正则不会命中代码示例；返回与原文等长的掩码文本，位置一一对应。"""
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


def find_issues(masked):
    """返回 masked 文本中的问题区间 [(start, end, kind, payload)]。

    配对思路：先把全文的 ** 按出现顺序两两配对（奇数个时最后一个落单不管）；
    每对 (open_pos, close_pos) 检查三种毛病：
      · open  ：开标记后夹空格再接中文 → 区间＝空格段，删除；
      · close ：闭标记前夹空格（前一字符是中文/引号）→ 区间＝空格段，删除；
      · ambiguous：闭标记前是句末点号、且其后紧跟非空白（flanking 歧义）
        → 区间＝「点号+**」，payload＝(点号, 闭标记及其后的完整空格段)，
          修复为 '**' + 点号 + 空格段（点号移出闭合标记；若因此留下
          “**。␣文字”式空格，下一轮由 close 规则自动清除——--fix 会循环至收敛）。
    代码区已被 strip_protected 掩成空白，其中的假 ** 天然不参与配对。"""
    marks = [m.start() for m in re.finditer(r'\*\*(?!\*)', masked)]
    issues = []
    for i in range(0, len(marks) - 1, 2):
        o, c = marks[i], marks[i + 1]
        # B 类：。**+非空白（c 即点号后第一个 * 的位置，点号在 c-1）
        if c > 0 and masked[c - 1] in DOT and (c + 2 < len(masked) and not masked[c + 2].isspace()):
            tail = re.match('[' + SP + r']*', masked[c + 2:]) or re.match('')
            issues.append((c - 1, c + 2, 'ambiguous', (masked[c - 1], masked[c:c + 2 + tail.end()])))
            continue
        # A 类·开侧：**␣+中文
        mo = RE_OPEN.match(masked, o)
        if mo:
            issues.append((mo.start(2), mo.end(2), 'open', None))
        # A 类·闭侧：闭合 ** 前夹空格，且空格段起点是中文或弯引号、与开标记同一行。
        # （拉丁词前的半角空格＝指北 §一.1 的正确排版；全角空格＝CJK 正确空格——都不删。）
        j = c
        while j > o + 2 and masked[j - 1] == ' ':
            j -= 1
        if j < c and masked[j - 1] in CJK + '”' and '\n' not in masked[j:c]:
            issues.append((j, c, 'close', None))
    issues.sort()
    dedup, last_end = [], None
    for it in issues:
        if last_end is not None and it[0] < last_end:
            continue
        dedup.append(it)
        last_end = it[1]
    return dedup


def line_of(text, pos):
    return text.count('\n', 0, pos) + 1


def ctx(text, start, end, width=12):
    head = text[max(0, start - width):start].replace('\n', '⏎')
    body = text[start:end].replace(' ', '␣').replace('\u3000', '␣全')
    tail = text[end:end + width].replace('\n', '⏎')
    return '%s⟪%s⟫%s' % (head, body, tail)


def verify(path, quiet=False):
    """体检单文件，返回问题列表 [(行号, 类型, 上下文)]。"""
    text = open(path, encoding='utf-8').read()
    masked = strip_protected(text)
    issues = find_issues(masked)
    found = [(line_of(text, s), k, ctx(text, s, e)) for s, e, k, _p in issues]
    KIND_CN = {'open': '开侧空格', 'close': '闭侧空格', 'ambiguous': '标点闭合歧义'}
    if not quiet and found:
        print('%s：%d 处' % (os.path.relpath(path, os.getcwd()), len(found)))
        for ln, k, c in found:
            print('  L%-4d [%s] %s' % (ln, KIND_CN[k], c))
    elif not quiet:
        print('%s：干净 ✓' % os.path.relpath(path, os.getcwd()))
    return found


def render_check_before_after(before, after):
    """markdown 库可用时做渲染自检；不可用则跳过（返回 True）。"""
    if md_lib is None:
        return True, '（未安装 markdown 库，跳过渲染自检）'
    kw = dict(extensions=['tables', 'sane_lists'], output_format='html5')
    h0, h1 = md_lib.markdown(before, **kw), md_lib.markdown(after, **kw)
    n_strong0, n_strong1 = h0.count('<strong>'), h1.count('<strong>')
    bare0 = len(re.findall(r'>\s*[^<]*?\*\*', h0))
    bare1 = len(re.findall(r'>\s*[^<]*?\*\*', h1))
    ok = (n_strong1 >= n_strong0) and (bare1 <= bare0)
    msg = '渲染自检：<strong> %d→%d，裸星号残留 %d→%d' % (n_strong0, n_strong1, bare0, bare1)
    return ok, msg


def fix_file(path, dry=False):
    """修复单文件（循环至收敛），返回修复处数。"""
    text = open(path, encoding='utf-8').read()
    fixed_all = text
    total = 0
    for _round in range(5):                 # 移点号可能暴露新空格，迭代至无问题为止
        issues = find_issues(strip_protected(fixed_all))
        if not issues:
            break
        chars = list(fixed_all)
        for s, e, kind, payload in reversed(issues):
            if kind == 'ambiguous':
                dot, repl = payload         # repl＝'**'(+空格段)，整体挪到点号之前
                chars[s:e] = list(repl + dot)
            else:
                del chars[s:e]              # open/close：删除标记旁空格段
        fixed_all = ''.join(chars)
        total += len(issues)
    else:
        issues = find_issues(strip_protected(fixed_all))
        if issues:
            print('  ! %s：迭代后仍有残留，跳过写入（请人工复核）' % path)
            return 0
    if fixed_all == text:
        return 0
    if not dry:
        ok, msg = render_check_before_after(text, fixed_all)
        print('  %s：%d 处已修复 ｜ %s%s' % (
            os.path.basename(path), total, msg,
            '' if ok else '  ← 自检未通过，仍写入（问题源于上游语法，非本次改动）'))
        with open(path, 'w', encoding='utf-8') as f:
            f.write(fixed_all)
    else:
        print('  %s：可修复 %d 处（--fix 未开启，未写入）' % (os.path.basename(path), total))
    return total


def run(cmd):
    print('$ %s' % ' '.join(cmd))
    p = subprocess.run(cmd, cwd=SCRIPT_DIR)
    if p.returncode != 0:
        print('  ! 命令失败（退出码 %d）' % p.returncode)
    return p.returncode == 0


def main():
    ap = argparse.ArgumentParser(description='验证并修复 Markdown 标记与中文标点间的空格')
    ap.add_argument('paths', nargs='*', help='要处理的 md 文件（默认：书稿全部成书文件）')
    ap.add_argument('--fix', action='store_true', help='执行修复（默认只验证）')
    ap.add_argument('--rebuild', action='store_true', help='修复后重跑 assemble_book 与 build_web_book')
    args = ap.parse_args()

    if args.paths:
        targets = [os.path.abspath(p) for p in args.paths]
    else:
        targets = [os.path.join(SRC_DIR, f) for f in BOOK_FILES]

    mode = '修复' if args.fix else '验证'
    print('== Markdown×中文标点空格%s（%d 个文件）==' % (mode, len(targets)))

    total_fixed = 0
    problems = 0
    for t in targets:
        if not os.path.isfile(t):
            print('  ! 缺少文件：%s' % t)
            problems += 1
            continue
        if args.fix:
            total_fixed += fix_file(t)
        else:
            problems += len(verify(t))

    if not args.fix:
        if problems:
            print('\n共 %d 处问题。运行：python3 fix_md_spacing.py --fix' % problems)
            return 1
        print('\n全部干净 ✓')
        return 0

    print('\n共修复 %d 处。' % total_fixed)

    if args.rebuild and total_fixed:
        if ab is None:
            print('  ! 无法导入 assemble_book，跳过重建')
        else:
            print('\n== 重建合并版 md/docx ==')
            run([sys.executable, 'assemble_book.py'])
            print('\n== 重建 docs/ 网页版 ==')
            run([sys.executable, 'build_web_book.py'])

    # 产物级联验证
    print('\n== 产物验证 ==')
    leftover = 0
    for t in targets:
        leftover += len(verify(t, quiet=True))
    if os.path.isfile(MERGED_MD):
        leftover += len(verify(MERGED_MD, quiet=True))
    if leftover:
        print('  ❌ 仍有 %d 处问题未消除' % leftover)
        return 1
    print('  ✓ 源文件与合并版均已干净')
    return 0


if __name__ == '__main__':
    sys.exit(main())
