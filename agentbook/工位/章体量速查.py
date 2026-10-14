"""章节体量与合规速查（只读工具，不属于书稿源）。"""
import re
import sys
import pathlib

HAN = re.compile(r'[\u4e00-\u9fff]')
EN = re.compile(r'[A-Za-z][A-Za-z0-9_\-\.]*')


def cn(t: str) -> int:
    return len(HAN.findall(t)) + len(EN.findall(t))


def report(path: str) -> None:
    t = pathlib.Path(path).read_text(encoding='utf-8')
    print(f'== {path}  cn_chars={cn(t)}')
    print('   anchors@', [m.start() for m in re.finditer(r'【时效锚点', t)])
    for w in ['Kong AI Gateway', 'LiteLLM', 'RouteLLM', 'vLLM Semantic Router',
              'Portkey', 'TrueFoundry', 'Envoy AI Gateway', 'Cloudflare AI Gateway']:
        pos = [m.start() for m in re.finditer(re.escape(w), t)]
        if pos:
            uncov = [p for p in pos if '【时效锚点' not in t[p:p + 3000]]
            print(f'   {w}: {pos}  未覆盖={uncov}')
    secs = re.split(r'^(## .*)$', t, flags=re.M)
    for i in range(1, len(secs), 2):
        body = secs[i] + secs[i + 1]
        print(f'   {cn(body):6d}  {secs[i][:44]}')


for p in sys.argv[1:]:
    report(p)
