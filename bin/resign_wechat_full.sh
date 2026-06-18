#!/bin/bash
# resign_wechat_full.sh — Copy and ad-hoc resign the full WeChat.app bundle.
#
# This is the preferred macOS path after App Store WeChatAppEx rejects LLDB
# even with sudo. It creates a local WeChat-Resigned-Full.app whose main
# binary and embedded helpers can carry get-task-allow.
#
# Usage:
#   ./bin/resign_wechat_full.sh [--wechat-app <path>] [--out-app <path>]
#   ./bin/resign_wechat_full.sh --skip-copy --out-app <existing-copy.app>
#
# Defaults:
#   --wechat-app /Applications/WeChat.app
#   --out-app    $WECHAT_ZSTD_WORKSPACE/WeChat-Resigned-Full.app

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE="${WECHAT_ZSTD_WORKSPACE:-$REPO_ROOT/data}"
mkdir -p "$WORKSPACE"

WECHAT_APP="/Applications/WeChat.app"
OUT_APP="${WORKSPACE}/WeChat-Resigned-Full.app"
SKIP_COPY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --wechat-app) WECHAT_APP="$2"; shift 2 ;;
    --out-app) OUT_APP="$2"; shift 2 ;;
    --skip-copy) SKIP_COPY=1; shift ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \?//'
      exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║       resign_wechat_full.sh — full WeChat get-task-allow     ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

if [[ "$SKIP_COPY" -eq 0 ]]; then
  if [[ ! -d "$WECHAT_APP" ]]; then
    echo "ERROR: WeChat.app not found: $WECHAT_APP" >&2
    exit 1
  fi
  if [[ -e "$OUT_APP" ]]; then
    echo "Removing existing copy: $OUT_APP"
    rm -rf "$OUT_APP"
  fi
  echo "Copying:"
  echo "  from: $WECHAT_APP"
  echo "  to:   $OUT_APP"
  cp -R "$WECHAT_APP" "$OUT_APP"
else
  if [[ ! -d "$OUT_APP" ]]; then
    echo "ERROR: --skip-copy requires an existing --out-app bundle." >&2
    exit 1
  fi
fi

ENTITLEMENTS_TMP="$(mktemp /tmp/wechat_full_entitlements.XXXXXX.plist)"
trap 'rm -f "$ENTITLEMENTS_TMP"' EXIT
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

RESIGN_COUNT=0
RESIGN_ERRORS=0

is_macho() {
  local f="$1"
  local magic
  magic="$(xxd -l 4 -p "$f" 2>/dev/null || true)"
  case "$magic" in
    feedfacf|feedface|cafebabe|cffaedfe|cefaedfe) return 0 ;;
    *) return 1 ;;
  esac
}

sign_path() {
  local target="$1"
  echo "  codesign: $target"
  if codesign --force --sign - \
      --entitlements "$ENTITLEMENTS_TMP" \
      --timestamp=none \
      "$target" 2>&1; then
    RESIGN_COUNT=$((RESIGN_COUNT + 1))
  else
    echo "  WARNING: codesign failed: $target" >&2
    RESIGN_ERRORS=$((RESIGN_ERRORS + 1))
  fi
}

echo ""
echo "Signing Mach-O files ..."
while IFS= read -r -d '' f; do
  [[ -f "$f" ]] || continue
  is_macho "$f" || continue
  sign_path "$f"
done < <(find "$OUT_APP" -type f \
    -not -path "*/*.framework/*" \
    -not -path "*/*.xpc/*" \
    -not -path "*/*.appex/*" \
    -not -path "*/*.bundle/*" \
    -print0)

echo ""
echo "Signing embedded bundles deepest first ..."
while IFS= read -r bundle; do
  [[ -d "$bundle" ]] || continue
  [[ "$bundle" == "$OUT_APP" ]] && continue
  sign_path "$bundle"
done < <(
  find "$OUT_APP" -type d \( \
    -name "*.app" -o \
    -name "*.framework" -o \
    -name "*.xpc" -o \
    -name "*.appex" -o \
    -name "*.bundle" \
  \) -print | awk '{ print length($0) "\t" $0 }' | sort -rn | cut -f2-
)

echo ""
echo "Signing top-level app bundle ..."
sign_path "$OUT_APP"

echo ""
echo "Signed ${RESIGN_COUNT} item(s). Errors: ${RESIGN_ERRORS}"
echo ""

echo "Verifying top-level signature ..."
codesign -dv "$OUT_APP" 2>&1 || true
echo ""
echo "Checking top-level get-task-allow entitlement ..."
codesign -d --entitlements - "$OUT_APP" 2>&1 | grep -A2 'get-task-allow' || true

if [[ "$RESIGN_ERRORS" -gt 0 ]]; then
  echo ""
  echo "ERROR: signing had ${RESIGN_ERRORS} error(s). Fix them before capture." >&2
  exit 1
fi

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  Full resigned WeChat bundle is ready:"
echo "  $OUT_APP"
echo ""
echo "  NEXT:"
echo "    open \"$OUT_APP\""
echo "    ./bin/find_attachable_wechat.sh"
echo "    ./bin/capture_dict5_appex_resigned.sh"
echo "═══════════════════════════════════════════════════════════════"
echo ""
