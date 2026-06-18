#!/bin/bash
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE="${WECHAT_ZSTD_WORKSPACE:-$REPO_ROOT/data}"
mkdir -p "$WORKSPACE"
# LLDB attach capture for ad-hoc WeChat-Resigned.app (get-task-allow) — v6 roam + rw- heap scan.
set -euo pipefail

EXPORT="$WORKSPACE"
VALIDATE_SCRIPT="${REPO_ROOT}/scripts/validate_dict5.py"
RESIGNED_APP="${EXPORT}/WeChat-Resigned.app"
RESIGNED_BIN="${RESIGNED_APP}/Contents/MacOS/WeChat"
SCAN_MODULE="${REPO_ROOT}/scripts/_migration_dict5_scan_v6.py"
LOG="${EXPORT}/resigned_capture_run.log"
SUMMARY="${EXPORT}/resigned_capture_summary.txt"
WAIT_SECONDS="${WAIT_SECONDS:-300}"
# Process younger than this is likely still in backup UI after fresh launch.
SESSION_MAX_AGE_SEC="${SESSION_MAX_AGE_SEC:-180}"
CAPTURE_FORCE="${CAPTURE_FORCE:-0}"
# Scan-only by default; set MIGRATION_ENABLE_BP=1 to enable roam_migration breakpoints.
MIGRATION_ENABLE_BP="${MIGRATION_ENABLE_BP:-0}"
export MIGRATION_ENABLE_BP
export WECHAT_ZSTD_REPO="$REPO_ROOT"
export WECHAT_ZSTD_WORKSPACE="$WORKSPACE"
export WECHAT_ZSTD_VALIDATE_SCRIPT="$VALIDATE_SCRIPT"

cd "$EXPORT"

print_instructions() {
  cat <<'BANNER'

╔══════════════════════════════════════════════════════════════════════╗
║     微信 dict_id=5 — WeChat-Resigned.app（v6 roam + heap 擷取）      ║
╚══════════════════════════════════════════════════════════════════════╝

【擷取前請完成以下步驟】

  1. 完全退出當前運行的微信（選單 → 退出微信，或 Cmd+Q）
  2. 雙擊打開 WeChat-Resigned.app 並登入
     路徑："$WORKSPACE"/WeChat-Resigned.app
  3. 開啟「備份與遷移」介面
  4. **點擊開始備份/遷移**（被動停留在介面不會載入 dict_5）
  5. 在 90 秒掃描期間保持該流程進行中

v6 改進：
  Phase 1 — roam_migration __TEXT/__DATA（約 3–4MB，同 v5）
  Phase 2 — 全部 rw- 記憶體區（vmmap/LLDB，跳過 >50MB 與 __LINKEDIT）
64KB 分塊讀取、每 2MB 記錄進度；每 2 秒一輪記錄 ROUND=N phase1_magic5 + phase2_magic5。
預設掃描模式不呼叫 proc.Continue()（避免 SIGSTOP attach 後卡住）。
可選 breakpoint（MIGRATION_ENABLE_BP=1）；預設關閉。
若 v6 仍 magic5=0，dict_5 可能僅在 iOS 裝置匯出時載入 — 見 IOS_DICT5_README.txt。

BANNER
}

countdown() {
  local sec="${1:-5}"
  echo ""
  echo ">>> ${sec} 秒後開始 90 秒 LLDB 擷取（v6）<<<"
  echo ""
  for ((i=sec; i>=1; i--)); do
    printf "\r  倒數 %2d 秒 — 請確認已點擊「開始備份/遷移」…  " "$i"
    sleep 1
  done
  printf "\r  開始掃描！請保持備份/遷移流程進行中。                    \n\n"
}

find_resigned_pid() {
  local line pid cmd
  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    pid="${line%% *}"
    cmd="${line#* }"
    case "$cmd" in
      "$RESIGNED_BIN") ;;
      *) continue ;;
    esac
    case "$cmd" in
      *WeChatAppEx*|*crashpad*|*wxocr*|*wxplayer*|*wxutility*) continue ;;
    esac
    echo "$pid"
    return 0
  done < <(pgrep -lf "$RESIGNED_BIN" 2>/dev/null || true)
  return 1
}

