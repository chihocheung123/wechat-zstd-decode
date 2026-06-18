#!/bin/bash
# capture_dict5_appex_resigned.sh — Attach LLDB to resigned WeChatAppEx and scan for dict_id=5.
#
# Background:
#   App Store WeChatAppEx has hardened runtime — even sudo lldb is denied task_for_pid.
#   This script targets a resigned copy (get-task-allow) created by resign_wechatappex.sh,
#   OR a WeChatAppEx process spawned by WeChat-Resigned.app if it happens to be attachable.
#
# Prerequisites:
#   Run ./bin/resign_wechatappex.sh first to produce WeChatAppEx-Resigned.app in $WORKSPACE.
#   Launch WeChat-Resigned.app and open 備份與遷移 → start migration before running this.
#
# Usage:
#   ./bin/capture_dict5_appex_resigned.sh [--wait <sec>] [--pid <pid>]
#
#   --wait <sec>   Seconds to wait for WeChatAppEx process before giving up (default: 60)
#   --pid <pid>    Skip discovery, attach directly to this PID

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE="${WECHAT_ZSTD_WORKSPACE:-$REPO_ROOT/data}"
mkdir -p "$WORKSPACE"

SCAN_MODULE="${REPO_ROOT}/scripts/_migration_dict5_scan_v6.py"
VALIDATE_SCRIPT="${REPO_ROOT}/scripts/validate_dict5.py"
LOG="${WORKSPACE}/appex_resigned_capture.log"
WAIT_SEC=60
FORCE_PID=""

export WECHAT_ZSTD_REPO="$REPO_ROOT"
export WECHAT_ZSTD_WORKSPACE="$WORKSPACE"
export WECHAT_ZSTD_VALIDATE_SCRIPT="$VALIDATE_SCRIPT"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --wait) WAIT_SEC="$2"; shift 2 ;;
    --pid)  FORCE_PID="$2"; shift 2 ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \?//'
      exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║    WeChat dict_id=5 — WeChatAppEx-Resigned capture (v6)      ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# --- Validate scan module ---
if [[ ! -f "$SCAN_MODULE" ]]; then
  echo "MISSING: $SCAN_MODULE" >&2
  exit 1
fi

# --- Discover target PID ---
# Non-intrusive check: inspect the binary's entitlements via codesign.
# Avoids lldb attach/detach which would briefly pause an active process.
_pid_binary() {
  # Returns the executable path for a given PID.
  # ps -p <pid> -o comm= gives the binary path on macOS.
  ps -p "$1" -o comm= 2>/dev/null | head -1 || true
}

_has_get_task_allow() {
  local pid="$1"
  local binary
  binary="$(_pid_binary "$pid")"
  [[ -z "$binary" ]] && return 1
  codesign -d --entitlements - "$binary" 2>/dev/null | grep -q 'get-task-allow'
}

find_appex_resigned_pid() {
  # NOTE: Strategy 1 (pgrep by workspace path) was removed.
  # When WeChat-Resigned.app spawns WeChatAppEx it uses the binary from inside
  # its own bundle (not from $WORKSPACE), so pgrep on the workspace path never
  # matches. Use --pid <pid> to force a specific resigned WeChatAppEx process.

  # Strategy: find any running WeChatAppEx with get-task-allow entitlement.
  # Uses codesign (non-intrusive) instead of a test lldb attach.
  local pids
  pids="$(pgrep -f 'WeChatAppEx.app/Contents/MacOS/WeChatAppEx' 2>/dev/null || \
          pgrep -x WeChatAppEx 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    while IFS= read -r pid; do
      [[ -z "$pid" ]] && continue
      if _has_get_task_allow "$pid"; then
        echo "$pid"
        return 0
      fi
    done <<< "$pids"
  fi
  return 1
}

TARGET_PID="$FORCE_PID"

if [[ -z "$TARGET_PID" ]]; then
  echo "Searching for attachable WeChatAppEx process ..."
  echo "(Wait up to ${WAIT_SEC}s for WeChat-Resigned to spawn WeChatAppEx)"
  echo ""

  ELAPSED=0
  SLEEP_INTERVAL=5
  while true; do
    if TARGET_PID="$(find_appex_resigned_pid 2>/dev/null)"; then
      echo "Found attachable WeChatAppEx PID: ${TARGET_PID}"
      break
    fi

    if [[ "$ELAPSED" -ge "$WAIT_SEC" ]]; then
      echo ""
      echo "NO_PID: No attachable WeChatAppEx found after ${WAIT_SEC}s."
      echo ""
      echo "Troubleshooting:"
      echo "  1. Ensure WeChat-Resigned.app is running (not App Store WeChat)."
      echo "  2. Run ./bin/resign_wechatappex.sh to resign the WeChatAppEx bundle,"
      echo "     then launch it manually alongside WeChat-Resigned.app."
      echo "  3. If WeChatAppEx is embedded inside WeChat-Resigned.app bundle,"
      echo "     you may need to resign the entire WeChat.app (not just the helper)."
      echo "     See docs/APPEX_RESIGNED_CAPTURE.txt"
      echo ""
      echo "Alternative: use --pid <pid> to force attach a specific PID."
      exit 1
    fi

    sleep "$SLEEP_INTERVAL"
    ELAPSED=$((ELAPSED + SLEEP_INTERVAL))
    echo "  ... still waiting (${ELAPSED}s / ${WAIT_SEC}s) ..."
  done
