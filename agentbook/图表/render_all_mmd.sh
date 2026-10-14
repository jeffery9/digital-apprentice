#!/usr/bin/env bash
# render_all_mmd.sh — 批量渲染 图表/*.mmd → 同名 .svg（mermaid-cli）
# 用法：bash 图表/render_all_mmd.sh [mmd文件...]（缺省渲染全部）
# 约定（QWEN.md §九）：.mmd 是语义真相，SVG 是派生产物；改图先改 .mmd 再重跑本脚本。
# 依赖：npm 安装 @mermaid-js/mermaid-cli；Chrome 经 PUPPETEER_EXECUTABLE_PATH 指定。
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
export PUPPETEER_EXECUTABLE_PATH="${PUPPETEER_EXECUTABLE_PATH:-/root/.cache/puppeteer/chrome/linux-155.0.8059.79/chrome-linux64/chrome}"
PUPPETEER_CONFIG="${PUPPETEER_CONFIG:-/tmp/pptr.json}"
[ -f "$PUPPETEER_CONFIG" ] || printf '{"args":["--no-sandbox","--disable-gpu","--disable-dev-shm-usage"]}' > "$PUPPETEER_CONFIG"
mapfile -t FILES < <(if [ $# -gt 0 ]; then printf '%s\n' "$@"; else ls "$DIR"/*.mmd; fi)
ok=0; fail=0
for f in "${FILES[@]}"; do
  out="${f%.mmd}.svg"
  if npx --yes mmdc -i "$f" -o "$out" -p "$PUPPETEER_CONFIG" -b white >/dev/null 2>&1; then
    echo "OK   $(basename "$out")"; ok=$((ok+1))
  else
    echo "FAIL $(basename "$f")"; fail=$((fail+1))
  fi
done
echo "── 渲染完成：成功 $ok / 失败 $fail ──"
[ "$fail" -eq 0 ]
