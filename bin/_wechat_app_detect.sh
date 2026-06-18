#!/bin/bash
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE="${WECHAT_ZSTD_WORKSPACE:-$REPO_ROOT/data}"
mkdir -p "$WORKSPACE"
# Shared WeChat / WeChat-Debug main-process detection for capture scripts.
# Source from bash: . "${EXPORT}/_wechat_app_detect.sh"

WECHAT_DEBUG_APP="/Users/patrickchiho/Applications/WeChat-Debug.app"
WECHAT_REGULAR_APP="/Applications/WeChat.app"

wechat_app_paths() {
  local mode="${1:-auto}"
  case "$mode" in
    debug)
      echo "$WECHAT_DEBUG_APP"
      ;;
    regular|release)
      echo "$WECHAT_REGULAR_APP"
      ;;
    auto)
      echo "$WECHAT_REGULAR_APP"
      echo "$WECHAT_DEBUG_APP"
      ;;
    *)
      echo "Unknown app mode: $mode (use auto|debug|regular)" >&2
      return 1
      ;;
  esac
}

wechat_app_label_for_path() {
  local app_path="$1"
  case "$app_path" in
    *WeChat-Debug.app*) echo "WeChat-Debug" ;;
    *WeChat.app*) echo "WeChat" ;;
    *) echo "WeChat" ;;
  esac
}

# Sets WECHAT_PID, WECHAT_APP_PATH, WECHAT_APP_LABEL. Returns 0 on success.
find_wechat_main_pid() {
  local mode="${1:-auto}"
  local app_path pattern pid cmd
  WECHAT_PID=""
  WECHAT_APP_PATH=""
  WECHAT_APP_LABEL=""

  while IFS= read -r app_path; do
    [[ -d "$app_path" ]] || continue
    pattern="${app_path}/Contents/MacOS/WeChat"
    while IFS= read -r line; do
      pid="${line%% *}"
      cmd="${line#* }"
      case "$cmd" in
        "$pattern") ;;
        *) continue ;;
      esac
      case "$cmd" in
        *WeChatAppEx*|*crashpad*|*wxocr*|*wxplayer*|*wxutility*) continue ;;
      esac
      WECHAT_PID="$pid"
      WECHAT_APP_PATH="$app_path"
      WECHAT_APP_LABEL="$(wechat_app_label_for_path "$app_path")"
      return 0
    done < <(pgrep -lf "$pattern" 2>/dev/null || true)
  done < <(wechat_app_paths "$mode")

  return 1
}

wechat_installed_summary() {
  local app
  for app in "$WECHAT_REGULAR_APP" "$WECHAT_DEBUG_APP"; do
    if [[ -d "$app" ]]; then
      local ver
      ver="$(plutil -extract CFBundleShortVersionString raw -o - "$app/Contents/Info.plist" 2>/dev/null || echo '?')"
      echo "  - $(basename "$app") v${ver} @ $app"
    fi
  done
}

# Sets WECHATAPPEX_PIDS (space-separated) and WECHATAPPEX_COUNT.
# Returns 0 if at least one WeChatAppEx process is found.
find_wechatappex_pids() {
  WECHATAPPEX_PIDS=""
  WECHATAPPEX_COUNT=0
  local pids
  pids="$(pgrep -f 'WeChatAppEx.app/Contents/MacOS/WeChatAppEx' 2>/dev/null | head -5 || true)"
  if [[ -z "$pids" ]]; then
    pids="$(pgrep -x WeChatAppEx 2>/dev/null | head -5 || true)"
  fi
  [[ -z "$pids" ]] && return 1
  WECHATAPPEX_PIDS="$(echo "$pids" | tr '\n' ' ' | sed 's/ $//')"
  WECHATAPPEX_COUNT="$(echo "$pids" | grep -c . || true)"
  return 0
}

# Check whether roam_migration is loaded in a given PID (quick image-list only, no scan).
# Usage: wechatappex_has_roam_migration <pid>
# Prints the slide+path line and returns 0 if found, 1 otherwise.
# If WECHAT_LLDB_SUDO=1, uses sudo lldb for hardened processes.
wechatappex_has_roam_migration() {
  local pid="$1"
  local result rc
  local lldb_cmd=(lldb -b -p "$pid")
  if [[ "${WECHAT_LLDB_SUDO:-0}" == "1" ]]; then
    lldb_cmd=(sudo lldb -b -p "$pid")
  fi

  local restore_errexit=0
  case "$-" in
    *e*) restore_errexit=1; set +e ;;
  esac

  result="$("${lldb_cmd[@]}" \
    -o 'settings set auto-confirm true' \
    -o 'image list -o -f' \
    -o 'quit' 2>&1)"
  rc=$?

  if [[ "$restore_errexit" -eq 1 ]]; then
    set -e
  fi

  if [[ "$rc" -ne 0 ]] || grep -qi 'not allowed to attach\|attach failed' <<<"$result"; then
    echo "$result" | grep -iE 'not allowed to attach|attach failed|error:' | head -3 >&2 || true
    return 2
  fi

  result="$(echo "$result" | grep -i 'roam_migration' | head -3 || true)"
  [[ -n "$result" ]] && {
    echo "$result"
    return 0
  }
  return 1
}