process_age_seconds() {
  local pid="$1"
  local etime
  etime="$(ps -p "$pid" -o etime= 2>/dev/null | tr -d ' ')"
  [[ -n "$etime" ]] || return 1

  if [[ "$etime" =~ ^([0-9]+)-([0-9]+):([0-9]+):([0-9]+)$ ]]; then
    echo $(( ${BASH_REMATCH[1]} * 86400 + ${BASH_REMATCH[2]} * 3600 + ${BASH_REMATCH[3]} * 60 + ${BASH_REMATCH[4]} ))
  elif [[ "$etime" =~ ^([0-9]+):([0-9]+):([0-9]+)$ ]]; then
    echo $(( ${BASH_REMATCH[1]} * 3600 + ${BASH_REMATCH[2]} * 60 + ${BASH_REMATCH[3]} ))
  elif [[ "$etime" =~ ^([0-9]+):([0-9]+)$ ]]; then
    echo $(( ${BASH_REMATCH[1]} * 60 + ${BASH_REMATCH[2]} ))
  else
    return 1
  fi
}

kill_stale_lldb() {
  local pid="$1"
  local lldb_pids
  lldb_pids="$(pgrep -f "lldb.*${pid}" 2>/dev/null || true)"
  [[ -n "$lldb_pids" ]] || return 0
  echo "發現殘留 lldb attach PID=${pid}，正在結束…"
  while IFS= read -r lp; do
    [[ -n "$lp" ]] || continue
    kill "$lp" 2>/dev/null || true
    echo "  killed lldb PID=${lp}"
  done <<< "$lldb_pids"
  sleep 1
}

wait_for_resigned() {
  local deadline=$((SECONDS + WAIT_SECONDS))
  echo "等待 WeChat-Resigned 主程序（最多 ${WAIT_SECONDS}s）…"
  echo "  預期執行檔: ${RESIGNED_BIN}"
  while (( SECONDS < deadline )); do
    if PID="$(find_resigned_pid)"; then
      echo "偵測到 WeChat-Resigned PID=${PID}"
      return 0
    fi
    sleep 2
  done
  echo "NO_PID: 在 ${WAIT_SECONDS}s 內未偵測到 WeChat-Resigned.app 主程序" >&2
  return 1
}

should_auto_capture() {
  local pid="$1"
  local age
  age="$(process_age_seconds "$pid")" || return 1
  echo "WeChat-Resigned PID=${pid} 已運行 ${age}s（session 閾值 ${SESSION_MAX_AGE_SEC}s）"
  if [[ "$CAPTURE_FORCE" == "1" ]]; then
    echo "CAPTURE_FORCE=1 — 強制擷取"
    return 0
  fi
  if (( age <= SESSION_MAX_AGE_SEC )); then
    echo "程序較新，可能仍在備份 UI — 自動擷取"
    return 0
  fi
  return 1
}

offer_retry() {
  local pid="$1"
  local age
  age="$(process_age_seconds "$pid")" || age="?"
  echo ""
  echo "SKIP_AUTO: WeChat-Resigned 已運行 ${age}s，可能已離開備份介面。"
  echo "請重新開啟「備份與遷移」並點擊開始，然後選擇重試。"
  echo ""
  if [[ ! -t 0 ]]; then
    echo "非互動終端 — 跳過自動重試。手動執行："
    echo "  CAPTURE_FORCE=1 $0"
    echo "或重新啟動 WeChat-Resigned 後再執行本腳本。"
    return 1
  fi
  read -r -p "已回到備份/遷移流程？按 Enter 重試一次（Ctrl+C 取消）: " _ || true
  echo "使用者確認重試"
  return 0
}

unwedged_process() {
  local pid="$1"
  local state
  state="$(ps -p "$pid" -o state= 2>/dev/null | tr -d ' ' || true)"
  if [[ "$state" == *X* ]] || [[ "$state" == *T* ]]; then
    echo "WeChat-Resigned PID=${pid} 處於 ${state}（可能被 lldb 暫停），嘗試恢復…"
    kill_stale_lldb "$pid"
    sleep 1
    state="$(ps -p "$pid" -o state= 2>/dev/null | tr -d ' ' || true)"
    if [[ "$state" == *X* ]]; then
      echo "警告：程序仍為 ${state}，請手動 Cmd+Q 退出後重新啟動 WeChat-Resigned"
    fi
  fi
}

