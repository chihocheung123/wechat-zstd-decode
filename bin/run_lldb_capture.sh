#!/bin/bash
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE="${WECHAT_ZSTD_WORKSPACE:-$REPO_ROOT/data}"
mkdir -p "$WORKSPACE"
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
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
if [ -z "$PID" ]; then
  echo "Start WeChat-Debug first"
  exit 1
fi
echo "Attaching to main WeChat PID=$PID"
exec lldb -p "$PID" -s "$DIR/lldb_capture_wcdb.lldb" "$@"
