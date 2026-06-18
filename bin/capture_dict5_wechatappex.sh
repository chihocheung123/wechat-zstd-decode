#!/bin/bash
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE="${WECHAT_ZSTD_WORKSPACE:-$REPO_ROOT/data}"
mkdir -p "$WORKSPACE"
# Attach to WeChatAppEx helper process and scan for ZSTD dict_id=5.
#
# Background: Main WeChat breakpoints stayed at 0 hits. Decompression may run
# in WeChatAppEx which loads roam_migration independently. This script targets
# that helper directly.
#
# Usage:
#   ./bin/capture_dict5_wechatappex.sh [--diag] [--sudo]
#
#   --diag   Only print WeChatAppEx PID(s) and whether roam_migration is loaded; don't scan.
#   --sudo   Use sudo lldb attach. Required if normal attach says "Not allowed to attach to process".
set -euo pipefail

EXPORT="$WORKSPACE"
SCAN_MODULE="${REPO_ROOT}/scripts/_migration_dict5_scan_v6.py"
VALIDATE_SCRIPT="${REPO_ROOT}/scripts/validate_dict5.py"
LOG="${EXPORT}/wechatappex_capture.log"

export WECHAT_ZSTD_REPO="$REPO_ROOT"
export WECHAT_ZSTD_WORKSPACE="$WORKSPACE"
export WECHAT_ZSTD_VALIDATE_SCRIPT="$VALIDATE_SCRIPT"

# shellcheck source=_wechat_app_detect.sh
source "${REPO_ROOT}/bin/_wechat_app_detect.sh"

DIAG_ONLY=false
USE_SUDO=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --diag) DIAG_ONLY=true; shift ;;
    --sudo) USE_SUDO=true; shift ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [[ "$USE_SUDO" == "true" ]]; then
  export WECHAT_LLDB_SUDO=1
  if ! sudo -n true 2>/dev/null; then
    echo ""
    echo "═══════════════════════════════════════════════════════════════"
    echo "  即將執行 sudo — 請在下方輸入您的 macOS 登入密碼"
    echo "  (Password prompt appears below; typing is hidden.)"
    echo "═══════════════════════════════════════════════════════════════"
    echo ""
  fi
else
  export WECHAT_LLDB_SUDO=0
fi

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║          WeChat dict_id=5 — WeChatAppEx helper capture       ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# --- Discover WeChatAppEx PIDs ---
if ! find_wechatappex_pids; then
  echo "NO_PID: WeChatAppEx not running."
  echo ""
  echo "WeChat must be open and have processed at least one message."
  echo "WeChatAppEx usually starts when WeChat renders message content."
  echo ""
  echo "Tip: open a chat with many image/media messages, then retry."
  exit 1
fi

echo "Found ${WECHATAPPEX_COUNT} WeChatAppEx process(es): ${WECHATAPPEX_PIDS}"
if [[ "$USE_SUDO" == "true" ]]; then
  echo "Attach mode: sudo lldb"
else
  echo "Attach mode: lldb"
fi
echo ""

# --- Check roam_migration presence in each ---
TARGET_PID=""
ATTACH_DENIED=false
for pid in $WECHATAPPEX_PIDS; do
  echo -n "PID ${pid}: checking for roam_migration... "
  result=""
  if result="$(wechatappex_has_roam_migration "$pid" 2>&1)"; then
    echo "FOUND"
    echo "  ${result}"
    TARGET_PID="$pid"
    break
  else
    rc=$?
    if [[ "$rc" -eq 2 ]]; then
      ATTACH_DENIED=true
      echo "attach denied"
      echo "  ${result}"
      if [[ "$USE_SUDO" != "true" ]]; then
        echo ""
        echo "This process denied normal LLDB attach. Re-run:"
        echo "  ./bin/capture_dict5_wechatappex.sh --sudo --diag"
        echo "  ./bin/capture_dict5_wechatappex.sh --sudo"
        exit 2
      fi
    else
      echo "not found (may still load it later)"
    fi
  fi
done

if [[ "$ATTACH_DENIED" == "true" ]] && [[ "$USE_SUDO" == "true" ]]; then
  echo ""
  echo "sudo lldb was also denied by macOS task_for_pid / hardened runtime."
  echo "Stop retrying this App Store WeChatAppEx process; the scan never started."
  echo ""
  echo "Next viable paths:"
  echo "  1. Use WeChat-Resigned.app with get-task-allow:"
  echo "     ./bin/capture_dict5_resigned.sh"
  echo "  2. Capture on an iOS device during WeChat backup/export."
  echo "     See docs/IOS_DICT5_README.txt"
  exit 3
