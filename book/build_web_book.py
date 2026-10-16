#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
《数字见习生》GitHub Pages 电子书构建脚本
=========================================

功能：
  1. 复用 assemble_book.py 的成书逻辑（BOOK_ORDER、草稿注释清理、弯引号规范化、
     特殊规则），按成书顺序把书稿渲染为静态 HTML，输出到仓库根目录 docs/。
  2. 图片压缩副本：插画原图约 174MB，不宜直接上 GitHub；被引用的 PNG 转 JPEG
     （长边≤1600px、q82）存入 docs/images/，HTML 指向压缩副本。源文件只读不改。
  3. 生成：toc.html（分篇目录）、各篇页面、index.html（单页全书 + 下载入口）、
     searchindex.js + search.js（纯前端全文搜索）、style.css、_config.yml、.nojekyll。
  4. 可选 --serve 本地预览。

用法：
  python3 build_web_book.py             # 构建 docs/
  python3 build_web_book.py --serve     # 构建后本地预览（默认端口 8080）
  python3 build_web_book.py --no-images # 跳过图片压缩（调试用，页面图片将缺失）

依赖：python3、markdown、pillow（pip install markdown pillow）。
发布：推送到 GitHub 后，在仓库 Settings → Pages 选择分支 main、目录 /docs，
      即可通过 https://<用户名>.github.io/<仓库名>/ 访问。