run_capture() {
  local pid="$1"
  kill_stale_lldb "$pid"
  unwedged_process "$pid"
  countdown 5

  printf '' > "$LOG"
  {
    echo "=== resigned capture v6 start $(date -Iseconds) pid=${pid} path=${RESIGNED_APP} ==="
    echo "scan_module=${SCAN_MODULE} command=migration_capture_90s_v6 MIGRATION_ENABLE_BP=${MIGRATION_ENABLE_BP}"
    export MIGRATION_CAPTURE_APP_LABEL="WeChat-Resigned"
    export MIGRATION_CAPTURE_APP_PATH="${RESIGNED_APP}"
    lldb -b \
      -o 'settings set auto-confirm true' \
      -o "process attach --pid ${pid}" \
      -o "command script import \"${SCAN_MODULE}\"" \
      -o 'migration_capture_90s_v6' \
      -o 'detach' \
      -o 'quit'
  } 2>&1 | tee -a "$LOG"
}

write_summary() {
  local pid="$1"
  local captured=0
  if ls real_dict_5_*.bin >/dev/null 2>&1; then
    captured=$(ls -1 real_dict_5_*.bin 2>/dev/null | wc -l | tr -d ' ')
  fi

  local valid_rc=1
  if [[ -f real_dict_5.bin ]] || [[ "$captured" -gt 0 ]]; then
    python3 "$VALIDATE_SCRIPT" "${EXPORT}/real_dict_5.bin" || valid_rc=$?
  else
    echo "未產生 real_dict_5*.bin — 請在掃描期間點擊「開始備份/遷移」後重試"
  fi

  {
    echo "resigned_capture_summary $(date -Iseconds)"
    echo "version=v6"
    echo "app=WeChat-Resigned"
    echo "app_path=${RESIGNED_APP}"
    echo "pid=${pid}"
    echo "captured_files=${captured}"
    echo "validate_rc=${valid_rc}"
    if [[ -f real_dict_5.bin ]]; then
      echo "symlink=real_dict_5.bin -> $(readlink real_dict_5.bin 2>/dev/null || echo '?')"
    fi
    if grep -q 'CAPTURE_OK' "$LOG" 2>/dev/null; then
      echo "lldb_hits=yes"
      grep 'CAPTURE_OK\|VALIDATE_OK\|MAGIC5_HIT\|BP_HIT\|ROUND=\|SCAN_PROGRESS\|phase._magic5' "$LOG" || true
    else
      echo "lldb_hits=no"
      grep 'ROUND=\|SCAN_PROGRESS\|phase._magic5\|BP_\|NO_CAPTURE\|DONE' "$LOG" 2>/dev/null || true
      if grep -q 'phase1_magic5=0 phase2_magic5=0' "$LOG" 2>/dev/null; then
        echo "hint=see IOS_DICT5_README.txt if magic5=0 during active migration"
      fi
    fi
  } | tee "$SUMMARY"

  echo ""
  echo "--- ${SUMMARY} ---"
  cat "$SUMMARY"
  return "$valid_rc"
}

if [[ ! -d "$RESIGNED_APP" ]]; then
  echo "MISSING ${RESIGNED_APP} — 請先執行 re-sign 步驟" >&2
  exit 1
fi

if [[ ! -f "$SCAN_MODULE" ]]; then
  echo "MISSING ${SCAN_MODULE}" >&2
  exit 1
fi

# Clear stale lldb from a prior attach before waiting/capture.
if _early_pid="$(find_resigned_pid 2>/dev/null || true)"; then
  kill_stale_lldb "$_early_pid"
fi

print_instructions

if ! PID="$(find_resigned_pid)"; then
  if ! wait_for_resigned; then
    exit 1
  fi
  PID="$(find_resigned_pid)"
fi

echo "WeChat-Resigned PID=${PID}"
echo "App path: ${RESIGNED_APP}"
echo "輸出目錄: ${EXPORT}"
echo "Scan module: ${SCAN_MODULE}"
echo ""

RETRY_RAN=0
if should_auto_capture "$PID"; then
  run_capture "$PID"
else
  if offer_retry; then
    RETRY_RAN=1
    if ! PID="$(find_resigned_pid)"; then
      echo "WeChat-Resigned 已退出" >&2
      exit 1
    fi
    run_capture "$PID"
  else
    echo ""
    echo "擷取已跳過。請重新啟動 WeChat-Resigned 後再執行本腳本（<180s 內自動擷取）。"
    exit 2
  fi
fi

echo ""
echo "=== 擷取結束，執行驗證 ==="
write_summary "$PID"
exit $?
