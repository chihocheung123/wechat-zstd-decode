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