"""

import argparse
import datetime
import http.server
import json
import os
import re
import shutil
import socketserver
import sys
import threading
import webbrowser

from PIL import Image

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

try:
    import assemble_book as ab  # noqa: E402
    SRC_DIR = ab.SRC_DIR
    VERSION = ab.VERSION
except ImportError:      # assemble_book 顶层依赖 pypandoc 等，缺失时退回独立模式
    ab = None
    SRC_DIR = os.path.join(SCRIPT_DIR, '书稿')
    VERSION = 'v0.8l'

ILLUS_DIR = os.path.join(SRC_DIR, '插画')
DOCS_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), 'docs')   # 仓库根/docs
MERGED_MD = os.path.join(SCRIPT_DIR, '输出', '《数字见习生》全书稿_合并版.md')
MERGED_DOCX = os.path.join(SCRIPT_DIR, '输出', '《数字见习生》全书稿_合并版.docx')

BOOK_TITLE = '数字见习生'
BOOK_SUBTITLE = '写给无编程经验读者的 AI Agent 入门书'

# 与 assemble_book.BOOK_ORDER 一致的成书顺序（assemble_book 可导入时优先用其数据）
DEFAULT_ORDER = [
    ('file', '前言.md'),
    ('part', '第一篇 入门与动手：逐构件实战（第 1–6 章）', '扉页_第一篇_先动手.png'),
    ('file', '第01章_你好数字见习生.md'),
    ('file', '第02章_构件一Prompt写好员工手册.md'),
    ('file', '第03章_构件二知识库给见习生配资料室.md'),
    ('file', '第04章_构件三Skill给见习生写操作手册.md'),
    ('file', '第05章_构件四Sub-agent组建小团队.md'),
    ('file', '第06章_构件五工作流与毕业项目.md'),
    ('part', '第二篇 深入：需要时再懂的原理手册（使用指南 + 第 7–13 章）', '扉页_第二篇_深入.png'),
    ('file', '第07章_新物种全貌.md'),
    ('file', '第08章_数字见习生的内心.md'),
    ('file', '第09章_边想边做与先谋后动.md'),
    ('file', '第10章_学会复盘.md'),
    ('file', '第11章_好记性不如烂笔头.md'),
    ('file', '第12章_开卷考试的艺术.md'),
    ('file', '第13章_三个臭皮匠.md'),
    ('part', '第三篇 保持清醒：边界、伦理与未来（第 14–15 章）', '扉页_第三篇_清醒.png'),
    ('file', '第14章_守住边界.md'),
    ('file', '第15章_展望.md'),
    ('file', '附录A_术语表.md'),
    ('file', '附录B_检索与速查.md'),
    ('file', '附录C_场景配方集与工具清单.md'),
    ('file', '附录D_延伸阅读.md'),
    ('file', '附录E_教学使用建议.md'),
    ('file', '附录F_实验指南与记录表.md'),
    ('file', '附录G_大模型常识与谈资.md'),
    ('file', '后记.md'),
]

try:
    import markdown as md_lib
except ImportError:
    md_lib = None


def log(msg):
    print(msg, flush=True)


# ----------------------------------------------------------------------------
# Markdown → HTML
# ----------------------------------------------------------------------------
def preprocess_markdown(text):
    """图片路径 插画/x.png → images/x.jpg；行首全角空格转 &nbsp; 防折叠；
    表单类引用块行尾补硬换行（两个空格），防止字段行被渲染器合并成一段；
    粗体 **…** 预转换，规避 CommonMark 在闭合 ** 后紧跟中文/标点时不识别 strong 的问题。"""
    def _img(m):
        alt, path = m.group(1), m.group(2)
        fname = path.split('/')[-1]
        return '![%s](images/%s)' % (alt, image_target_name(fname))
    text = re.sub(r'^!\[([^\]]*)\]\(([^)]+)\)', _img, text, flags=re.M)
    text = re.sub(r'^(　+)', lambda m: '&nbsp;' * len(m.group(1)), text, flags=re.M)
    # 引用块内非空、非纯标记行补行尾双空格（Markdown 硬换行），保证表单逐行显示
    lines = []
    for ln in text.split('\n'):
        if ln.startswith('>') and not ln.rstrip().endswith(('  ', '\\')):
            inner = ln.lstrip('> ').rstrip()
            if inner and inner not in ('-', '---', '***') and not inner.startswith('|'):
                ln = ln.rstrip() + '  '
        lines.append(ln)
    text = '\n'.join(lines)
    # 粗体预转换：源稿约定 ** 内不含斜体星号，故 [^*]+? 配对安全；
    # 奇数残留的孤立 ** 保持原样，交由 prepub_check 报告。
    text = re.sub(r'\*\*(?!\s)([^*]+?)\*\*', r'<strong>\1</strong>', text)
    return text


def render_markdown(text):
    html = md_lib.markdown(preprocess_markdown(text),
                           extensions=['tables', 'sane_lists'],
                           output_format='html5')
    return html.replace('<img ', '<img loading="lazy" ')


def split_front_h1(html):
    """拆出正文首个 H1 作为条目标题，返回 (h1文本或None, 剩余html)。"""
    m = re.search(r'<h1>(.*?)</h1>', html, flags=re.S)
    if not m:
        return None, html
    h1 = re.sub(r'<[^>]+>', '', m.group(1)).strip()
    prefix, rest = html[:m.start()].strip(), html[m.end():].strip()
    joined = (prefix + '\n' + rest).strip() if prefix else rest
    return h1, joined


# ----------------------------------------------------------------------------
# 图片压缩（PNG → JPEG 副本，源文件只读）
# ----------------------------------------------------------------------------
_img_cache = {}


def image_target_name(fname):
    stem, ext = os.path.splitext(fname)
    return stem + '.jpg' if ext.lower() == '.png' else fname


def compress_image(fname, img_dir, max_side=1600, quality=82):
    src = os.path.join(ILLUS_DIR, fname)
    if not os.path.isfile(src):
        raise RuntimeError('缺少插画文件：' + src)
    if fname in _img_cache:
        return _img_cache[fname]
    target = image_target_name(fname)
    dst = os.path.join(img_dir, target)
    if target.lower().endswith('.jpg'):
        with Image.open(src) as im:
            im = im.convert('RGB')
            w, h = im.size
            scale = min(1.0, max_side / max(w, h))
            if scale < 1.0:
                im = im.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
            im.save(dst, 'JPEG', quality=quality, optimize=True)
    else:
        shutil.copy2(src, dst)
    _img_cache[fname] = target
    return target


def collect_referenced_images(text):
    return re.findall(r'^!\[[^\]]*\]\(插画/([^)]+)\)', text, flags=re.M)


# ----------------------------------------------------------------------------
# 书稿条目加载（与 assemble_book.assemble_markdown 同一套清理规则）
# ----------------------------------------------------------------------------
_used_anchors = set()


def slugify(fname, title):
    s = re.sub(r'<[^>]+>', '', title)
    s = re.sub(r'[^\w\s-]', '', s, flags=re.UNICODE)
    ascii_part = re.sub(r'[^a-zA-Z0-9-]', '', s)
    if ascii_part.strip('-'):
        base = 'ch-' + ascii_part[:24]
    else:
        base = 'sec-%04x' % (zlib_crc(fname) & 0xFFFF)
    anchor, n = base, 2
    while anchor in _used_anchors:
        anchor = '%s-%d' % (base, n)
        n += 1
    _used_anchors.add(anchor)
    return anchor


def zlib_crc(s):
    import zlib
    return zlib.crc32(s.encode('utf-8')) & 0xFFFFFFFF


def clean_text(fname, text):
    """套用与 docx 组装相同的清理规则，保证网页版与 Word 版内容一致。"""
    if ab is not None:
        text = ab.apply_special_rule(fname, text)
        text, _ = ab.strip_draft_notes(text, final=True)
        text = re.sub(r'\A(# [^\n]+\n)\n+---+\n', r'\1\n', text)
        text, _, skipped = ab.normalize_cn_quotes(text)
        for s in skipped:
            log('  ! 弯引号跳过（奇数引号行）：%s: %s' % (fname, s[:30]))
        text = ab.ensure_table_blank_lines(text)[0]
    return text


def load_entry(fname, img_dir, do_images):
    """读取并清理单个书稿文件，返回 (title, anchor, section_html, plain_text)。"""
    path = os.path.join(SRC_DIR, fname)
    if not os.path.isfile(path):
        raise RuntimeError('缺少书稿文件：' + path)
    text = open(path, encoding='utf-8').read()
    text = clean_text(fname, text)
    if do_images:
        for img in collect_referenced_images(text):
            compress_image(img, img_dir)
    html = render_markdown(text)
    h1, html = split_front_h1(html)
    title = h1 or os.path.splitext(fname)[0]
    anchor = slugify(fname, title)
    section = '<section id="%s" class="entry">\n<h1>%s</h1>\n%s\n</section>' % (
        anchor, title, html)
    plain = re.sub(r'<[^>]+>', ' ', html)
    return title, anchor, section, plain


# ----------------------------------------------------------------------------
# 组装：把 BOOK_ORDER 分成页面组（前言/三篇/附录/后记）
# ----------------------------------------------------------------------------
def make_groups(order, img_dir, do_images):
    groups, cur = [], None
    for item in order:
        kind = item[0]
        if kind == 'part':
            short = item[1].split('　')[0]          # 第一篇/第二篇/第三篇
            cover_html = ''
            if do_images:
                compress_image(item[2], img_dir)
                cover_html = ('<p class="cover"><img loading="lazy" '
                              'src="images/%s" alt="%s扉页"></p>'
                              % (image_target_name(item[2]), short))
            cur = {'file': short + '.html', 'title': item[1], 'entries': [],
                   'cover_html': cover_html}
            groups.append(cur)
        else:
            fname = item[1]
            base = os.path.splitext(fname)[0]
            if base == '前言':
                cur = {'file': '前言.html', 'title': '前言', 'entries': [],
                       'cover_html': ''}
                groups.insert(0, cur)
            elif base == '后记':
                cur = {'file': '后记.html', 'title': '后记', 'entries': [],
                       'cover_html': ''}
                groups.append(cur)
            elif base.startswith('附录'):
                if not groups or groups[-1].get('file') != '附录.html':
                    cur = {'file': '附录.html', 'title': '附录 A–G', 'entries': [],
                           'cover_html': ''}
                    groups.append(cur)
            elif cur is None:
                raise RuntimeError('无法归组：' + fname)
            title, anchor, section, plain = load_entry(fname, img_dir, do_images)
            cur['entries'].append({'fname': fname, 'title': title, 'anchor': anchor,
                                   'section': section, 'plain': plain})
    return groups


def group_body(g):
    parts = []
    if g['cover_html']:
        parts.append('<h1>%s</h1>\n%s' % (g['title'], g['cover_html']))
    parts.append('\n'.join(e['section'] for e in g['entries']))
    return '\n'.join(parts)


# ----------------------------------------------------------------------------
# 页面模板
# ----------------------------------------------------------------------------
CSS = """
:root { --fg:#2b2b2b; --bg:#ffffff; --accent:#8b1a1a; --muted:#888; --line:#e6e2da; }
* { box-sizing:border-box; }
body { margin:0; font-family:"Source Han Serif SC","Noto Serif CJK SC","Songti SC",serif;
       color:var(--fg); background:var(--bg); line-height:1.9; }
header.topbar { position:sticky; top:0; z-index:10; background:#fdfbf7ee;
  border-bottom:1px solid var(--line); padding:.6rem 1rem; display:flex; gap:.8rem;
  align-items:center; flex-wrap:wrap; backdrop-filter:blur(4px); }
header.topbar a { color:var(--accent); text-decoration:none; font-weight:bold; }
header.topbar form { flex:1; min-width:180px; max-width:420px; }
header.topbar input#q { width:100%; padding:.35rem .6rem; border:1px solid var(--line);
  border-radius:6px; font-size:.95rem; }
