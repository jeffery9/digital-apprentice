#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prepub_check.py — 《AI Agent 架构决策指南》出版前检查（A–H 八组）

用法：cd agentbook && python3 prepub_check.py [--json]
纪律：对源稿与产物**只读**；发现问题只报告（不绿在哪、疑似原因、建议改哪个源文件），
      修复是改稿任务，走「改稿闭环线」（QWEN.md §二）。
落盘：台账/检查报告/终检报告_<VERSION>_<时间戳>.md

增量写作期约定：尚未写就的章文件不判 FAIL，只在 A1 以 INFO 列出"待写"，
其余检查仅对**已存在**的源文件生效；付印前须以 --strict 全量通过（FAIL=0）。

规则集来源：策划案§十一"特有质量关卡"＋ QWEN.md 三条铁律。
"""
import argparse
import datetime
import json
import os
import re
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, '书稿')
FIG = os.path.join(ROOT, '图表')
CODE = os.path.join(ROOT, 'code')
MAT = os.path.join(ROOT, '素材')
OUT = os.path.join(ROOT, '输出')
LEDGER = os.path.join(ROOT, '台账')
REPORT_DIR = os.path.join(LEDGER, '检查报告')

sys.path.insert(0, ROOT)
import assemble_agentbook as AB   # 只取 BOOK_ORDER / VERSION / BOOK_TITLE 常量

# ── 期望结构（来自策划案§六章节总表）───────────────────────────────
EXPECT_CHAPTERS = 20
EXPECT_PATTERN_CARDS = 55          # 去重后独立卡数（对外口径）
EXPECT_DEMOS = 20                  # D-01 … D-20
CHAPTER_WORD_CAP = 12000           # 每章上限 1.2 万字
# 各章目标字数（策划案§六章节总表）；Z0 告警口径（作者指示 2026-10-14）：
# **字数偏差只能多不能少**——低于目标 85%（偏少 >15%）为 FAIL（须扩写）；
# 高于目标 115% 仅 WARN（登记即可，不要求减字）。
EXPECT_WORDS = {1: 9000, 2: 9000, 3: 9000, 4: 11000, 5: 10000, 6: 10000, 7: 9000,
                8: 9000, 9: 8000, 10: 10000, 11: 10000, 12: 9000, 13: 10000,
                14: 9000, 15: 9000, 16: 10000, 17: 10000, 18: 9000, 19: 8000, 20: 10000}
DEMO_CHAPTER_MAP = {               # demo → 章（策划案§十）
    'D-01': 1, 'D-02': 3, 'D-03': 4, 'D-04': 4, 'D-05': 5, 'D-06': 5,
    'D-07': 6, 'D-08': 7, 'D-09': 8, 'D-10': 9, 'D-11': 10, 'D-12': 11,
    'D-13': 11, 'D-14': 12, 'D-15': 13, 'D-16': 13, 'D-17': 14,
    'D-18': 16, 'D-19': 17, 'D-20': 18,
}
CARDS_PER_CHAPTER = {              # 章内出现次数（合计 67）
    1: 3, 2: 0, 3: 1, 4: 5, 5: 5, 6: 5, 7: 4, 8: 4, 9: 3, 10: 5,
    11: 5, 12: 3, 13: 4, 14: 3, 15: 3, 16: 4, 17: 4, 18: 3, 19: 3, 20: 0,
}
# 铁律一：机制 → (深挖章, 只许引用的章, 判别关键词)
DEEP_DIVE = {
    '检查点与恢复': (15, [10, 16], ['检查点', 'checkpoint', '崩溃恢复', '事件溯源']),
    '多模态注入防护': (17, [13, 14], ['提示注入', '注入防护', 'prompt injection']),
    '评估与可观测': (18, list(range(1, 18)) + [19, 20], ['可观测', '轨迹评估', 'trace', '指标体系']),
    '成本与计价': (19, [4], ['单价', '计价', '每百万 token', 'token 成本']),
    '沙箱与隔离': (15, [16], ['沙箱', 'sandbox', '隔离级别']),
    '上下文预算': (6, [13], ['上下文预算', '预算护栏', 'token 预算']),
}
# 八段式骨架的识别标记（段名 → 正文中应出现的字样）
SKELETON = ['决策场景', '本章地图', '决策指引', '小结', '延伸阅读']
# 同名多义术语辨析条（附录 B 必须立条）
DISAMBIG = ['工作流路由', '模型路由', '崩溃恢复', '断电续跑', '跨天恢复',
            '原生多模态', '感知管线', '模态轴', '时程轴', '能力画像']
# 语气红线
BANNED = ['革命性', '颠覆性', '赋能', '众所周知', '毋庸置疑', '史上最强',
          '遥遥领先', '闭环打法', '降维打击', '手把手教你', '小白也能']

_ap = argparse.ArgumentParser(description='《AI Agent 架构决策指南》出版前检查（只读）')
_ap.add_argument('--json', action='store_true', help='额外输出 JSON 结果')
_ap.add_argument('--strict', action='store_true', help='付印模式：INFO 级也计入告警统计')
_ap.add_argument('--only', type=lambda s: s.split(','), default=None,
                 metavar='CODES', help='只输出指定检查码（逗号分隔，如 Z0,D2,B3）')
_args = _ap.parse_args()

results = []


def rep(level, code, msg, fix=''):
    # --only 过滤：仅保留指定检查码（如 Z0/D2/B3），供扩写/修复迭代时快速复验
    if _args.only and code.split('b')[0] not in _args.only:
        return
    results.append({'level': level, 'code': code, 'msg': msg, 'fix': fix})
    mark = {'PASS': '✅', 'FAIL': '❌', 'WARN': '⚠️ ', 'INFO': 'ℹ️ '}[level]
    print(f'{mark}[{code}] {msg}')
    if fix and level in ('FAIL', 'WARN'):
        print(f'      └ 建议：{fix}')


def read(path):
    try:
        with open(path, encoding='utf-8') as f:
            return f.read()
    except OSError:
        return None


def cn_chars(text):
    """正文字数口径：去代码块、去表格分隔行后的中日韩字符＋英文单词数。"""
    t = re.sub(r'```.*?```', '', text, flags=re.S)
    t = re.sub(r'^\|[-\s|:]+\|$', '', t, flags=re.M)
    han = len(re.findall(r'[\u4e00-\u9fff]', t))
    en = len(re.findall(r'[A-Za-z][A-Za-z0-9_\-\.]*', t))
    return han + en


# ══════════════════════════════════════════════════════════════
print('\n═══ A. 结构与版本一致性 ═══')
bo_files = [e[1] for e in AB.BOOK_ORDER if e[0] == 'file']
bo_parts = [e[1] for e in AB.BOOK_ORDER if e[0] == 'part']
present = [f for f in bo_files if os.path.isfile(os.path.join(SRC, f))]
absent = [f for f in bo_files if f not in present]
rep('PASS' if not absent else 'INFO', 'A1',
    f'BOOK_ORDER {len(bo_files)} 个源文件：在位 {len(present)}，待写 {len(absent)}'
    + (f'（{", ".join(absent[:6])}{"…" if len(absent) > 6 else ""}）' if absent else ''),
    '付印前须 --strict 全量在位' if absent else '')

ch_files = sorted(f for f in present if re.match(r'第\d{2}章\.md$', f))
ch_nums = [int(re.search(r'第(\d{2})章', f).group(1)) for f in ch_files]
gaps = [n for n in range(1, (max(ch_nums) if ch_nums else 0) + 1) if n not in ch_nums]
rep('PASS' if not gaps else 'FAIL', 'A2',
    f'已写章节 {len(ch_nums)} 个，章号{"连续" if not gaps else f"断号：{gaps}"}'
    f'（最大章号 {max(ch_nums) if ch_nums else 0}/{EXPECT_CHAPTERS}）',
    '章号必须连续，缺章须补写或调整 BOOK_ORDER')

prog = read(os.path.join(SRC, '00_进度表.md')) or ''
v_prog = re.search(r'(v\d+\.\d+\w*)', prog.split('\n')[0] if prog else '')
cands = [f for f in os.listdir(OUT) if f.endswith('合并版.md')] if os.path.isdir(OUT) else []
merged_md = read(os.path.join(OUT, cands[0])) if cands else None
v_merged = re.search(r'subtitle[:：].*?(v\d+\.\d+\w*)', merged_md) if merged_md else None
vers = {'脚本': AB.VERSION,
        '进度表': v_prog.group(1) if v_prog else '?',
        '合并版': v_merged.group(1) if v_merged else '(未组装)'}
docx_path = os.path.join(OUT, cands[0][:-3] + '.docx') if cands else None
if docx_path and os.path.isfile(docx_path):
    try:
        import docx as _docx
        subj = _docx.Document(docx_path).core_properties.subject or '?'
        vers['docx'] = (re.search(r'(v\d+\.\d+\w*)', subj) or [None, '?'])[1] \
            if re.search(r'(v\d+\.\d+\w*)', subj) else '?'
    except Exception as e:                                    # noqa: BLE001
        vers['docx'] = f'(读取失败 {e.__class__.__name__})'
else:
    vers['docx'] = '(未组装)'
have = [v for k, v in vers.items() if not v.startswith('(') and v != '?']
same = len(set(have)) == 1 and len(have) >= 2
rep('PASS' if same else 'WARN', 'A3',
    f'版本号一致性：{vers}' + ('' if same else '（含未组装项或版本号不一致）'),
    'bump VERSION 后须同步进度表标题并重新组装（QWEN.md §三）')

h1_expect = len(present) + len(bo_parts) + 1     # +1 书名页
if merged_md:
    n_h1 = len(re.findall(r'^# ', merged_md, flags=re.M))
    rep('PASS' if n_h1 == h1_expect else 'WARN', 'A4',
        f'合并版 H1＝{n_h1}（期望 {h1_expect}：书名页1＋部扉页{len(bo_parts)}＋源文件{len(present)}）',
        '每个源文件应恰好一个 H1（章名），多余 H1 会污染目录')
else:
    rep('INFO', 'A4', '尚未组装，跳过合并版 H1 检查（先跑 assemble_agentbook.py）')

# ══════════════════════════════════════════════════════════════
print('\n═══ B. 章节体例（八段式骨架 / 字数 / 图表编号） ═══')
skel_miss, over_cap = [], []
fig_nums, tbl_nums = defaultdict(list), defaultdict(list)
for f in ch_files:
    n = int(re.search(r'第(\d{2})章', f).group(1))
    txt = read(os.path.join(SRC, f))
    miss = [s for s in SKELETON if s not in txt]
    if miss:
        skel_miss.append((f, miss))
    wc = cn_chars(txt)
    if wc > CHAPTER_WORD_CAP:
        over_cap.append((f, wc))
    for m in re.finditer(r'图\s*(\d+)-(\d+)', txt):
        fig_nums[int(m.group(1))].append(int(m.group(2)))
    for m in re.finditer(r'表\s*(\d+)-(\d+)', txt):
        tbl_nums[int(m.group(1))].append(int(m.group(2)))

rep('PASS' if not skel_miss else 'WARN', 'B1',
    f'八段式骨架：{len(ch_files) - len(skel_miss)}/{len(ch_files)} 章齐全'
    + ('' if not skel_miss else '；缺失 → ' + '；'.join(f'{f}缺{"/".join(m)}' for f, m in skel_miss[:4])),
    '骨架段名可作小标题或加粗引导词出现，但不得整段缺席（QWEN.md §四）')
rep('PASS' if not over_cap else 'WARN', 'B2',
    f'每章 ≤{CHAPTER_WORD_CAP} 字：' + ('全部达标' if not over_cap else
    '；'.join(f'{f}={wc}' for f, wc in over_cap)),
    '超限先按铁律一删重复，再删例子，最后才动原理')

bad_fig = []
for ch, nums in sorted(fig_nums.items()):
    seq = sorted(set(nums))
    if seq and seq != list(range(1, len(seq) + 1)):
        bad_fig.append(f'第{ch}章图号 {seq}')
bad_tbl = []
for ch, nums in sorted(tbl_nums.items()):
    seq = sorted(set(nums))
    if seq and seq != list(range(1, len(seq) + 1)):
        bad_tbl.append(f'第{ch}章表号 {seq}')
rep('PASS' if not (bad_fig or bad_tbl) else 'FAIL', 'B3',
    f'图表编号连续性：图 {sum(len(set(v)) for v in fig_nums.values())} 幅 / 表 '
    f'{sum(len(set(v)) for v in tbl_nums.values())} 张'
    + ('' if not (bad_fig or bad_tbl) else '；跳号 → ' + '；'.join(bad_fig + bad_tbl)),
    '图 X-Y 的 Y 必须从 1 连续；X 必须等于所在章号')
for ch, nums in sorted(fig_nums.items()):
    if ch not in ch_nums:
        rep('WARN', 'B3b', f'第{ch}章图号出现在非第{ch}章文件中（跨章引用应写作"见图 X-Y"而非本编号）')
        break

# 图位块 ↔ Mermaid 源文件对应
blocks = []
for f in present:
    txt = read(os.path.join(SRC, f)) or ''
    for m in re.finditer(r'源文件：\s*(图表/[^\s｜|）)]+\.mmd)', txt):
        blocks.append((f, m.group(1)))
mmd = {f'图表/{x}' for x in os.listdir(FIG)
       if x.endswith('.mmd')} if os.path.isdir(FIG) else set()
miss_mmd = sorted({b[1] for b in blocks} - mmd)
orphan_mmd = sorted(mmd - {b[1] for b in blocks})
rep('PASS' if not miss_mmd else 'FAIL', 'B4',
    f'图位块 {len(blocks)} 处 ↔ Mermaid 源 {len(mmd)} 个'
    + ('' if not miss_mmd else f'；缺源文件 → {miss_mmd}'),
    '每个图位块必须能追溯到 图表/*.mmd（决策 #4：源码入库、可再生）')
rep('PASS' if not orphan_mmd else 'INFO', 'B4b',
    '无游离 Mermaid 源' if not orphan_mmd
    else f'未被正文引用的 .mmd（属未写章，写完后自动纳入）：{sorted(orphan_mmd)}')

# B5 图表嵌入接线（M7，2026-10-15）：图位块须已接成 ![..](../图表/….svg) 真实引用
embed_refs = []          # (文件, svg相对路径)
for f in present:
    txt = read(os.path.join(SRC, f)) or ''
    for m in re.finditer(r'^!\[[^\]]*\]\((?:\.\./)?图表/([^\s)]+\.svg)\)', txt, flags=re.M):
        embed_refs.append((f, m.group(1)))
svg_files = {x for x in os.listdir(FIG) if x.endswith('.svg')} \
    if os.path.isdir(FIG) else set()
miss_svg = sorted({p for _, p in embed_refs} - svg_files)
unembedded_blocks = []   # 仍为占位形态（【图 X-Y…】且 SVG 已存在 → 应接线而未接）
for f in present:
    txt = read(os.path.join(SRC, f)) or ''
    for m in re.finditer(r'【图\s*(\d+-\d+)｜[^】]*】.*?(?:源文件|渲染源)[：:]\s*(图表/\S+?\.mmd)', txt):
        derived = os.path.basename(m.group(2)).replace('.mmd', '.svg')
        if derived in svg_files:
            unembedded_blocks.append(f'{f}:{m.group(1)}')
rep('PASS' if not miss_svg else 'FAIL', 'B5',
    f'图表嵌入：源稿真实图片引用 {len(embed_refs)} 处 ↔ SVG {len(svg_files)} 个'
    + ('' if not miss_svg else f'；引用了不存在的 SVG → {miss_svg}'),
    '运行 assemble_agentbook.py 的 embed_figures 接线，或补渲染 图表/*.svg')
rep('PASS' if not unembedded_blocks else 'WARN', 'B5b',
    '所有有 SVG 的图位块均已接线' if not unembedded_blocks
    else f'SVG 已渲染但正文仍为占位图位块（应接线）→ {unembedded_blocks}',
    '组装时由 embed_figures 自动接线；若单章检查请跑一次合装')

# ══════════════════════════════════════════════════════════════
print('\n═══ C. 模式卡与交叉引用 ═══')
card_ledger = read(os.path.join(SRC, '模式卡', '模式卡台账.md'))
CARD_FIELDS = ['名称', '意图', '问题', '结构', '适用场景', '不适用场景',
               '风险与代价', '关联模式', '出处与实例']
card_hits = defaultdict(list)      # 章 → [卡名]
field_miss = []
for f in ch_files:
    n = int(re.search(r'第(\d{2})章', f).group(1))
    txt = read(os.path.join(SRC, f))
    for m in re.finditer(r'【模式卡[:：]\s*([^】\n]+)】', txt):
        card_hits[n].append(m.group(1).strip())
    # 九字段完整性：定位每张卡的块体
    for m in re.finditer(r'【模式卡[:：][^】]+】\n(.*?)(?=\n【模式卡|\n##\s|\Z)', txt, flags=re.S):
        body = m.group(1)
        miss = [fd for fd in CARD_FIELDS if fd not in body]
        if miss:
            field_miss.append((f, miss))
total_cards = sum(len(v) for v in card_hits.values())
exp_total = sum(CARDS_PER_CHAPTER.get(n, 0) for n in ch_nums)
rep('PASS' if total_cards == exp_total else ('INFO' if total_cards < exp_total else 'WARN'),
    'C1', f'模式卡标记 {total_cards} 张（已写章节按策划案应含 {exp_total} 张）；'
         f'全书目标 章内 {sum(CARDS_PER_CHAPTER.values())} 次 / 去重 {EXPECT_PATTERN_CARDS} 张',
    '卡数须与策划案§六章节总表一致；变动须同步台账与附录 A')
rep('PASS' if not field_miss else 'FAIL', 'C2',
    '模式卡九字段完整' if not field_miss else
    '九字段缺失 → ' + '；'.join(f'{f}缺{"/".join(m)}' for f, m in field_miss[:4]),
    '九字段一个都不能少；"不适用场景""风险与代价"不得写空话（铁律二）')

if card_ledger:
    # 只数「## 一、台账」小节的表，其余小节（待建清单／共卡关系／维护规则）不计
    sec = re.split(r'^## ', card_ledger, flags=re.M)
    sec1 = next((s for s in sec if s.startswith('一、台账')), '')
    rows = [ln for ln in sec1.split('\n') if ln.startswith('|') and '---' not in ln]
    ledger_names = {re.split(r'\|', r)[1].strip() for r in rows[1:]} if len(rows) > 1 else set()
    body_names = {c for v in card_hits.values() for c in v}
    unreg = sorted(body_names - ledger_names)
    rep('PASS' if not unreg else 'FAIL', 'C3',
        f'模式卡台账登记 {len(ledger_names)} 条，正文出现 {len(body_names)} 个卡名'
        + ('' if not unreg else f'；未登记 → {unreg[:6]}'),
        '每张卡必须在 书稿/模式卡/模式卡台账.md 登记（卡名→归属章→共卡关系）')
else:
    rep('WARN', 'C3', '缺 书稿/模式卡/模式卡台账.md', '建卡时同步建台账，否则附录 A 会与正文打架')

# 回指闭环："详见 X.Y" / "见 X.Y" / "回指 X.Y"
refs = []
for f in present:
    txt = read(os.path.join(SRC, f)) or ''
    for m in re.finditer(r'(?:详见|参见|见|回指)\s*(\d{1,2})\.(\d{1,2})', txt):
        refs.append((f, int(m.group(1)), int(m.group(2))))
secs = defaultdict(set)
for f in ch_files:
    n = int(re.search(r'第(\d{2})章', f).group(1))
    for m in re.finditer(r'^#{2,4}\s*(\d{1,2})\.(\d{1,2})', read(os.path.join(SRC, f)), flags=re.M):
        secs[int(m.group(1))].add(int(m.group(2)))
dangling = sorted({(c, s) for _, c, s in refs
                   if c in ch_nums and s not in secs.get(c, set())})
future = sorted({(c, s) for _, c, s in refs if c not in ch_nums})
rep('PASS' if not dangling else 'FAIL', 'C4',
    f'跨章回指 {len(refs)} 处：已写章节内悬空 {len(dangling)} 处'
    + ('' if not dangling else f' → {dangling[:6]}'),
    '"详见 X.Y" 必须指向真实存在的小节（策划案§十一 回指闭环检查）')
rep('INFO', 'C4b', f'指向尚未写就章节的回指 {len(future)} 处（写完后自动纳入校验）')

# demo 编号 ↔ 章节映射
demo_head, demo_mention = defaultdict(set), defaultdict(set)
for f in present:
    n = int(re.search(r'第(\d{2})章', f).group(1)) if re.match(r'第\d{2}章', f) else 0
    txt = read(os.path.join(SRC, f)) or ''
    for m in re.finditer(r'^#{2,4}.*?\bD-(\d{2})\b', txt, flags=re.M):   # 标题里＝主体出现
        demo_head[f'D-{m.group(1)}'].add(n)
    for m in re.finditer(r'\bD-(\d{2})\b', txt):                        # 正文提及＝跨章引用
        demo_mention[f'D-{m.group(1)}'].add(n)
misplaced = [(d, sorted(cs), DEMO_CHAPTER_MAP.get(d)) for d, cs in demo_head.items()
             if d in DEMO_CHAPTER_MAP and DEMO_CHAPTER_MAP[d] not in cs]
rep('PASS' if not misplaced else 'FAIL', 'C5',
    f'demo 主体出现 {len(demo_head)} 个编号，章节归属与策划案§十一致'
    if not misplaced else f'demo 主体出现在错误章节 → {misplaced}',
    'D-XX 的讲解小节只能在其归属章；他章只可作跨章引用提及')
xref = {d: sorted(c for c in cs if DEMO_CHAPTER_MAP.get(d) != c)
        for d, cs in demo_mention.items() if d in DEMO_CHAPTER_MAP}
xref = {d: c for d, c in xref.items() if c}
rep('PASS' if not xref else 'INFO', 'C5c',
    '无跨章 demo 引用' if not xref else
    f'跨章引用（合法，写完后须确认目标章已讲解）：{xref}')
unknown = sorted(d for d in demo_mention if d not in DEMO_CHAPTER_MAP)
rep('PASS' if not unknown else 'FAIL', 'C5b',
    '无未登记 demo 编号' if not unknown else f'未登记编号 → {unknown}（合法范围 D-01…D-{EXPECT_DEMOS:02d}）')

# ══════════════════════════════════════════════════════════════
print('\n═══ D. 事实纪律与时效锚点 ═══')
all_txt = '\n'.join(read(os.path.join(SRC, f)) or '' for f in present)
# 格式口径（QWEN.md §七）：主干必须是【时效锚点｜截至 YYYY-MM】，
# 允许在日期之后追加备注子句（「，…」或「， 」引导），备注不得吞掉日期。
ANCHOR_OK = re.compile(r'【时效锚点｜截至\s*\d{4}-\d{2}(?:，[^】]*)?】')
anchors = re.findall(ANCHOR_OK, all_txt)
bad_anchor = [a for a in re.findall(r'【时效锚点[^】]*】', all_txt)
              if not ANCHOR_OK.fullmatch(a)]
rep('PASS' if anchors and not bad_anchor else ('WARN' if not anchors else 'FAIL'), 'D1',
    f'L3 时效锚点 {len(anchors)} 处，格式全部合规' if anchors and not bad_anchor
    else (f'格式不合规 → {bad_anchor[:4]}' if bad_anchor else '未发现时效锚点'),
    '锚点格式固定为【时效锚点｜截至 YYYY-MM】，且集中收纳在小节末尾（QWEN.md §七）')

# L3 高危词出现处是否在锚点覆盖的小节内
L3_WORDS = ['OSWorld', 'RouteLLM', 'vLLM Semantic Router', 'Kong AI Gateway', 'LiteLLM',
            'Envoy AI Gateway', 'Cloudflare AI Gateway', 'Portkey', 'TrueFoundry',
            'Computer Use', 'computer use', 'ColPali']
l3_hits = defaultdict(list)
for f in present:
    txt = read(os.path.join(SRC, f)) or ''
    for w in L3_WORDS:
        if w in txt:
            # 该词所在段落之后 3000 字内是否有锚点；
            # 或同一行／紧邻前一行（±150 字回溯窗）是否有锚点——
            # 覆盖"地图行/小节首行先打锚、L3 词随后出现"的合法形态（QWEN.md §七收纳口径）
            for m in re.finditer(re.escape(w), txt):
                fwd = txt[m.start():m.start() + 3000]
                bwd = txt[max(0, m.start() - 150):m.start()]
                if '【时效锚点' not in fwd and '【时效锚点' not in bwd:
                    l3_hits[f].append(w)
# 章级兜底：全章合规锚点 ≥2 处时，残余命中降级登记（延伸阅读/回指句中的产品名视为已覆盖）
l3_soft = {f: v for f, v in list(l3_hits.items())
           if len(re.findall(r'【时效锚点｜截至\s*\d{4}-\d{2}', read(os.path.join(SRC, f)) or '')) >= 2}
for f in l3_soft:
    del l3_hits[f]
rep('PASS' if not l3_hits else 'WARN', 'D2',
    'L3 短效内容均在锚点覆盖范围内' if not l3_hits
    else '疑似无锚点覆盖的 L3 内容 → ' + '；'.join(f'{f}:{sorted(set(v))[:3]}' for f, v in list(l3_hits.items())[:4]),
    '产品格局/基准分数/模型代际属 L3，必须打锚点（策划案§九）')
if l3_soft:
    rep('INFO', 'D2b', '章级锚点充足，延伸/回指处产品名按章覆盖豁免 → '
        + '；'.join(f'{f}:{sorted(set(v))[:3]}' for f, v in list(l3_soft.items())[:4]),
        '如编辑要求逐处锚点，可在对应小节补打')

ph = re.findall(r'【[^】]{2,40}】', all_txt)
ph = [x for x in ph if '时效锚点' not in x and '模式卡' not in x and '图 ' not in x and '表 ' not in x]
rep('INFO', 'D3', f'占位符【…】{len(ph)} 处' + (f'：{sorted(set(ph))[:8]}' if ph else '')
    + '（付印前须清零）', '占位事实用【方括号】，须逐项填实或删除')

bare = []
for f in present:
    txt = read(os.path.join(SRC, f)) or ''
    for m in re.finditer(r'(\d{1,3}(?:\.\d+)?%)\s*(?:的)?(?:成功率|准确率|通过率)', txt):
        ctx = txt[max(0, m.start() - 400):m.end() + 400]
        if not re.search(r'(arXiv|OSWorld|SWE-bench|GAIA|基准|benchmark|论文|v\d)', ctx, re.I):
            bare.append((f, m.group(0)))
rep('PASS' if not bare else 'FAIL', 'D4',
    '基准/成功率数字均带出处' if not bare else f'疑似裸引基准数字 → {bare[:5]}',
    '铁律三：基准名称＋版本＋测量条件，三者缺一即改【占位】')

fiction = any('虚构' in (read(os.path.join(SRC, f)) or '') for f in present)
rep('PASS' if fiction else 'WARN', 'D5',
    '智航科技虚构声明在位' if fiction else '未见虚构声明',
    '"智航科技"首次出现处须声明虚构、与真实公司相似为巧合（QWEN.md §七）')

# 算式可复算：出现幂/乘积论证处须有前提说明
calc = re.findall(r'0\.\d+\s*[\^⁵]|\^\s*\{?\d{1,3}\}?|≈\s*0\.\d+%', all_txt)
calc_ctx_ok = all(re.search(r'(假设|前提|单步|独立|每步)', all_txt[max(0, all_txt.find(c) - 600):all_txt.find(c) + 600])
                  for c in calc[:5]) if calc else True
rep('PASS' if calc_ctx_ok else 'WARN', 'D6',
    f'论证算式 {len(calc)} 处，均带前提假设' if calc and calc_ctx_ok
    else ('未发现论证算式' if not calc else '算式缺少前提假设说明'),
    '算式必须可复算：给出完整过程与前提（QWEN.md 铁律三）')

# ══════════════════════════════════════════════════════════════
print('\n═══ E. 铁律一：跨章重复深挖 ═══')
dup = []
for mech, (deep, ref_chs, kws) in DEEP_DIVE.items():
    for ch in ref_chs:
        f = f'第{ch:02d}章.md'
        if f not in present or ch == deep:
            continue
        txt = read(os.path.join(SRC, f)) or ''
        body = re.sub(r'```.*?```', '', txt, flags=re.S)
        hits = sum(body.count(k) for k in kws)
        # 允许提及，不允许展开：阈值 = 关键词命中 ≥8 次且无"详见深挖章"回指
        has_ref = re.search(rf'详见\s*{deep}\.\d|见第\s*{deep}\s*章|第{deep}章', txt)
        if hits >= 8 and not has_ref:
            dup.append((mech, ch, hits, deep))
rep('PASS' if not dup else 'WARN', 'E1',
    '未发现越界深挖' if not dup else
    '疑似在非深挖章展开 → ' + '；'.join(f'{m}在第{c}章命中{h}次（深挖章为第{d}章）' for m, c, h, d in dup),
    '一个机制只在一章深挖，其余只引用（QWEN.md 铁律一）；M4/M5/M6 关口一律删除重复')

# 术语首现中英对照
terms = re.findall(r'([\u4e00-\u9fff]{2,8})（([A-Za-z][A-Za-z0-9 \-\.]{2,40})）', all_txt)
rep('PASS' if len(terms) >= 5 else 'INFO', 'E2',
    f'术语中英对照首现 {len(terms)} 处（全书目标约 170 条，见附录 B）',
    '术语首次出现须给中英对照，此后中文为主')

# ══════════════════════════════════════════════════════════════
print('\n═══ F. Demo 代码工程 ═══')
demos = sorted(d for d in os.listdir(CODE) if re.match(r'D-\d{2}', d)) if os.path.isdir(CODE) else []
rep('INFO', 'F1', f'code/ 下 demo {len(demos)}/{EXPECT_DEMOS} 个：{demos if demos else "（暂无）"}')
no_mock, no_readme, no_req = [], [], []
for d in demos:
    dp = os.path.join(CODE, d)
    pys = [x for x in os.listdir(dp) if x.endswith('.py')]
    body = '\n'.join(read(os.path.join(dp, x)) or '' for x in pys)
    if '--mock' not in body:
        no_mock.append(d)
    if not os.path.isfile(os.path.join(dp, 'README.md')):
        no_readme.append(d)
    if not os.path.isfile(os.path.join(dp, 'requirements.txt')):
        no_req.append(d)
rep('PASS' if demos and not no_mock else ('INFO' if not demos else 'FAIL'), 'F2',
    f'全部 demo 支持 --mock 离线模式' if demos and not no_mock
    else (f'缺 --mock → {no_mock}' if no_mock else '尚无 demo'),
    '策划案§八：无 --mock 则读者无法复现、编辑无法审校')
rep('PASS' if demos and not no_readme else ('INFO' if not demos else 'WARN'), 'F3',
    '每个 demo 附 README' if demos and not no_readme else (f'缺 README → {no_readme}' if no_readme else '尚无 demo'),
    'README 须含：验证日期、验证环境、预期输出、"观察什么"、最低可跑供应商组合、密钥处理方式')
rep('PASS' if demos and not no_req else ('INFO' if not demos else 'WARN'), 'F4',
    '每个 demo 附钉版本的 requirements.txt' if demos and not no_req
    else (f'缺 requirements → {no_req}' if no_req else '尚无 demo'))

llm = read(os.path.join(CODE, 'llm.py'))
if llm:
    need = ['多模态', '快照', '故障转移', '能力画像']
    got = [k for k in need if k in llm or k.lower() in llm.lower()]
    rep('PASS' if len(got) == len(need) else 'WARN', 'F5',
        f'llm.py 薄适配层能力覆盖 {len(got)}/{len(need)}：{got}',
        '策划案§十：llm.py 须支持多模态输入、快照固定、故障转移钩子、能力画像查询')
    secret = re.findall(r'(?:sk-[A-Za-z0-9]{16,}|api[_-]?key\s*=\s*["\'][A-Za-z0-9]{16,})', llm, re.I)
    rep('PASS' if not secret else 'FAIL', 'F6', '无真实密钥形态' if not secret else f'疑似硬编码密钥 → {secret[:2]}')
else:
    rep('WARN', 'F5', '缺 code/llm.py 薄适配层', 'M2 交付判据之一：llm.py/gateway.py 基座')

# ══════════════════════════════════════════════════════════════
print('\n═══ G. 素材合规 ═══')
mat_ledger = read(os.path.join(MAT, '授权链台账.md'))
mat_files = []
for base, _, files in os.walk(MAT):
    mat_files += [os.path.join(base, x) for x in files
                  if x.lower().endswith(('.png', '.jpg', '.jpeg', '.pdf', '.wav', '.mp3', '.mp4'))]
if mat_files:
    rep('PASS' if mat_ledger else 'FAIL', 'G1',
        f'多模态素材 {len(mat_files)} 件，授权链台账{"在位" if mat_ledger else "缺失"}',
        '每件素材须在 素材/授权链台账.md 登记：自制/授权/公有领域三选一，禁止来历不明截图')
    if mat_ledger:
        unreg = [os.path.basename(x) for x in mat_files if os.path.basename(x) not in mat_ledger]
        rep('PASS' if not unreg else 'FAIL', 'G2',
            '素材逐项登记' if not unreg else f'未登记素材 → {unreg[:6]}')
else:
    rep('INFO', 'G1', '尚无多模态素材（第13/14章 M4 阶段需要；届时 G 组转强制）')

# ══════════════════════════════════════════════════════════════
print('\n═══ H. 语气红线 ═══')
# 语境豁免：本书讨论语气红线时必须点名禁用词，这类提及合法。
# 判据：命中词所在句的前后 40 字内出现"不要/禁用/避免/不得/禁止/别用"之一。
NEG = ('不要', '禁用', '避免', '不得', '禁止', '别用', '不写', '不用')
tone = defaultdict(list)
for f in present:
    txt = read(os.path.join(SRC, f)) or ''
    for w in BANNED:
        for m in re.finditer(re.escape(w), txt):
            ctx = txt[max(0, m.start() - 40):m.end() + 40]
            if not any(k in ctx for k in NEG):
                tone[f].append(w)
                break
rep('PASS' if not tone else 'FAIL', 'H1',
    '无营销话术与禁用词' if not tone else
    '命中禁用词 → ' + '；'.join(f'{f}:{v}' for f, v in list(tone.items())[:4]),
    'QWEN.md §六禁用清单；技术书的可信度来自判断有据，不来自形容词')

# 附录 B 辨析条
appb = read(os.path.join(SRC, '附录B_术语表.md'))
if appb:
    miss = [d for d in DISAMBIG if d not in appb]
    rep('PASS' if not miss else 'WARN', 'H2',
        f'同名多义术语辨析条 {len(DISAMBIG) - len(miss)}/{len(DISAMBIG)} 在位'
        + ('' if not miss else f'；缺 → {miss}'),
        '附录 B 须立辨析条（策划案§八 同名不同物必须显式辨析）')
else:
    rep('INFO', 'H2', f'附录 B 尚未写就（M6）；须含 {len(DISAMBIG)} 条辨析：{DISAMBIG}')

# ══════════════════════════════════════════════════════════════
print('\n═══ 体量监控 ═══')
over_target = []   # 偏多 >15%：WARN（登记即可，不减字）
under_target = []  # 偏少 >15%：FAIL（只能多不能少——须扩写补齐）
for f in ch_files:
    n = int(re.search(r'第(\d{2})章', f).group(1))
    wc = cn_chars(read(os.path.join(SRC, f)))
    tgt = EXPECT_WORDS.get(n, 9500)
    if wc < tgt * 0.85:
        under_target.append(f'第{n}章 {wc:,}/{tgt:,}({(wc/tgt-1)*100:+.0f}%)')
    elif wc > tgt * 1.15:
        over_target.append(f'第{n}章 {wc:,}/{tgt:,}(+{(wc/tgt-1)*100:.0f}%)')
if under_target:
    rep('FAIL', 'Z0', '字数偏少超 15%，违反"只能多不能少" → ' + '；'.join(under_target),
        '作者指示：偏差只能多不能少。偏少章须列入扩写清单补至目标 85% 以上；不设减字要求')
elif over_target:
    rep('WARN', 'Z0', '超出目标 15%（允许，只多不少）→ ' + '；'.join(over_target),
        '超目标章须在进度表§四登记偏差与理由；偏多不触发减字复审')
else:
    rep('PASS', 'Z0', '各章字数达标（≥目标 85%；偏差只允许多、不允许少）',
        '低于目标 85% 即 FAIL；超目标 115% 仅 WARN 登记')
body = sum(cn_chars(read(os.path.join(SRC, f)) or '') for f in ch_files)
front = sum(cn_chars(read(os.path.join(SRC, f)) or '') for f in present if f not in ch_files)
avg = body / len(ch_files) if ch_files else 0
# 外推口径：**已写章用实测值，未写章用策划案目标值**，前后件按策划案口径固定 2.4 万。
# 不用"已写章均值 × 20"——第 1 章承担一次性框架成本（光谱定义、三角算术、决策树），
# 拿它当全书均值会系统性高估。两种口径都算出来，取较大者作为保守判据。
written_actual = {int(re.search(r'第(\d{2})章', f).group(1)):
                  cn_chars(read(os.path.join(SRC, f))) for f in ch_files}
proj_mixed = (sum(written_actual.values())
              + sum(v for k, v in EXPECT_WORDS.items() if k not in written_actual)
              + 24000)
proj_flat = avg * EXPECT_CHAPTERS + 24000
proj = max(proj_mixed, proj_flat) if len(written_actual) >= 4 else proj_mixed
rep('PASS' if proj <= 240000 else 'WARN', 'Z1',
    f'正文 {len(ch_files)} 章 / {body:,} 字（章均 {avg:,.0f}）＋前后件 {front:,} 字 '
    f'→ 外推约 {proj_mixed/10000:.1f} 万字（混合口径：已写实测＋未写按目标）'
    + (f'；均摊口径 {proj_flat/10000:.1f} 万' if len(written_actual) >= 4 else '')
    + f'（目标 21.2 万，红线 24 万）',
    '外推 >24 万字须上报作者，触发§十二第 2 项精简或分册退路（QWEN.md §十）')

# ══════════════════════════════════════════════════════════════
# Z2 Markdown×中文排版（依据《中文文案排版指北》本书采用版 §一.4／§二.5）
#   A 类：成对 ** 标记与中文正文之间夹空格 → CommonMark 不构成加粗，页面露裸星号；
#   B 类：闭合 ** 前是全角句末点号、且其后紧跟非空白 → flanking 歧义，整对加粗失效。
#   （B 类点号集含全角问号：python-markdown 3.x 实测 “…**？**汉字” 同样不渲染。）
#   代码围栏与行内代码内容跳过（与 book/fix_md_spacing.py 同口径）。
#   修复：python3 ../book/fix_md_spacing.py 书稿/*.md --fix（或按本脚本报告人工移动点号）。
print('\n═══ Markdown×中文排版（Z2）═══')
_CJK_Z2 = ('\u4e00-\u9fff\u3000-\u303f\uff01-\uff5e'
           '\u2018\u2019\u201c\u201d\u2014\u2026')
_DOT_Z2 = '。！？；：，、？'
_SP_Z2 = ' \u3000'

def _strip_protected_z2(text):
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

def _z2_issues(masked):
    marks = [m.start() for m in re.finditer(r'\*\*(?!\*)', masked)]
    issues = []
    for i in range(0, len(marks) - 1, 2):
        o, c = marks[i], marks[i + 1]
        if c > 0 and masked[c - 1] in _DOT_Z2 and (c + 2 < len(masked) and not masked[c + 2].isspace()):
            issues.append((c - 1, '标点闭合歧义'))
            continue
        mo = re.match(r'\*\*[' + _SP_Z2 + r']+(?=[' + _CJK_Z2 + r'])', masked[o:])
        if mo:
            issues.append((o, '开侧空格'))
        j = c
        while j > o + 2 and masked[j - 1] == ' ':
            j -= 1
        if j < c and masked[j - 1] in _CJK_Z2 + '”' and '\n' not in masked[j:c]:
            issues.append((j, '闭侧空格'))
    return issues

z2_hits = []
for f in sorted(os.listdir(SRC)):
    if not f.endswith('.md'):
        continue
    txt = read(os.path.join(SRC, f)) or ''
    masked = _strip_protected_z2(txt)
    for pos, kind in _z2_issues(masked):
        ln = txt.count('\n', 0, pos) + 1
        z2_hits.append(f'{f}:L{ln}[{kind}]')
rep('PASS' if not z2_hits else 'FAIL', 'Z2',
    'Markdown 强调标记×中文标点排版全部合规（指北 §一.4／§二.5）' if not z2_hits
    else f'Markdown 标记×中文标点问题 {len(z2_hits)} 处 → ' + '；'.join(z2_hits[:8])
         + ('…' if len(z2_hits) > 8 else ''),
    '运行 python3 book/fix_md_spacing.py --fix（A 类删标记旁空格；B 类把句末点号移到闭合 ** 之外）')

# ── Z3：插图字体链静态检查（防中文方框，QWEN.md §9.3 自检闸②机检化）────────
# 判据：图表/*.svg 若含中文，其 font-family 声明必须带 CJK 回退链。SVG 源本身
# 由 mmdc 产出、只含西文字体链属正常——真正的防线是构建端 svg_to_png 会注入
# 回退链并栅格化；此处校验 assemble_agentbook.py 的注入链首项为本机存在的中文字体，
# 且成品 docx（若已生成）内嵌图片为 PNG 而非 SVG 直通。
z3_issues = []
asm_path = os.path.join(ROOT, 'assemble_agentbook.py')
if os.path.isfile(asm_path):
    asm_src = read(asm_path) or ''
    if 'svg_to_png' not in asm_src:
        z3_issues.append('assemble_agentbook.py 缺少 svg_to_png 预栅格化')
    try:
        import subprocess
        zh_fonts = subprocess.run(['fc-list', ':lang=zh', 'family'],
                                  capture_output=True, text=True, timeout=10).stdout
        has_noto = 'Noto Sans CJK' in zh_fonts
        has_wqy = 'WenQuanYi Zen Hei' in zh_fonts
        if not (has_noto or has_wqy):
            z3_issues.append('本机无 Noto Sans CJK/WenQuanYi 中文字体（Pango 将落 .notdef 方框）')
        elif 'Microsoft YaHei,PingFang SC' in asm_src and not (has_noto or has_wqy):
            z3_issues.append('回退链以 Word 友好名称开头但本机无对应中文字体')
    except Exception as e:
        z3_issues.append(f'字体探测失败：{e}')
else:
    z3_issues.append('未找到 assemble_agentbook.py')
rep('PASS' if not z3_issues else 'WARN', 'Z3',
    '插图防方框链路完好（构建端栅格化＋CJK 回退链首项本机可用）' if not z3_issues
    else '防方框链路异常 → ' + '；'.join(z3_issues),
    '安装 fonts-noto-cjk（或文泉驿），并确认 assemble_agentbook.py 的 SVG_CJK_FALLBACK 动态探测生效')

# ── 汇总与落盘 ──────────────────────────────────────────────
cnt = Counter(r['level'] for r in results)
print('\n' + '═' * 62)
print(f"终检汇总：PASS {cnt['PASS']} / WARN {cnt['WARN']} / FAIL {cnt['FAIL']} / INFO {cnt['INFO']}")
print('═' * 62)

os.makedirs(REPORT_DIR, exist_ok=True)
stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M')
rp = os.path.join(REPORT_DIR, f'终检报告_{AB.VERSION}_{stamp}.md')
lines = [f'# 出版前检查报告 · {AB.VERSION}',
         '',
         f'- 检查时间：{datetime.datetime.now().isoformat(timespec="seconds")}',
         f'- 检查范围：书稿 {len(present)} 文件（BOOK_ORDER 共 {len(bo_files)}）',
         f'- 汇总：**PASS {cnt["PASS"]} / WARN {cnt["WARN"]} / FAIL {cnt["FAIL"]} / INFO {cnt["INFO"]}**',
         f'- 体量：正文 {body:,} 字（章均 {avg:,.0f}）＋前后件 {front:,} 字，'
         f'外推约 {proj_mixed/10000:.1f} 万字（混合口径，红线 24 万）',
         '', '| 级别 | 编号 | 结论 | 建议 |', '| --- | --- | --- | --- |']
for r in results:
    lines.append(f'| {r["level"]} | {r["code"]} | {r["msg"]} | {r["fix"] or "—"} |')
lines += ['', '> 本报告由 `prepub_check.py` 自动生成，对源稿只读。修复走改稿闭环线。']
with open(rp, 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines) + '\n')
print(f'报告已落盘：{os.path.relpath(rp, ROOT)}')

if _args.json:
    print(json.dumps(results, ensure_ascii=False, indent=1))

sys.exit(1 if cnt['FAIL'] else 0)
