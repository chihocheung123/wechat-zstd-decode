#!/bin/bash
# resign_wechatappex.sh — Resign WeChatAppEx helper with get-task-allow so LLDB can attach.
#
# Background:
#   App Store WeChat has hardened runtime + app sandbox — even sudo lldb is denied
#   task_for_pid. The only reliable path to LLDB-attach WeChatAppEx is to strip the
#   hardened-runtime flag and add the get-task-allow entitlement (ad-hoc resign).
#
# What this script does:
#   1. Finds WeChatAppEx inside WeChat.app (checks several known sub-bundle paths).
#   2. Copies the entire WeChatAppEx.app bundle to $WORKSPACE/WeChatAppEx-Resigned.app.
#   3. Resigns every Mach-O binary in the bundle with:
#      - ad-hoc identity (-)
#      - get-task-allow entitlement
#      - --options=runtime stripped (removes hardened runtime requirement)
#   4. Prints instructions for launching alongside WeChat-Resigned.app.
#
# Usage:
#   ./bin/resign_wechatappex.sh [--wechat-app <path>] [--out <dest>]
#
#   --wechat-app <path>   Path to WeChat.app (default: /Applications/WeChat.app)
#   --out <dest>          Destination for WeChatAppEx-Resigned.app (default: $WORKSPACE)
#
# Requirements:
#   - Xcode command-line tools (codesign)
#   - SIP must NOT block codesign on a copied binary (copying to workspace avoids this)
#
# After running this script, follow the instructions in:
#   docs/APPEX_RESIGNED_CAPTURE.txt

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE="${WECHAT_ZSTD_WORKSPACE:-$REPO_ROOT/data}"
mkdir -p "$WORKSPACE"

WECHAT_APP="/Applications/WeChat.app"
OUT_DIR="$WORKSPACE"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --wechat-app) WECHAT_APP="$2"; shift 2 ;;
    --out) OUT_DIR="$2"; shift 2 ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \?//'
      exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║         resign_wechatappex.sh — WeChatAppEx get-task-allow   ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# --- Locate WeChatAppEx inside WeChat.app ---
APPEX_SRC=""
CANDIDATE_PATHS=(
  "${WECHAT_APP}/Contents/XPCServices/WeChatAppEx.app"
  "${WECHAT_APP}/Contents/Library/LoginItems/WeChatAppEx.app"
  "${WECHAT_APP}/Contents/PlugIns/WeChatAppEx.app"
  "${WECHAT_APP}/Contents/MacOS/WeChatAppEx.app"
)

for p in "${CANDIDATE_PATHS[@]}"; do
  if [[ -d "$p" ]]; then
    APPEX_SRC="$p"
    echo "Found WeChatAppEx bundle: $p"
    break
  fi
done

if [[ -z "$APPEX_SRC" ]]; then
  # Fallback: deep search inside the WeChat.app bundle
  echo "Searching for WeChatAppEx.app inside ${WECHAT_APP} ..."
  APPEX_SRC="$(find "$WECHAT_APP" -name "WeChatAppEx.app" -maxdepth 8 2>/dev/null | head -1 || true)"
fi

if [[ -z "$APPEX_SRC" ]] || [[ ! -d "$APPEX_SRC" ]]; then
  echo ""
  echo "ERROR: WeChatAppEx.app not found inside $WECHAT_APP"
  echo ""
  echo "This may mean:"
  echo "  - WeChat.app is not installed at $WECHAT_APP"
  echo "  - WeChatAppEx ships as a standalone process, not a sub-bundle"
  echo "    (check: find /Applications/WeChat.app -name 'WeChatAppEx' -type f)"
  echo ""
  echo "If WeChatAppEx is a plain binary (not a .app bundle), run:"
  echo "  ./bin/resign_wechatappex.sh --wechat-app <path>"
  exit 1
fi

APPEX_DEST="${OUT_DIR}/WeChatAppEx-Resigned.app"

