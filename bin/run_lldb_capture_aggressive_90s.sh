#!/bin/bash
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE="${WECHAT_ZSTD_WORKSPACE:-$REPO_ROOT/data}"
mkdir -p "$WORKSPACE"
set -euo pipefail
cd "$WORKSPACE"

export LLDB_CAPTURE_AGGRESSIVE=1
export LLDB_CAPTURE_SYMBOL_HUNT=1

PID=""
while IFS= read -r line; do
  pid="${line%% *}"
  cmd="${line#* }"
  case "$cmd" in
    */WeChat-Debug.app/Contents/MacOS/WeChat) ;;
    *) continue ;;
  esac
  case "$cmd" in
    *WeChatAppEx*|*crashpad*) continue ;;
  esac
  PID="$pid"
  break
done < <(pgrep -lf 'WeChat-Debug.app/Contents/MacOS/WeChat' 2>/dev/null || true)

if [[ -z "${PID}" ]]; then
  echo "NO_PID: start WeChat-Debug.app first" | tee lldb_capture_attach_denied.txt
  exit 1
fi

echo "Main WeChat-Debug PID=${PID}"
echo "Aggressive batch: LLDB_CAPTURE_AGGRESSIVE=1 lldb -b -p ${PID} -s lldb_capture_aggressive.lldb"
printf '' > lldb_capture_hits.log
printf '' > lldb_capture_hit_count.txt

export BATCH_CAPTURE_SECONDS=90
export BATCH_CAPTURE_DETACH=1
export BATCH_CAPTURE_QUIT=1

echo "=== AGGRESSIVE 90s — scroll 米迷, open quotes, expand, search haha ==="
echo "=== Quit other lldb sessions first ==="
lldb -b -p "${PID}" -s lldb_capture_aggressive.lldb 2>&1 | tee lldb_capture_run.log

python3 test_real_dict_5.py || true

echo "--- lldb_capture_summary.txt ---"
if [[ -f lldb_capture_summary.txt ]]; then cat lldb_capture_summary.txt; else echo "(missing)"; fi
HIT_LINES=0
if [[ -f lldb_capture_hits.log ]]; then
  HIT_LINES=$(grep -cE ' HIT bp=' lldb_capture_hits.log 2>/dev/null) || HIT_LINES=0
fi
echo "HIT lines in lldb_capture_hits.log: ${HIT_LINES}"
echo "Done. Check lldb_capture_hits.log for SYMBOL_HUNT / AGGRESSIVE_OFFSETS / HIT lines."