main.book { max-width:820px; margin:0 auto; padding:1.5rem 1.2rem 4rem; }
main.book h1 { color:var(--accent); font-size:1.7rem; border-bottom:2px solid var(--accent);
  padding-bottom:.4rem; margin-top:2.5rem; }
section.entry:first-of-type > h1 { margin-top:.5rem; }
main.book h2 { font-size:1.35rem; margin-top:2rem; }
main.book h3 { font-size:1.15rem; }
main.book img { max-width:100%; height:auto; display:block; margin:1rem auto; border-radius:4px; }
main.book blockquote { border-left:4px solid var(--accent); background:#faf6ef;
  margin:1rem 0; padding:.6rem 1rem; color:#555; }
main.book table { border-collapse:collapse; width:100%; margin:1rem 0; font-size:.95rem;
  display:block; overflow-x:auto; }
main.book th, main.book td { border:1px solid var(--line); padding:.45rem .6rem; }
main.book th { background:#f5efe3; }
main.book hr { border:none; border-top:1px solid var(--line); margin:2.5rem 0; }
.pagenav { display:flex; justify-content:space-between; max-width:820px; margin:0 auto;
  padding:0 1.2rem 2rem; gap:1rem; }
.pagenav a { flex:1; text-align:center; padding:.6rem; border:1px solid var(--line);
  border-radius:8px; text-decoration:none; color:var(--fg); background:#fdfbf7; }
.pagenav a:hover { border-color:var(--accent); color:var(--accent); }
.pagenav span { flex:1; }
footer.pagefoot { text-align:center; color:var(--muted); font-size:.85rem;
  padding:2rem 1rem; border-top:1px solid var(--line); }
ul.toclist { list-style:none; padding-left:0; }
ul.toclist ul { list-style:none; padding-left:1.4rem; }
ul.toclist li { margin:.3rem 0; }
ul.toclist a { color:var(--fg); text-decoration:none; }
ul.toclist a:hover { color:var(--accent); }
.hit { margin:1rem 0; }
.hit a { color:var(--accent); font-weight:bold; }
mark { background:#ffe9a8; }
/* 防盗版：水印样式 */
.watermark { position:fixed; top:0; left:0; width:100%; height:100%; pointer-events:none; z-index:9999; opacity:0.03; }
.watermark-text { position:absolute; top:50%; left:50%; transform:translate(-50%,-50%) rotate(-30deg); font-size:3rem; color:#000; white-space:nowrap; }
/* 防盗版：打印时显示版权信息 */
@media print {
  header.topbar,.pagenav { display:none; }
  body::before { content:"《数字见习生》版权所有 © jeffery · 未经授权禁止传播"; display:block; text-align:center; padding:1rem; background:#ff0; color:#000; font-weight:bold; }
  .watermark { opacity:0.08; }
}
"""

JS_SEARCH = r"""
(function(){
  var q = document.getElementById('q');
  if(!q) return;
  var base = window.location.pathname.replace(/[^/]*$/, '');
  function esc(s){ return s.replace(/[&<>"]/g, function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  function run(kw){
    var idx = window.WEBBOOK_INDEX;
    var box = document.getElementById('results');
    if(!idx || !box) return;
    if(!kw){ box.innerHTML=''; return; }
    var hits=[], total=0;
    var re; try{ re = new RegExp(kw.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'),'gi'); }catch(e){ return; }
    for(var i=0;i<idx.length && hits.length<30;i++){
      var p=idx[i], m, positions=[];
      re.lastIndex=0;
      while((m=re.exec(p.text))!==null){ positions.push(m.index); if(positions.length>200) break; }
      if(!positions.length) continue;
      total += positions.length;
      var snips=[];
      for(var k=0;k<Math.min(positions.length,3);k++){
        var s=Math.max(0,positions[k]-40), e=Math.min(p.text.length,positions[k]+kw.length+60);
        snips.push('…'+esc(p.text.slice(s,e)).replace(re,function(x){return '<mark>'+x+'</mark>';})+'…');
      }
      hits.push('<div class="hit"><a href="'+base+p.file+'#'+p.anchor+'">'+esc(p.title)+'</a><div>'+snips.join('<br>')+'</div></div>');
    }
    box.innerHTML = hits.length ? ('<p>共 '+total+' 处提及，显示前 '+hits.length+' 个条目：</p>'+hits.join('<hr>'))
                               : ('<p>未找到「'+esc(kw)+'」。</p>');
  }
  function ensureIndex(cb){
    if(window.WEBBOOK_INDEX){ cb(); return; }
    var s=document.createElement('script'); s.src=base+'searchindex.js';
    s.onload=cb; document.head.appendChild(s);
  }
  var timer=null;
  q.addEventListener('input', function(){
    clearTimeout(timer);
    var kw=q.value.trim();
    timer=setTimeout(function(){ ensureIndex(function(){ run(kw); }); }, 250);
  });
  var pm = /[?&]q=([^&]+)/.exec(window.location.search);
  if(pm){ q.value = decodeURIComponent(pm[1].replace(/\+/g,' '));
    ensureIndex(function(){ run(q.value.trim()); }); }
})();
"""

# 防盗版：水印追踪 + 禁止复制
JS_ANTITHEFT = r"""
(function(){
  // 水印追踪：在页面中嵌入用户标识
  function addWatermark() {
    var canvas = document.createElement('canvas');
    canvas.width = 400; canvas.height = 300;
    var ctx = canvas.getContext('2d');
    ctx.fillStyle = 'rgba(0,0,0,0.03)';
    ctx.font = '20px serif';
    ctx.rotate(-30 * Math.PI / 180);
    // 水印内容：时间戳 + 随机ID（可追溯）
    var ts = new Date().toISOString().slice(0,16).replace(/[T:-]/g,'');
    var rid = Math.random().toString(36).slice(2,8).toUpperCase();
    var text = '数字见习生·' + ts + '·' + rid;
    ctx.fillText(text, 50, 150);
    var div = document.createElement('div');
    div.className = 'watermark';
    div.style.backgroundImage = 'url(' + canvas.toDataURL() + ')';
    div.style.backgroundSize = '400px 300px';
    document.body.appendChild(div);
  }
  // 禁止复制和右键
  function disableCopy() {
    document.addEventListener('contextmenu', function(e) { e.preventDefault(); });
    document.addEventListener('selectstart', function(e) { e.preventDefault(); });
    document.addEventListener('copy', function(e) { e.preventDefault(); });
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function() { addWatermark(); disableCopy(); });
  } else {
    addWatermark(); disableCopy();
  }
})();
"""
PAGE_TMPL = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · 《{book}》</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
<header class="topbar">
  <a href="index.html">《{book}》</a>
  <a href="toc.html">目录</a>
  <form action="search.html" method="get"><input id="q" type="search" name="q" placeholder="全书搜索…" autocomplete="off"></form>
</header>
<main class="book">
{body}
</main>
<nav class="pagenav">{prevnext}</nav>
<footer class="pagefoot">《{book}》· 作者 jeffery · {version} · 本页由书稿源文件自动生成（build_web_book.py）· {built}<br><small>版权所有 © jeffery · 未经授权禁止转载、复制或传播</small></footer>
<script src="search.js"></script>
<script src="antitheft.js"></script>
</body>
</html>
"""


def write(path, content):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)


def dir_size_mb(path):
    return sum(os.path.getsize(os.path.join(r, f))
               for r, _, fs in os.walk(path) for f in fs) / 1048576


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description='构建 GitHub Pages 电子书（docs/）')
    ap.add_argument('--serve', action='store_true', help='构建后启动本地预览')
    ap.add_argument('--port', type=int, default=8080, help='预览端口（默认 8080）')
    ap.add_argument('--no-images', action='store_true', help='跳过图片压缩（调试用）')
    args = ap.parse_args()

    if md_lib is None:
        sys.exit('缺少依赖：pip install markdown pillow')

    do_images = not args.no_images
    built = datetime.date.today().isoformat()
    version = VERSION

    outdir = DOCS_DIR
    img_dir = os.path.join(outdir, 'images')
    if os.path.isdir(outdir):
        shutil.rmtree(outdir)
    os.makedirs(img_dir if do_images else outdir, exist_ok=True)

    order = ab.BOOK_ORDER if ab is not None else DEFAULT_ORDER
    log('== 构建《%s》网页版电子书 → %s' % (BOOK_TITLE, outdir))
    groups = make_groups(order, img_dir, do_images)

    def page(title, body, prevnext=''):
        return PAGE_TMPL.format(title=title, book=BOOK_TITLE, version=version,
                                 built=built, body=body, prevnext=prevnext)

    # 各页面 + 上一页/下一页
    for i, g in enumerate(groups):
        prev = ''
        nxt = ''
        if i > 0:
            prev = '<a href="%s">← %s</a>' % (groups[i-1]['file'], groups[i-1]['title'])
        else:
            prev = '<span></span>'
        if i < len(groups) - 1:
            nxt = '<a href="%s">%s →</a>' % (groups[i+1]['file'], groups[i+1]['title'])
        else:
            nxt = '<span></span>'
        write(os.path.join(outdir, g['file']),
              page(g['title'], group_body(g), prev + nxt))
        log('  ✓ %-10s ← %d 个源文件' % (g['file'], len(g['entries'])))

    # toc.html
    items = []
    for g in groups:
        subs = ''.join('<li><a href="%s#%s">%s</a></li>'
                       % (g['file'], e['anchor'], e['title']) for e in g['entries'])
        items.append('<li><a href="%s">%s</a><ul>%s</ul></li>'
                     % (g['file'], g['title'], subs))
    write(os.path.join(outdir, 'toc.html'),
          page('目录', '<h1>目　录</h1><ul class="toclist">' + ''.join(items) + '</ul>'))

    # index.html：单页全书（无下载入口）
    single = '\n'.join(group_body(g) for g in groups)
    home = ('<h1>《%s》</h1><p>%s</p>'
            '<p>作者：jeffery · 联系：<a href="mailto:jeffery9@gmail.com">jeffery9@gmail.com</a></p>'
            '<p>版本 %s · 构建于 %s · <a href="toc.html">分篇阅读（推荐）</a>'
            ' · <a href="search.html">全书搜索</a></p>'
            '<p><strong>商务合作</strong>：欢迎 AI Agent 厂商、高校教师、培训机构联系合作</p>'
            '<div style="background:#fff3cd;border:2px solid #ffc107;padding:1rem;margin:1rem 0;border-radius:8px;">'
            '<strong>⚠ 版权声明</strong>：本书内容受版权保护，仅限个人学习使用。'
            '未经授权，禁止以任何形式复制、传播、转载或用于商业用途。'
            '违者将追究法律责任。商务合作请联系作者邮箱。</div>'
            '<hr>%s'
            % (BOOK_TITLE, BOOK_SUBTITLE, version, built, single))
    write(os.path.join(outdir, 'index.html'), page(BOOK_TITLE, home))

    # search.html + searchindex.js + search.js + style.css
    idx = []
    for g in groups:
        for e in g['entries']:
            idx.append({'file': g['file'], 'anchor': e['anchor'], 'title': e['title'],
                        'text': re.sub(r'\s+', ' ', e['plain'])[:150000]})
    write(os.path.join(outdir, 'searchindex.js'),
          'window.WEBBOOK_INDEX = ' + json.dumps(idx, ensure_ascii=False) + ';')
    write(os.path.join(outdir, 'search.js'), JS_SEARCH)
    write(os.path.join(outdir, 'antitheft.js'), JS_ANTITHEFT)
    write(os.path.join(outdir, 'style.css'), CSS)
    write(os.path.join(outdir, 'search.html'), page('全书搜索',
          '<h1>全书搜索</h1><div class="searchbox"><p>输入关键词即时检索全书正文（纯前端实现）。</p>'
          '<div id="results"></div></div>'))

    # GitHub Pages 配置
    write(os.path.join(outdir, '_config.yml'),
          'theme: null\nplugins: []\ntitle: 《数字见习生》\n'
          'description: 写给无编程经验读者的 AI Agent 入门书\n')
    write(os.path.join(DOCS_DIR, '..', '.nojekyll'), '')
    write(os.path.join(outdir, 'README.md'),
          '# 《%s》网页版电子书\n\n%s。\n\n'
          '- 首页（单页全书）：index.html\n- 分篇目录：toc.html\n- 全书搜索：search.html\n\n'
          '本目录由 `book/build_web_book.py` 自动生成，请勿手工编辑；'
          '修改书稿后重新运行构建脚本并提交。\n' % (BOOK_TITLE, BOOK_SUBTITLE))

    size_mb = dir_size_mb(outdir)
    log('== 完成：%d 个页面｜压缩图片 %d 张｜docs/ 合计 %.1f MB'
        % (len(groups), len(_img_cache), size_mb))
    if size_mb > 90:
        log('  ! 提醒：docs/ 超过 90MB，接近 GitHub 仓库软限制，建议下调图片参数。')

    if args.serve:
        serve(outdir, args.port)


def serve(outdir, port):
    os.chdir(outdir)
    httpd = socketserver.TCPServer(('', port), http.server.SimpleHTTPRequestHandler)
    url = 'http://localhost:%d/' % port
    log('== 本地预览：%s （Ctrl+C 退出）' % url)
    threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
    httpd.serve_forever()


if __name__ == '__main__':
    main()
