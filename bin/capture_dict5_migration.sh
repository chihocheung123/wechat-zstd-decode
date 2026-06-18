#!/bin/bash
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE="${WECHAT_ZSTD_WORKSPACE:-$REPO_ROOT/data}"
mkdir -p "$WORKSPACE"
# One-command dict_id=5 capture while user opens 備份與遷移 UI.
# Supports regular WeChat (preferred for backup UI) and WeChat-Debug.
set -euo pipefail

EXPORT="$WORKSPACE"
VALIDATE_SCRIPT="${REPO_ROOT}/scripts/validate_dict5.py"
SCAN_MODULE="${REPO_ROOT}/scripts/_migration_dict5_scan_v6.py"
export WECHAT_ZSTD_REPO="$REPO_ROOT"
export WECHAT_ZSTD_WORKSPACE="$WORKSPACE"
export WECHAT_ZSTD_VALIDATE_SCRIPT="$VALIDATE_SCRIPT"
cd "$EXPORT"

# shellcheck source=_wechat_app_detect.sh
source "${REPO_ROOT}/bin/_wechat_app_detect.sh"

LOG="${EXPORT}/migration_capture_run.log"
SUMMARY="${EXPORT}/migration_capture_summary.txt"
APP_MODE="auto"

usage() {
  cat <<'EOF'
Usage: ./capture_dict5_migration.sh [--app auto|regular|debug]

  --app auto     Prefer running regular WeChat, else WeChat-Debug (default)
  --app regular  Require /Applications/WeChat.app (use for 備份與遷移)
  --app debug    Require ~/Applications/WeChat-Debug.app

Quit the other WeChat variant first — both share com.tencent.xinWeChat.
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
║          微信 dict_id=5 字典擷取 — 備份與遷移介面觸發               ║
╚══════════════════════════════════════════════════════════════════════╝

【請在倒數結束後立刻操作 Mac 微信】

  1. 點左下角選單（三條線或頭像旁）
  2. 選「備份與遷移」(Backup and Migration)
  3. 進入「遷移」或「備份」介面，並保持開啟至 90 秒結束

提示：
  • 備份/遷移介面請用正式版 WeChat.app（WeChat-Debug 可能無法開啟備份）
  • 可先開啟 iOS→Mac 遷移預覽（若可用）
  • 關閉其他已 attach 微信的 LLDB 視窗（同一程序只能有一個偵錯器）
  • 倒數期間請把微信視窗準備好，一聽到「開始」就點進遷移介面

掃描方式：每 4 秒掃描記憶體中的 ZSTD dict_id=5 特徵碼
  37 A4 30 EC 05 00 00 00
命中後自動傾印 112640 位元組 → real_dict_5_<時間>.bin 並驗證

BANNER
}

countdown() {
  local sec="${1:-5}"
  echo ""
  echo ">>> 準備 attach ${WECHAT_APP_LABEL}，${sec} 秒後開始 90 秒擷取 <<<"
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

print_instructions
echo "${WECHAT_APP_LABEL} PID=${PID}"
echo "App path: ${WECHAT_APP_PATH}"
echo "輸出目錄: ${EXPORT}"
echo ""

countdown 5

printf '' > "$LOG"
{
  echo "=== migration capture start $(date -Iseconds) app=${WECHAT_APP_LABEL} pid=${PID} path=${WECHAT_APP_PATH} ==="
  export MIGRATION_CAPTURE_APP_LABEL="${WECHAT_APP_LABEL}"
  export MIGRATION_CAPTURE_APP_PATH="${WECHAT_APP_PATH}"
  lldb -b \
    -o 'settings set auto-confirm true' \
    -o "process attach --pid ${PID}" \
    -o "command script import \"${SCAN_MODULE}\"" \
    -o 'migration_capture_90s_v6' \
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
  python3 "$VALIDATE_SCRIPT" "${EXPORT}/real_dict_5.bin" || VALID_RC=$?
else
  echo "未產生 real_dict_5*.bin — 請在掃描期間開啟「備份與遷移」後重試"
fi

{
  echo "migration_capture_summary $(date -Iseconds)"
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
