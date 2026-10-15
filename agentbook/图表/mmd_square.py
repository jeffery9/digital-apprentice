#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mmd_square.py — Mermaid 图表"方正布局"工具（2026-10-15 作者指示）
==================================================================
约定（QWEN.md §九）：.mmd 是语义真相；本脚本只做**布局层**改写，不改任何
节点文字与连线语义。改图先改 .mmd 语义，再重跑本脚本补齐布局指令。

两类处理（均幂等，可反复运行）：
1) 注入/更新 %%{init:...}%% 主题块（flowchart / sequenceDiagram）：
   · flowchart：nodeSpacing/rankSpacing 加大 + 加宽 padding，让节点分布均匀、
     图面更"方"；
   · sequenceDiagram：actorMargin/messageMargin 加大 + useMaxWidth:false，
     避免时序图被压成一条细带；
   · xychart-beta 等其它类型不注入（其示意曲线由 render_curves.py 直出 PNG）。
2) 结构性重排（仅当上次渲染的 SVG 长宽比越界时执行，判据来自 图表/*.svg）：
   · 过扁（宽/高 > RATIO_FLAT）：LR→TB（或 TD/BT→TB）——把单行横链折成纵向，
     同时把并列 subgraph 从竖排改为每行 2~3 个的网格（内部方向取反保持内容横排）；
   · 过高（宽/高 < RATIO_TALL）：TB→LR——把多行纵堆改为横排。
   若尚无同名 SVG（首次作图），跳过重排、只注入 init。

用法：
  python3 mmd_square.py                 # 处理 图表/*.mmd 全部
  python3 mmd_square.py 图4-1*.mmd ...  # 只处理指定文件
  python3 mmd_square.py --dry-run       # 只报告将做的改动，不落盘
"""
import glob
import os
import re
import sys

DIR = os.path.dirname(os.path.abspath(__file__))
RATIO_FLAT = 1.95      # 宽/高 超过此值 → 判"过扁"，需要增高（转纵向/网格化）
RATIO_TALL = 0.72      # 宽/高 低于此值 → 判"过高"，需要增宽（转横向）

FC_INIT = ('%%{init: {"flowchart": {"nodeSpacing": 70, "rankSpacing": 90, '
           '"padding": 18, "useMaxWidth": false}}}%%')
SEQ_INIT = ('%%{init: {"sequence": {"actorMargin": 130, "messageMargin": 55, '
            '"boxMargin": 12, "useMaxWidth": false}}}%%')

SUB_RE = re.compile(r'^([ \t]*)subgraph[ \t]+([\w-]+)(?:\[[^\]]*\])?[ \t]*$', re.M)
DIR_RE = re.compile(r'^([ \t]*)direction[ \t]+(TB|BT|LR|RL)[ \t]*$', re.M)


def svg_ratio(stem):
    """读取上次渲染 SVG 的 viewBox 长宽比；无 SVG 或**手工渲染器产物**则 None。

    判据：Mermaid CLI 在 <style> 中必带 ".flowchart"/".sequence" 类名；
    render_*.py 手工直出的 SVG（如图2-1，1680×1050 本已方正）不含这些标记，
    一律返回 None，避免把精心制版的图误判越界而重排。
    """
    p = os.path.join(DIR, stem + '.svg')
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding='utf-8') as f:
            whole = f.read()
        if ('.flowchart' not in whole and '.sequence' not in whole
                and '.node' not in whole):
            return None
        with open(p, encoding='utf-8') as f:
            head = f.read(4000)
        m = re.search(r'<svg[^>]*viewBox="[-\d.\s]+"', head)
        if not m:
            return None
        nums = re.findall(r'[-\d.]+', m.group(0).split('viewBox=')[1])
        if len(nums) >= 4:
            w, h = float(nums[2]), float(nums[3])
            return w / h if h else None
    except Exception:
        pass
    return None


def split_top_level(src):
    """按顶层（非缩进）subgraph 头把源码切成 [prose, block1, block2, ...]。"""
    heads = [m for m in re.finditer(r'^subgraph\b.*?(?=^\s*end\s*$)\s*^end\s*$',
                                     src, re.M | re.S)]
    if not heads:
        return None
    pieces, prev = [], 0
    for h in heads:
        pieces.append(src[prev:h.start()])
        pieces.append(h.group(0))
        prev = h.end()
    pieces.append(src[prev:])
    # prose 段合并到首元素之前？保持原样：pieces 奇偶交替（prose, block, prose, block…, tail）
    return pieces


def regroup_grid(src, per_row):
    """并列 subgraph 块改为每行 per_row 个：用 ~~~ 隐形边串联同行相邻块。
    要求这些 subgraph 之间原本没有显式连线（图11-1/10-2 即如此）。"""
    blocks = re.findall(r'^subgraph\b.*?^\s*end\s*$', src, re.M | re.S)
    if len(blocks) < 3:
        return src, False
    # 块间不能有引用彼此内部节点的连线
    ids = [re.match(r'subgraph\s+([\w-]+)', b).group(1) for b in blocks]
    outside = src
    for b in blocks:
        outside = outside.replace(b, '')
    for i in ids:
        if re.search(r'^\s*' + re.escape(i) + r'\b.*(-->|---|-\.->|~~~)', outside, re.M):
            return src, False
    rows = []
    for s in range(0, len(blocks), per_row):
        row = blocks[s:s + per_row]
        chain = '\n'.join(row)
        links = '\n'.join(f'{ids[s + j]} ~~~ {ids[s + j + 1]}'
                          for j in range(len(row) - 1))
        rows.append(chain + ('\n' + links if links else ''))
    new_src = src
    for b in blocks:
        new_src = new_src.replace(b, '\x00', 1)
    parts = new_src.split('\x00')
    out = parts[0]
    for k, blk in enumerate(rows):
        out += blk + parts[k + 1]
    return out, True


def regroup_explicit(src, groups):
    """按给定的分组顺序（id 列表的列表）重排顶层 subgraph 块。
    专用于块间存在连线的图（如图4-1：APPS→GW→POOL），保持语义不动。"""
    order = [i for row in groups for i in row]
    pat = re.compile(r'^subgraph[ \t]+(' + '|'.join(order) + r')\b.*?^\s*end\s*$',
                     re.M | re.S)
    found = {m.group(1): m.group(0) for m in pat.finditer(src)}
    if set(found) != set(order):
        return src, False
    out = pat.sub('\x00', src)
    rows_txt = []
    for row in groups:
        chain = '\n'.join(found[i] for i in row)
        links = '\n'.join(f'{row[j]} ~~~ {row[j + 1]}'
                          for j in range(len(row) - 1))
        rows_txt.append(chain + ('\n' + links if links else ''))
    parts = out.split('\x00')
    res = parts[0]
    for k, blk in enumerate(rows_txt):
        res += blk + parts[k + 1]
    return res, True


def flip_direction(src, new_dir):
    src, n = re.subn(r'^(flowchart|graph)\s+(TB|BT|LR|RL)',
                     lambda m: f'{m.group(1)} {new_dir}', src, count=1, flags=re.M)
    return src, n


def swap_subgraph_dirs(src):
    """顶层 subgraph 内部 direction TB↔LR、BT↔RL 互换（配合主方向翻转）。"""
    lines = src.split('\n')
    depth = 0
    for i, ln in enumerate(lines):
        if re.match(r'^\s*subgraph\b', ln):
            if depth == 0 and re.match(r'^\s*direction\s+(TB|BT|LR|RL)\s*$',
                                       lines[i + 1] if i + 1 < len(lines) else ''):
                d = lines[i + 1].strip().split()[1]
                nd = {'TB': 'LR', 'BT': 'RL', 'LR': 'TB', 'RL': 'BT'}[d]
                lines[i + 1] = re.sub(r'(direction\s+)\S+', r'\g<1>' + nd,
                                      lines[i + 1])
            depth += 1
        elif re.match(r'^\s*end\s*$', ln):
            depth = max(0, depth - 1)
    return '\n'.join(lines)


def ensure_init(src, kind):
    want = FC_INIT if kind == 'flowchart' else SEQ_INIT
    if re.match(r'^\s*%%\{init:', src):
        return re.sub(r'^\s*%%\{init:.*?\}%%', want, src, count=1, flags=re.S), True
    lines = src.split('\n')
    ins = next((i for i, ln in enumerate(lines)
                if re.match(r'^(flowchart|sequenceDiagram|graph)\b', ln)), None)
    if ins is None:
        return src, False
    lines.insert(ins, want)
    return '\n'.join(lines), True


def process(path, dry=False):
    name = os.path.basename(path)
    with open(path, encoding='utf-8') as f:
        src = f.read()
    first = next((ln for ln in src.split('\n') if ln.strip()
                  and not ln.strip().startswith('%%')), '')
    kind = ('flowchart' if re.match(r'^(flowchart|graph)\b', first) else
            'sequence' if re.match(r'^sequenceDiagram\b', first) else None)
    if kind is None:
        return f'SKIP（非 flowchart/sequence：{first[:20]}）'
    actions = []
    src2, ok = ensure_init(src, kind)
    if ok:
        actions.append('init')
    if kind == 'flowchart':
        ratio = svg_ratio(os.path.splitext(name)[0])
        if ratio and ratio > RATIO_FLAT:
            m = re.search(r'^(flowchart|graph)\s+(TB|BT|LR|RL)', src2, re.M)
            main = m.group(2)
            nblocks = len(re.findall(r'^subgraph\b', src2, re.M))
            if main in ('LR', 'RL'):          # 单行横链 → 纵链
                src2, _ = flip_direction(src2, 'TB')
                src2 = swap_subgraph_dirs(src2)
                actions.append(f'LR→TB（ratio={ratio:.2f}）')
            elif nblocks >= 3:                # 竖堆并列块 → 每行3网格
                src2, done = regroup_grid(src2, 3)
                if done:
                    actions.append(f'并列块网格化×3（ratio={ratio:.2f}）')
                else:
                    src2, _ = flip_direction(src2, 'LR')
                    src2 = swap_subgraph_dirs(src2)
                    actions.append(f'TB→LR（ratio={ratio:.2f}）')
        elif ratio and ratio < RATIO_TALL:
            src2, _ = flip_direction(src2, 'LR')
            src2 = swap_subgraph_dirs(src2)
            actions.append(f'TB→LR（ratio={ratio:.2f}）')
    changed = src2 != src
    if changed and not dry:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(src2)
    return ('CHANGED[' + '; '.join(actions) + ']' if changed
            else 'OK（无需改动）') + (' [dry-run]' if dry and changed else '')


def main():
    args = [a for a in sys.argv[1:] if a != '--dry-run']
    dry = '--dry-run' in sys.argv[1:]
    files = (args if args and args[0].endswith('.mmd')
             else sorted(glob.glob(os.path.join(DIR, '*.mmd'))))
    if args and not args[0].endswith('.mmd'):
        files = sorted(glob.glob(os.path.join(DIR, args[0])))
    n = 0
    for p in files:
        if not os.path.isfile(p):
            print(f'!! 不存在：{p}')
            continue
        r = process(p, dry)
        print(f'{os.path.basename(p):50s} {r}')
        if r.startswith('CHANGED'):
            n += 1
    print(f'── 共处理 {len(files)} 个，改动 {n} 个 ──')


if __name__ == '__main__':
    main()