fi

if [[ "$DIAG_ONLY" == "true" ]]; then
  echo ""
  echo "Diagnostic complete (--diag mode, no scan performed)."
  echo "To run full capture: ./bin/capture_dict5_wechatappex.sh"
  exit 0
fi

if [[ -z "$TARGET_PID" ]]; then
  echo ""
  echo "WARN: roam_migration not found in any WeChatAppEx PID."
  echo "It may load only when migration/decompression is triggered."
  echo ""
  echo "Falling back to first PID: ${WECHATAPPEX_PIDS%% *}"
  TARGET_PID="${WECHATAPPEX_PIDS%% *}"
  echo "This may still capture dict_id=5 if it loads during the 90s window."
fi

if [[ ! -f "$SCAN_MODULE" ]]; then
  echo "MISSING ${SCAN_MODULE}" | tee -a "$LOG"
  exit 1
fi

echo ""
echo "Target PID: ${TARGET_PID}"
echo "Output dir: ${EXPORT}"
if [[ "$USE_SUDO" == "true" ]]; then
  echo "Attach: sudo lldb"
else
  echo "Attach: lldb"
fi
echo ""
echo ">>> Starting 90-second dict_id=5 scan on WeChatAppEx PID ${TARGET_PID} <<<"
echo ""
echo "While scanning, trigger decompression in WeChat:"
echo "  1. Open a chat with iOS-exported messages (CT=2)"
echo "  2. Scroll through compressed messages"
echo "  3. OR open 備份與遷移 → start migration"
echo ""

printf '' > "$LOG"
set +e
{
  echo "=== wechatappex capture start $(date -Iseconds) pid=${TARGET_PID} ==="
  export MIGRATION_CAPTURE_APP_LABEL="WeChatAppEx"
  export MIGRATION_CAPTURE_APP_PATH="WeChatAppEx"
  if [[ "$USE_SUDO" == "true" ]]; then
    sudo lldb -b \
      -o 'settings set auto-confirm true' \
      -o "process attach --pid ${TARGET_PID}" \
      -o "command script import \"${SCAN_MODULE}\"" \
      -o 'migration_capture_90s_v6' \
      -o 'detach' \
      -o 'quit'
  else
    lldb -b \
      -o 'settings set auto-confirm true' \
      -o "process attach --pid ${TARGET_PID}" \
      -o "command script import \"${SCAN_MODULE}\"" \
      -o 'migration_capture_90s_v6' \
      -o 'detach' \
      -o 'quit'
  fi
  echo "=== wechatappex capture end $(date -Iseconds) ==="
} 2>&1 | tee -a "$LOG"
CAPTURE_RC=${PIPESTATUS[0]}
set -e

if [[ "$CAPTURE_RC" -ne 0 ]]; then
  echo ""
  echo "Capture command failed with rc=${CAPTURE_RC}."
  if grep -qi 'not allowed to attach\|attach failed' "$LOG" 2>/dev/null && [[ "$USE_SUDO" != "true" ]]; then
    echo "Normal attach was denied. Re-run:"
    echo "  ./bin/capture_dict5_wechatappex.sh --sudo"
  fi
  exit "$CAPTURE_RC"
fi

echo ""
echo "=== Capture complete. Running validation... ==="

CAPTURED=0
cd "$EXPORT"
if ls real_dict_5_*.bin >/dev/null 2>&1; then
  CAPTURED=$(ls -1 real_dict_5_*.bin 2>/dev/null | wc -l | tr -d ' ')
fi

VALID_RC=1
if [[ "$CAPTURED" -gt 0 ]] || [[ -f real_dict_5.bin ]]; then
  python3 "$VALIDATE_SCRIPT" "${EXPORT}/real_dict_5.bin" 2>&1 | tee -a "$LOG" || VALID_RC=$?
else
  echo "No real_dict_5*.bin produced."
  echo "If roam_migration was not loaded, retry after opening a CT=2 chat."
fi

echo ""
if grep -q 'MAGIC5_HIT\|CAPTURE_OK' "$LOG" 2>/dev/null; then
  echo "MAGIC5_HIT detected in log — dict_id=5 may have been captured."
else
  echo "MAGIC5 hits: 0 — dict not loaded in WeChatAppEx during scan window."
  echo "Next steps:"
  echo "  Option A: attach to main WeChat PID during active migration UI"
  echo "           ./bin/capture_dict5_migration.sh --app regular"
  echo "  Option B: scroll CT=2 compressed messages in WeChat and retry this script"
fi

[[ "$VALID_RC" -eq 0 ]] && exit 0
exit 1