fi

echo ""
echo "Target PID: ${TARGET_PID}"
echo "Scan module: ${SCAN_MODULE}"
echo "Output dir: ${WORKSPACE}"
echo ""
echo ">>> Starting 90-second dict_id=5 scan on WeChatAppEx PID ${TARGET_PID} <<<"
echo ""
echo "While scanning, trigger decompression in WeChat-Resigned.app:"
echo "  1. Open 備份與遷移 → start migration OR"
echo "  2. Scroll through a chat with iOS-exported CT=2 messages"
echo ""

printf '' > "$LOG"
set +e
{
  echo "=== appex_resigned capture start $(date -Iseconds) pid=${TARGET_PID} ==="
  export MIGRATION_CAPTURE_APP_LABEL="WeChatAppEx-Resigned"
  export MIGRATION_CAPTURE_APP_PATH="WeChatAppEx"
  lldb -b \
    -o 'settings set auto-confirm true' \
    -o "process attach --pid ${TARGET_PID}" \
    -o "command script import \"${SCAN_MODULE}\"" \
    -o 'migration_capture_90s_v6' \
    -o 'detach' \
    -o 'quit'
  echo "=== appex_resigned capture end $(date -Iseconds) ==="
} 2>&1 | tee -a "$LOG"
CAPTURE_RC=${PIPESTATUS[0]}
set -e

if [[ "$CAPTURE_RC" -ne 0 ]]; then
  echo ""
  echo "Capture failed (rc=${CAPTURE_RC})."
  if grep -qi 'not allowed to attach\|attach failed' "$LOG" 2>/dev/null; then
    echo ""
    echo "LLDB attach was still denied — the WeChatAppEx binary is not resigned."
    echo ""
    echo "NEXT STEPS:"
    echo "  Option A (resign the helper separately):"
    echo "    ./bin/resign_wechatappex.sh"
    echo "    # Then launch WeChatAppEx-Resigned.app manually (double-click in Finder)"
    echo "    # Verify it's running: pgrep -lf WeChatAppEx"
    echo "    ./bin/capture_dict5_appex_resigned.sh"
    echo ""
    echo "  Option B (resign the full WeChat bundle including all helpers):"
    echo "    # See docs/APPEX_RESIGNED_CAPTURE.txt — Section: Full Bundle Resign"
    echo ""
    echo "  Option C (iOS device capture):"
    echo "    # See docs/IOS_DICT5_README.txt"
  fi
  exit "$CAPTURE_RC"
fi

echo ""
echo "=== Capture complete. Running validation ==="

cd "$WORKSPACE"
VALID_RC=0
if ls real_dict_5*.bin >/dev/null 2>&1; then
  if [[ -e "${WORKSPACE}/real_dict_5.bin" || -L "${WORKSPACE}/real_dict_5.bin" ]]; then
    DICT_PATH="${WORKSPACE}/real_dict_5.bin"
  else
    DICT_PATH="${WORKSPACE}/$(ls -t real_dict_5*.bin | head -1)"
  fi
  python3 "$VALIDATE_SCRIPT" "$DICT_PATH" 2>&1 | tee -a "$LOG" || VALID_RC=$?
else
  echo "No real_dict_5*.bin produced."
  VALID_RC=1
fi

echo ""
if grep -q 'MAGIC5_HIT\|CAPTURE_OK' "$LOG" 2>/dev/null; then
  echo "MAGIC5_HIT detected — dict_id=5 may have been captured."
else
  echo "MAGIC5 hits: 0 — dict not loaded in WeChatAppEx during scan window."
  echo ""
  echo "Next steps:"
  echo "  - Ensure migration was actively running (not just backup UI open)"
  echo "  - Try iOS device capture: docs/IOS_DICT5_README.txt"
fi

[[ "$VALID_RC" -eq 0 ]] && exit 0
exit 1
