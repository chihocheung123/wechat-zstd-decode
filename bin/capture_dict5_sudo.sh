#!/bin/bash
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE="${WECHAT_ZSTD_WORKSPACE:-$REPO_ROOT/data}"
mkdir -p "$WORKSPACE"
# dict_id=5 capture via sudo lldb attach (for App Store WeChat hardened runtime).
# Requires interactive Terminal: you will be prompted for your macOS login password.
set -euo pipefail

EXPORT="$WORKSPACE"
cd "$EXPORT"

# shellcheck source=_wechat_app_detect.sh
source "${EXPORT}/_wechat_app_detect.sh"

SCAN_MODULE="${EXPORT}/_migration_dict5_scan.py"
LOG="${EXPORT}/migration_capture_run.log"
SUMMARY="${EXPORT}/migration_capture_summary.txt"
APP_MODE="regular"

usage() {
  cat <<'EOF'
Usage: ./capture_dict5_sudo.sh [--app regular|debug|auto]

Runs the same 90s MAGIC5 memory scan as capture_dict5_migration.sh, but attaches
with sudo lldb (root task_for_pid). Use when regular attach fails with
"Not allowed to attach to process".

  --app regular  Require /Applications/WeChat.app (default; needed for 備份與遷移)
  --app debug    Require ~/Applications/WeChat-Debug.app
  --app auto     Prefer regular WeChat, else WeChat-Debug

You MUST run this in an interactive Terminal — sudo will ask for your password.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --app)
      APP_MODE="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

print_instructions() {
  cat <<BANNER

╔══════════════════════════════════════════════════════════════════════╗
║     微信 dict_id=5 擷取 — sudo LLDB attach（備份與遷移介面）        ║
╚══════════════════════════════════════════════════════════════════════╝

【重要】此腳本需要 sudo 權限
  • 接下來會要求輸入 macOS 登入密碼（輸入時畫面不會顯示字元，屬正常現象）
  • 請在本機 Terminal 互動執行，勿透過無密碼的自動化執行

【請在倒數結束後立刻操作 Mac 微信】

  1. 點左下角選單（三條線或頭像旁）
  2. 選「備份與遷移」(Backup and Migration)
  3. 進入「遷移」或「備份」介面，並保持開啟至 90 秒結束

提示：
  • 備份/遷移介面請用正式版 WeChat.app
  • 關閉其他已 attach 微信的 LLDB 視窗
  • 若 sudo attach 仍失敗，見 MIGRATION_CAPTURE_README.txt 疑難排解

掃描方式：每 4 秒掃描記憶體中的 ZSTD dict_id=5 特徵碼
  37 A4 30 EC 05 00 00 00
命中後自動傾印 112640 位元組 → real_dict_5_<時間>.bin 並驗證

BANNER
}

countdown() {
  local sec="${1:-5}"
  echo ""
  echo ">>> sudo attach ${WECHAT_APP_LABEL}，${sec} 秒後開始 90 秒擷取 <<<"
  echo ""
  for ((i=sec; i>=1; i--)); do
    printf "\r  倒數 %2d 秒 — 請準備點開「備份與遷移」…  " "$i"
    sleep 1
  done
  printf "\r  開始！請立刻開啟「備份與遷移」介面。                    \n\n"
}

if ! find_wechat_main_pid "$APP_MODE"; then
  echo "NO_PID: 請先啟動 WeChat（建議正式版 WeChat.app 以使用備份與遷移）" | tee -a "$LOG"
  echo "Installed:" >&2
  wechat_installed_summary >&2
  echo "Mode requested: --app ${APP_MODE}" >&2
  exit 1
fi

PID="$WECHAT_PID"

if [[ ! -f "$SCAN_MODULE" ]]; then
  echo "MISSING ${SCAN_MODULE}" | tee -a "$LOG"
  exit 1
fi

if ! sudo -n true 2>/dev/null; then
  echo ""
  echo "═══════════════════════════════════════════════════════════════"
  echo "  即將執行 sudo — 請在下方輸入您的 macOS 登入密碼"
  echo "  (Password prompt appears below; typing is hidden.)"
  echo "═══════════════════════════════════════════════════════════════"
  echo ""
fi

print_instructions
echo "${WECHAT_APP_LABEL} PID=${PID}"
echo "App path: ${WECHAT_APP_PATH}"
echo "輸出目錄: ${EXPORT}"
echo "Attach: sudo lldb (root task_for_pid)"
echo ""

countdown 5

printf '' > "$LOG"
{
  echo "=== migration capture (sudo) start $(date -Iseconds) app=${WECHAT_APP_LABEL} pid=${PID} path=${WECHAT_APP_PATH} ==="
  export MIGRATION_CAPTURE_APP_LABEL="${WECHAT_APP_LABEL}"
  export MIGRATION_CAPTURE_APP_PATH="${WECHAT_APP_PATH}"
  sudo lldb -b \
    -o 'settings set auto-confirm true' \
    -o "process attach --pid ${PID}" \
    -o "command script import \"${SCAN_MODULE}\"" \
    -o 'migration_capture_90s' \
    -o 'detach' \
    -o 'quit'
} 2>&1 | tee -a "$LOG"

echo ""
echo "=== 擷取結束，執行驗證 ==="

CAPTURED=0
if ls real_dict_5_*.bin >/dev/null 2>&1; then
  CAPTURED=$(ls -1 real_dict_5_*.bin 2>/dev/null | wc -l | tr -d ' ')
fi

VALID_RC=1
if [[ -f real_dict_5.bin ]] || [[ "$CAPTURED" -gt 0 ]]; then
  python3 "${EXPORT}/validate_dict5.py" || VALID_RC=$?
else
  echo "未產生 real_dict_5*.bin — 請在掃描期間開啟「備份與遷移」後重試"
fi

{
  echo "migration_capture_summary $(date -Iseconds)"
  echo "mode=sudo"
  echo "app=${WECHAT_APP_LABEL}"
  echo "app_path=${WECHAT_APP_PATH}"
  echo "pid=${PID}"
  echo "captured_files=${CAPTURED}"
  echo "validate_rc=${VALID_RC}"
  if [[ -f real_dict_5.bin ]]; then
    echo "symlink=real_dict_5.bin -> $(readlink real_dict_5.bin 2>/dev/null || echo '?')"
  fi
  if grep -q 'CAPTURE_OK' "$LOG" 2>/dev/null; then
    echo "lldb_hits=yes"
    grep 'CAPTURE_OK\|VALIDATE_OK\|MAGIC5_HIT' "$LOG" || true
  else
    echo "lldb_hits=no"
  fi
} | tee "$SUMMARY"

echo ""
echo "--- ${SUMMARY} ---"
cat "$SUMMARY"

if [[ "$VALID_RC" -eq 0 ]]; then
  echo ""
  echo "SUCCESS: real_dict_5.bin 已驗證，可用於 bulk_decode_messages.py"
  exit 0
fi

exit 1
