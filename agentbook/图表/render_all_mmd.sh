#!/usr/bin/env bash
# render_all_mmd.sh — 批量渲染 图表/*.mmd → 同名 .svg（mermaid-cli）
# 用法：bash 图表/render_all_mmd.sh [mmd文件...]（缺省渲染全部）
# 约定（QWEN.md §九）：.mmd 是语义真相，SVG 是派生产物；改图先改 .mmd 再重跑本脚本。
# 依赖：npm 安装 @mermaid-js/mermaid-cli（node_modules/.bin/mmdc）；
#       Chrome 路径自动探测（PUPPETEER_EXECUTABLE_PATH 可覆盖）。
# 防方框（QWEN.md §9.3 根因②③）：字体回退链写入 puppeteer 配置（本机中文字体
#       置于链首），保证 Chrome 渲染 .mmd 时中文落在真实字形上；
#       htmlLabels:false 仅对流程图经 mmd_square.py 注入的 %%{init}%% 生效
#       （出原生 SVG <text>，避免构建端转换丢宽度导致重叠）；序列图不设该项，
#       否则 mermaid 序列图原生气泡测量不认中文字体，中文变方框、文字重叠。
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"

# --- 探测 Chrome（优先已安装版本，避免硬编码路径失效导致渲染静默全败）---
if [ -z "${PUPPETEER_EXECUTABLE_PATH:-}" ]; then
  CHROME=$(ls /root/.cache/puppeteer/chrome/*/chrome-linux64/chrome 2>/dev/null | sort -V | tail -1)
  [ -n "$CHROME" ] || CHROME=$(command -v chromium || command -v google-chrome || command -v chrome)
  export PUPPETEER_EXECUTABLE_PATH="$CHROME"
fi
[ -x "$PUPPETEER_EXECUTABLE_PATH" ] || { echo "!! 找不到 Chrome，无法渲染（设 PUPPETEER_EXECUTABLE_PATH 指定）"; exit 1; }

# --- puppeteer 配置：无沙箱 + CJK 字体回退（本机真实中文字体在链首）---
PUPPETEER_CONFIG="${PUPPETEER_CONFIG:-/tmp/pptr.json}"
ZH_FIRST=$(fc-list ':lang=zh' family 2>/dev/null | grep -o 'Noto Sans CJK SC\|WenQuanYi Zen Hei' | sort -u | head -1)
[ -n "$ZH_FIRST" ] || ZH_FIRST="sans-serif"
printf '{"executablePath":"%s","args":["--no-sandbox","--disable-gpu","--disable-dev-shm-usage"],"font_family_list_popup_enabled":false,"default_font_family":"%s","font_family_fallbacks":["%s","Noto Sans CJK SC","WenQuanYi Zen Hei","sans-serif"],"standard_fontfamily":"%s","serif_fontfamily":"%s","sansserif_fontfamily":"%s","cursive_fontfamily":"%s","fantasy_fontfamily":"%s","monospace_fontfamily":"DejaVu Sans"}' \
  "$PUPPETEER_EXECUTABLE_PATH" "$ZH_FIRST" "$ZH_FIRST" "$ZH_FIRST" "$ZH_FIRST" "$ZH_FIRST" "$ZH_FIRST" "$ZH_FIRST" > "$PUPPETEER_CONFIG"

MMDC="npx --yes mmdc"
[ -x "$DIR/../node_modules/.bin/mmdc" ] && MMDC="$DIR/../node_modules/.bin/mmdc"

mapfile -t FILES < <(if [ $# -gt 0 ]; then printf '%s\n' "$@"; else ls "$DIR"/*.mmd; fi)
ok=0; fail=0
for f in "${FILES[@]}"; do
  out="${f%.mmd}.svg"
  if $MMDC -i "$f" -o "$out" -p "$PUPPETEER_CONFIG" -b white >/dev/null 2>&1; then
    echo "OK   $(basename "$out")"; ok=$((ok+1))
  else
    echo "FAIL $(basename "$f")"; fail=$((fail+1))
  fi
done
echo "── 渲染完成：成功 $ok / 失败 $fail ──"
[ "$fail" -eq 0 ]
