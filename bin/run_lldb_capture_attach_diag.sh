#!/bin/bash
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE="${WECHAT_ZSTD_WORKSPACE:-$REPO_ROOT/data}"
mkdir -p "$WORKSPACE"
# Quick attach diagnostics (no 90s wait). Requires WeChat-Debug running and NOT already lldb-attached.
set -euo pipefail
cd "$WORKSPACE"
PID=""
while IFS= read -r line; do
  pid="${line%% *}"
  cmd="${line#* }"
  case "$cmd" in */WeChat-Debug.app/Contents/MacOS/WeChat) ;; *) continue ;; esac
  case "$cmd" in *WeChatAppEx*|*crashpad*) continue ;; esac
  PID="$pid"; break
done < <(pgrep -lf 'WeChat-Debug.app/Contents/MacOS/WeChat' 2>/dev/null || true)
[[ -n "${PID}" ]] || { echo "NO_PID"; exit 1; }
OUT="lldb_attach_diag.txt"
export LLDB_CAPTURE_VERIFY=1
{
  echo "=== main pid ${PID} ==="
  lldb -b -p "${PID}" -o 'command script import lldb_capture_setup.py' -o 'wcdb_capture_run' \
    -o 'image list -o -f roam_migration' \
    -o 'image list -o -f' \
    -o 'breakpoint list'
  echo ""
  echo "=== WeChatAppEx PIDs (roam_migration grep) ==="
  for p in $(pgrep -f 'WeChatAppEx.app/Contents/MacOS/WeChatAppEx' | head -3); do
    echo "--- AppEx pid $p ---"
    lldb -b -p "$p" -o 'image list -o -f roam_migration' 2>&1 | head -20 || true
  done
} 2>&1 | tee "$OUT"
echo "Wrote $OUT"