# --- Copy the bundle ---
echo "Copying to: $APPEX_DEST"
if [[ -e "$APPEX_DEST" ]]; then
  echo "Removing existing $APPEX_DEST ..."
  rm -rf "$APPEX_DEST"
fi
cp -R "$APPEX_SRC" "$APPEX_DEST"
echo "Copy done."
echo ""

# --- Create entitlements plist ---
ENTITLEMENTS_TMP="$(mktemp /tmp/wechatappex_entitlements.XXXXXX.plist)"
cat > "$ENTITLEMENTS_TMP" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>com.apple.security.get-task-allow</key>
    <true/>
</dict>
</plist>
PLIST

echo "Entitlements file: $ENTITLEMENTS_TMP"
cat "$ENTITLEMENTS_TMP"
echo ""

# --- Resign all Mach-O binaries in the bundle (deep-first) ---
RESIGN_COUNT=0
RESIGN_ERRORS=0

resign_binary() {
  local bin="$1"
  # Check it's a Mach-O (magic bytes: CE FA ED FE or CF FA ED FE or CA FE BA BE)
  local magic
  magic="$(xxd -l 4 -p "$bin" 2>/dev/null || true)"
  case "$magic" in
    feedfacf|feedface|cafebabe|cffaedfe|cefaedfe) ;;  # Mach-O or fat binary
    *) return 0 ;;  # skip non-Mach-O
  esac

  echo "  codesign: $(basename "$bin")"
  if codesign --force --sign - \
      --entitlements "$ENTITLEMENTS_TMP" \
      --timestamp=none \
      "$bin" 2>&1; then
    RESIGN_COUNT=$((RESIGN_COUNT + 1))
  else
    echo "  WARNING: codesign failed for $bin" >&2
    RESIGN_ERRORS=$((RESIGN_ERRORS + 1))
  fi
}

echo "Resigning Mach-O binaries in bundle ..."
# Sign leaf binaries first, then the app bundle (codesign requirement)
while IFS= read -r -d '' f; do
  resign_binary "$f"
done < <(find "$APPEX_DEST" -type f -not -name "*.plist" -not -name "*.nib" -print0 | sort -rz)

# Sign the top-level bundle last
codesign --force --sign - \
    --entitlements "$ENTITLEMENTS_TMP" \
    --timestamp=none \
    "$APPEX_DEST" 2>&1 || true

rm -f "$ENTITLEMENTS_TMP"

echo ""
echo "Resigned ${RESIGN_COUNT} binaries. Errors: ${RESIGN_ERRORS}"
echo ""

# --- Verify ---
echo "Verifying signature ..."
codesign -dv "$APPEX_DEST" 2>&1 || true
echo ""
echo "Checking get-task-allow entitlement ..."
codesign -d --entitlements - "$APPEX_DEST" 2>&1 | grep -A2 'get-task-allow' || echo "(entitlement not found in display — check above)"
echo ""

if [[ "$RESIGN_ERRORS" -gt 0 ]]; then
  echo "WARNING: ${RESIGN_ERRORS} resign error(s) — some binaries may not be attachable."
fi

echo "═══════════════════════════════════════════════════════════════"
echo "  WeChatAppEx-Resigned.app is ready at:"
echo "  $APPEX_DEST"
echo ""
echo "  NEXT STEPS:"
echo "  1. Launch WeChat-Resigned.app first (it spawns WeChatAppEx)."
echo "     open \"${WORKSPACE}/WeChat-Resigned.app\""
echo ""
echo "  2. Run capture_dict5_appex_resigned.sh to attach and scan:"
echo "     ./bin/capture_dict5_appex_resigned.sh"
echo ""
echo "  NOTE: If WeChat spawns its own WeChatAppEx from inside the bundle"
echo "  rather than using this standalone resigned copy, you may need to"
echo "  resign the whole WeChat.app bundle. See docs/APPEX_RESIGNED_CAPTURE.txt"
echo "═══════════════════════════════════════════════════════════════"
echo ""
