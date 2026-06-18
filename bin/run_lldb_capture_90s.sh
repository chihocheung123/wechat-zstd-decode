#!/bin/bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE="${WECHAT_ZSTD_WORKSPACE:-$REPO_ROOT/data}"
LLDB_DIR="${REPO_ROOT}/lldb"
mkdir -p "$WORKSPACE"
cd "$WORKSPACE"

# Quick batch syntax check (no attach): import module + register wcdb_capture_run
if [[ "${LLDB_CAPTURE_SYNTAX_ONLY:-}" == "1" ]]; then
  lldb -b \
    -o "command script import \"${LLDB_DIR}/lldb_capture_setup.py\"" \
    -o 'help wcdb_capture_run' \
    -o quit
  echo "SYNTAX_OK: lldb_capture_setup imported, wcdb_capture_run registered"
  exit 0
fi

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
echo "Batch LLDB: lldb -b -p ${PID} -s ${LLDB_DIR}/lldb_capture_wcdb.lldb"
echo "Manual LLDB: lldb -p ${PID} -s ${LLDB_DIR}/lldb_manual_dict_resolve.lldb"
printf '' > lldb_capture_hits.log
printf '' > lldb_capture_hit_count.txt

export BATCH_CAPTURE_SECONDS=90
export BATCH_CAPTURE_DETACH=1
export BATCH_CAPTURE_QUIT=1
export WECHAT_ZSTD_WORKSPACE="$WORKSPACE"

LLDB_SCRIPT="${LLDB_DIR}/lldb_capture_wcdb.lldb"
if [[ "${LLDB_CAPTURE_AGGRESSIVE:-}" == "1" ]]; then
  export LLDB_CAPTURE_SYMBOL_HUNT="${LLDB_CAPTURE_SYMBOL_HUNT:-1}"
  LLDB_SCRIPT="${LLDB_DIR}/lldb_capture_aggressive.lldb"
fi

echo "=== SCROLL compressed chat messages during the next 90 seconds ==="
echo "=== Quit other lldb sessions first (only one debugger per process) ==="
echo "=== Using ${LLDB_SCRIPT} ==="
lldb -b -p "${PID}" -s "${LLDB_SCRIPT}" 2>&1 | tee lldb_capture_run.log

python3 "${REPO_ROOT}/scripts/test_real_dict_5.py" || true

echo "--- lldb_capture_summary.txt ---"
if [[ -f lldb_capture_summary.txt ]]; then cat lldb_capture_summary.txt; else echo "(missing)"; fi
echo "--- lldb_capture_hit_count.txt ---"
if [[ -f lldb_capture_hit_count.txt ]]; then cat lldb_capture_hit_count.txt; else echo "0"; fi
HIT_LINES=0
if [[ -f lldb_capture_hits.log ]]; then
  HIT_LINES=$(grep -cE ' HIT bp=' lldb_capture_hits.log 2>/dev/null) || HIT_LINES=0
fi
echo "HIT lines in lldb_capture_hits.log: ${HIT_LINES}"
echo "Done. Check lldb_capture_hits.log for CONTINUE_START/END and HIT lines."
