#!/bin/bash
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE="${WECHAT_ZSTD_WORKSPACE:-$REPO_ROOT/data}"
# Scan all running WeChat-related processes, check get-task-allow entitlement,
# and report which ones LLDB can attach to without SIP/hardened-runtime denial.
#
# Usage: ./bin/find_attachable_wechat.sh
#
# Output example:
#   PID 80123  WeChat             /Applications/WeChat.app/...        NO  (hardened)
#   PID 80698  WeChatAppEx        /Applications/WeChat.app/...        NO  (hardened)
#   PID 81234  WeChat (Debug)     ~/Applications/WeChat-Debug.app/... YES get-task-allow
#
# A process marked YES is safe to attach with LLDB without --sudo or SIP changes.

set -euo pipefail

# shellcheck source=_wechat_app_detect.sh
source "${REPO_ROOT}/bin/_wechat_app_detect.sh"

PATTERNS=(
  'WeChat'
  'WeChatAppEx'
  'crashpad_handler'
  'wxocr'
  'wxplayer'
  'wxutility'
  'roam_migration'
)

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo ""
echo "=== WeChat process attachability report ==="
echo ""
printf "%-8s %-20s %-12s %s\n" "PID" "PROCESS" "ATTACHABLE" "BINARY"
printf "%-8s %-20s %-12s %s\n" "---" "-------" "----------" "------"

found=0
seen_pids=""

pid_seen() {
  case " $seen_pids " in
    *" $1 "*) return 0 ;;
    *) return 1 ;;
  esac
}

mark_pid_seen() {
  seen_pids="${seen_pids}${1} "
}

for pattern in "${PATTERNS[@]}"; do
  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    pid="${line%% *}"
    pid_seen "$pid" && continue
    mark_pid_seen "$pid"

    bin_path="$(get_pid_executable "$pid" 2>/dev/null || echo '?')"
    proc_name="$(basename "$bin_path" 2>/dev/null || echo "$pattern")"

    # Check entitlements
    attachable="NO"
    reason="hardened/SIP"
    color="$RED"

    if [[ "$bin_path" != "?" ]] && has_get_task_allow "$bin_path" 2>/dev/null; then
      attachable="YES"
      reason="get-task-allow"
      color="$GREEN"
    elif [[ "$bin_path" == *"WeChat-Debug"* ]] || [[ "$bin_path" == *"WeChat-Resigned"* ]]; then
      # Debug/resigned builds typically have get-task-allow even if codesign check unclear
      attachable="LIKELY"
      reason="debug/resigned build"
      color="$YELLOW"
    fi

    printf "%-8s %-20s ${color}%-12s${NC} %s\n" \
      "$pid" "$proc_name" "$attachable ($reason)" "$bin_path"
    found=$((found + 1))
  done < <(pgrep -lf "$pattern" 2>/dev/null || true)
done

echo ""
if [[ "$found" -eq 0 ]]; then
  echo "No WeChat-related processes found. Open WeChat and retry."
  exit 1
fi

echo "--- Recommendation ---"
echo ""

# Collect all binary paths from seen PIDs and check for debug/resigned builds
has_attachable=false
for pid in $seen_pids; do
  bin="$(get_pid_executable "$pid" 2>/dev/null || true)"
  if [[ "$bin" == *"WeChat-Debug"* ]] || [[ "$bin" == *"WeChat-Resigned"* ]]; then
    has_attachable=true
    break
  fi
  if [[ "$bin" != "?" ]] && has_get_task_allow "$bin" 2>/dev/null; then
    has_attachable=true
    break
  fi
done

if [[ "$has_attachable" == "true" ]]; then
  echo -e "${GREEN}✅ Debug/Resigned build detected — use the matching LLDB capture path:${NC}"
  echo "   ./bin/capture_dict5_appex_resigned.sh          (attachable WeChatAppEx/helper)"
  echo "   ./bin/capture_dict5_migration.sh --app debug    (WeChat-Debug main process)"
  echo "   ./bin/capture_dict5_resigned.sh                 (WeChat-Resigned with backup UI)"
else
  echo -e "${RED}❌ Only hardened App Store WeChat found — LLDB attach not possible.${NC}"
  echo ""
  echo "Options:"
  echo "  1. Install WeChat-Debug.app in ~/Applications/ and open it"
  echo "     Then: ./bin/capture_dict5_migration.sh --app debug"
  echo ""
  echo "  2. Re-sign WeChat.app with get-task-allow entitlement:"
  echo "     Preferred full bundle path: ./bin/resign_wechat_full.sh"
  echo "     Helper-only fallback: ./bin/resign_wechatappex.sh"
  echo "     Then: ./bin/capture_dict5_appex_resigned.sh"
  echo ""
  echo "  3. Capture on iOS device (most reliable for dict_id=5):"
  echo "     See docs/IOS_DICT5_README.txt"
fi
echo ""
