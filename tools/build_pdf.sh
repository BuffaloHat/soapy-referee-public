#!/usr/bin/env bash
# Build a PDF from one of SREF's Markdown docs (the raglab pipeline).
#   pandoc (markdown -> self-contained HTML, images inlined) -> Chrome headless --print-to-pdf
# No PDF engine needed; Chrome renders the CSS and page breaks faithfully.
#
#   tools/build_pdf.sh docs/sref_eval_report.md docs/sref_eval_report.pdf
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/${1:?markdown source}"
OUT="$ROOT/${2:?pdf output}"
CSS="$ROOT/docs/report/report.css"
HTML="$(dirname "$SRC")/.pdf_build.html"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

pandoc "$SRC" \
  -f gfm+raw_html \
  -t html5 \
  --standalone \
  --embed-resources \
  --resource-path="$ROOT/docs:$ROOT" \
  --metadata pagetitle="$(basename "$SRC" .md)" \
  --css "$CSS" \
  -o "$HTML"

"$CHROME" --headless --disable-gpu --no-pdf-header-footer \
  --run-all-compositor-stages-before-draw --virtual-time-budget=10000 \
  --print-to-pdf="$OUT" "file://$HTML" 2>/dev/null

rm -f "$HTML"
echo "Wrote ${OUT#$ROOT/}"
