#!/bin/bash
# RUN_FULL_RESIGN_CAPTURE.sh — One-shot: resign full WeChat bundle + launch + capture.
#
# Run this from an interactive Terminal while WeChat is QUIT.
# After the countdown you will be asked to log in to WeChat and start 備份與遷移.
#
# Usage:
#   cd /Users/patrickchiho/Documents/Code/wechat-zstd-decode
#   export WECHAT_ZSTD_WORKSPACE="$PWD/data"
#   ./bin/RUN_FULL_RESIGN_CAPTURE.sh

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE="${WECHAT_ZSTD_WORKSPACE:-$REPO_ROOT/data}"
mkdir -p "$WORKSPACE"

echo ""
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║         WeChat dict_id=5 — Full bundle resign + capture          ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""
echo "Step 0: diagnostic — which WeChat processes are currently attachable?"
echo ""
"${REPO_ROOT}/bin/find_attachable_wechat.sh" || true
echo ""

echo "Step 1: Resign full WeChat bundle (creates WeChat-Resigned-Full.app in data/)"
echo "        This will take ~30s for the copy + codesign loop."
echo ""
"${REPO_ROOT}/bin/resign_wechat_full.sh" --out-app "${WORKSPACE}/WeChat-Resigned-Full.app"

echo ""
echo "Step 2: Launching WeChat-Resigned-Full.app ..."
echo "        → When it opens: log in, then open 備份與遷移 → start migration."
echo ""
open "${WORKSPACE}/WeChat-Resigned-Full.app"

echo ""
echo "═══════════════════════════════════════════════════════════════════"
echo "  ACTION REQUIRED:"
echo "  1. Log in to WeChat (if not already)"
echo "  2. Click  左下角選單 → 備份與遷移"
echo "  3. Click  開始遷移 or 開始備份  (just opening the panel is NOT enough)"
echo ""
echo "  Waiting 30 seconds for you to navigate to the migration screen ..."
echo "  (use Ctrl-C to skip the wait if you're already there)"
echo "═══════════════════════════════════════════════════════════════════"
echo ""
sleep 30

echo ""
echo "Step 3: Starting 90-second dict_id=5 scan on WeChatAppEx ..."
echo "        (keep migration running during the full 90s)"
echo ""
"${REPO_ROOT}/bin/capture_dict5_appex_resigned.sh" --wait 30

echo ""
echo "═══════════════════════════════════════════════════════════════════"
if [[ -f "${WORKSPACE}/real_dict_5.bin" ]]; then
  echo "  🎉 real_dict_5.bin FOUND — running validation ..."
  python3 "${REPO_ROOT}/scripts/validate_dict5.py" "${WORKSPACE}/real_dict_5.bin"
else
  echo "  No real_dict_5.bin produced this run."
  echo "  Check: ${WORKSPACE}/appex_resigned_capture.log"
  echo "  If MAGIC5 hits = 0: try iOS device capture (docs/IOS_DICT5_README.txt)"
fi
echo "═══════════════════════════════════════════════════════════════════"
