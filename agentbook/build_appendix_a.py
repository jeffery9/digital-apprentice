#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""附录 A 骨架生成器（M6）——台账是模式卡清单的唯一数据源（QWEN.md §五·维护规则 4）。

用法：
    python3 build_appendix_a.py            # 打印骨架到 stdout（人读 diff 用）
    python3 build_appendix_a.py --write    # 生成并覆盖 书稿/附录A_设计模式速查目录.md

分工纪律（防两处打架）：
  - 「一、台账」表格决定：每族有哪些卡、归属章、共卡关系、族顺序 → 机器生成；
  - 一行意图文案与 A.0 去重裁决叙述写在 书稿/附录A_设计模式速查目录.md 的
    `## A.N 族名` 小节正文里，生成时**原样保留**，只重建各族的卡表。
  - 新卡入台账后重跑本脚本，卡自动进附录 A；意图列留空则由人工补写。
"""
import re
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(HERE, '书稿', '模式卡', '模式卡台账.md')
APPENDIX = os.path.join(HERE, '书稿', '附录A_设计模式速查目录.md')

FAMILY_ORDER = ['规划', '经典·基础构件', '模型编排与路由', '上下文', '工具', '记忆',
                '反思', '编排', '多 agent', '协议', '多模态', '运行时', '长时程',
                '安全', '评估']
FAMILY_TITLE = {
    '规划': '规划（Planning）｜第 5 章',
    '经典·基础构件': '经典·基础构件｜第 1、3 章',
    '模型编排与路由': '模型编排与路由｜第 4 章（路由族模板）',
    '上下文': '上下文（Context）｜第 6 章',
    '工具': '工具（Tools）｜第 7 章',
    '记忆': '记忆（Memory）｜第 8 章',
    '反思': '反思（Reflection）｜第 9 章',
    '编排': '编排（Orchestration）｜第 10 章',
    '多 agent': '多 agent｜第 11 章',
    '协议': '协议（Protocols）｜第 12 章（路由族模板）',
    '多模态': '多模态（Multimodal）｜第 13、14 章',
    '运行时': '运行时（Runtime）｜第 15 章（长时程族模板）',
    '长时程': '长时程（Long-horizon）｜第 16 章（长时程族模板）',
    '安全': '安全（Security）｜第 17 章',
    '评估': '评估（Evaluation）｜第 18 章',
}


def parse_ledger():
    """台账「一、台账」→ [(族, 卡名, 归属章, 共卡/引用)]，保持行序。"""
    with open(LEDGER, encoding='utf-8') as f:
        text = f.read()
    sec = re.split(r'^## ', text, flags=re.M)
    sec1 = next(s for s in sec if s.startswith('一、台账'))
    rows = [ln for ln in sec1.split('\n')
            if ln.startswith('|') and '---' not in ln]
    out = []
    for r in rows[1:]:
        cols = [c.strip() for c in re.split(r'\|', r)[1:-1]]
        if len(cols) < 4:
            continue
        name, family, home, shared = cols[0], cols[1], cols[2], cols[3]
        out.append((family, name, home, shared))
    return out


def extract_intents(existing):
    """从现版附录 A 各族卡表提取 卡名→意图 映射（人工文案，生成时保留）。

    兼容手工版的 **加粗** 与「（合并组注记）」前缀；台账改名（如补全英文对照）
    后旧名经去括注归一化仍可命中。"""
    def norm(s):
        s = s.replace('**', '').strip()
        # 去掉尾部括注：路由（工作流）→ 路由；但保留 "ReAct（react loop）" 型？
        return s
    intents = {}
    for m in re.finditer(r'^\| ([^|]+?) \| ([^|]*?) \| [^|]* \|[^|]*\|\s*$',
                         existing, flags=re.M):
        name, intent = m.group(1).strip(), m.group(2).strip()
        if name in ('卡名', '') or intent.startswith('一行意图') or set(name) <= set('-| '):
            continue
        intent_clean = re.sub(r'^（[^）]*）', '', intent).replace('⊕', '').strip()
        if not intent_clean:
            continue
        keys = {norm(name)}
        base = re.sub(r'（[^）]*）$', '', name.replace('**', '')).strip()
        keys.add(base)
        for k in keys:
            intents[k] = intent_clean
    return intents


def lookup_intent(intents, name):
    for key in (name, re.sub(r'（[^）]*）$', '', name).strip()):
        if key in intents:
            return intents[key]
    return None


def render(cards, intents):
    fams = {}
    for family, name, home, shared in cards:
        fams.setdefault(family, []).append((name, home, shared))
    parts = ['# 附录 A 设计模式速查目录\n',
             '> 排版与标注说明：本附录由 `书稿/模式卡/模式卡台账.md` 第一节生成'
             '（维护规则 4，唯一数据源），不得在正文与本表之间维护第三份清单。'
             '组装时本引用块由 strip_draft_notes 删除。\n']
    # A.0 保留现版叙述（若存在）
    parts.append('## A.0 计数口径与去重裁决\n')
    a0 = re.search(r'## A\.0 计数口径与去重裁决\n(.*?)\n## A\.1', existing, flags=re.S)
    parts.append((a0.group(1).rstrip() + '\n') if a0 else
                 '【待补：合并裁决叙述】\n')
    for i, family in enumerate(FAMILY_ORDER, start=1):
        title = FAMILY_TITLE.get(family, family)
        parts.append(f'## A.{i} {title}\n')
        prev = re.search(rf'## A\.{i} .*?\n(.*?)(?=\n## A\.{i + 1}|\n## A\.1[0-9]?|\Z)',
                         existing, flags=re.S)
        prose_lines = []
        if prev:
            block = prev.group(1)
            prose_lines = [ln for ln in block.split('\n')
                           if ln.strip() and not ln.startswith('|')]
        parts.append('| 卡名 | 一行意图 | 归属 | 共卡／引用 |')
        parts.append('| --- | --- | --- | --- |')
        for name, home, shared in fams.get(family, []):
            intent = lookup_intent(intents, name) or '【待补意图】'
            parts.append(f'| {name} | {intent} | {home} | {shared} |')
        parts.append('')
        for ln in prose_lines:
            if '一行意图' in ln or ln.startswith('使用约定'):
                continue
            parts.append(ln)
        parts.append('')
    # 尾部小节（A.16/A.17/A.18/A.19 等）原样带过来
    tail = re.search(r'(\n## A\.1[6-9].*)$', existing, flags=re.S)
    if tail:
        parts.append(tail.group(1).strip() + '\n')
    return '\n'.join(parts)


def main():
    ap = __import__('argparse').ArgumentParser(description='附录 A 骨架生成器（台账为唯一数据源）')
    ap.add_argument('--write', action='store_true', help='覆盖写入 书稿/附录A_设计模式速查目录.md')
    ap.add_argument('--check', action='store_true',
                    help='幂等校验：重生成与现版比对，卡表漂移即退出码 1（prepub C5 用）')
    args, _ = ap.parse_known_args()
    with open(APPENDIX, encoding='utf-8') as f:
        global existing
        existing = f.read()
    cards = parse_ledger()
    intents = extract_intents(existing)
    out = render(cards, intents)
    missing = [n for _, n, _, _ in cards if not lookup_intent(intents, n)]
    if args.check:
        strip = lambda s: [ln.rstrip() for ln in s.splitlines() if ln.strip()]
        drift = [a for a, b in zip(strip(out), strip(existing)) if a != b] \
            if len(strip(out)) == len(strip(existing)) else ['行数漂移']
        if missing or drift:
            print('附录 A 与台账漂移：' + (f'缺意图 {missing[:4]}；' if missing else '')
                  + (f'差异行 {len(drift)} 处' if drift else ''))
            sys.exit(1)
        print(f'附录 A 与台账同步（{len(cards)} 张卡，零漂移）')
        return
    if args.write:
        with open(APPENDIX, 'w', encoding='utf-8') as f:
            f.write(out)
        print(f'已写入 {APPENDIX}（卡 {len(cards)} 张；'
              f'意图缺文案 {len(missing)} 张{": " + "、".join(missing[:6]) if missing else ""}）')
    else:
        print(out)
        print(f'\n[骨架统计] 卡 {len(cards)} 张；意图缺文案 {len(missing)} 张', file=sys.stderr)


if __name__ == '__main__':
    main()
