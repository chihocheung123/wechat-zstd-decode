#!/bin/bash
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE="${WECHAT_ZSTD_WORKSPACE:-$REPO_ROOT/data}"
mkdir -p "$WORKSPACE"
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
EXPORT="$DIR"

killall lldb 2>/dev/null || true
sleep 0.5

PID=""
while IFS= read -r line; do
  pid="${line%% *}"
  cmd="${line#* }"
  case "$cmd" in
    */WeChat-Debug.app/Contents/MacOS/WeChat) ;;
    *) continue ;;
  esac
  case "$cmd" in
    *WeChatAppEx*|*crashpad*) continue ;;
  esac
  PID="$pid"
  break
done < <(pgrep -lf 'WeChat-Debug.app/Contents/MacOS/WeChat' 2>/dev/null || true)

if [ -z "$PID" ]; then
  echo "Start WeChat-Debug first"
  exit 1
fi
echo "WeChat main PID=$PID"

SYMBOL_LOG="$EXPORT/symbol_hunt_${PID}.txt"
{
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) symbol hunt pid=$PID ==="
  echo "--- lldb wcdb_symbol_hunt (SB API; image lookup -r can hang) ---"
} >"$SYMBOL_LOG"
lldb -b -p "$PID" \
  -o 'settings set interpreter.require-overwrite true' \
  -o "command script import $DIR/lldb_capture_setup.py" \
  -o 'wcdb_symbol_hunt no-bp' \
  -o 'detach' -o 'quit' \
  2>&1 | tee -a "$SYMBOL_LOG"

ROAM="/Users/patrickchiho/Applications/WeChat-Debug.app/Contents/Frameworks/roam_migration.framework/Versions/A/roam_migration"
{
  echo ""
  echo "--- static nm roam_migration (wcdb_decompress stripped) ---"
  nm "$ROAM" 2>/dev/null | grep -iE 'wcdb_decompress|ZSTD_decompress_usingDict|ZSTD_createDDict' || echo "(no exported symbols)"
} >>"$SYMBOL_LOG"

echo "--- memory scan via lldb_capture_setup ---"
lldb -b -p "$PID" -s "$DIR/lldb_memory_scan_only.lldb" 2>&1 | tee "$EXPORT/symbol_memory_scan_run.log"

RESULTS="$EXPORT/memory_scan_results.txt"
{
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) memory_scan_results pid=$PID ==="
  python3 "$DIR/test_real_dict_5.py"
  echo ""
  echo "=== dict_from_*.bin vs target_4134_from_db.blob ==="
  python3 - "$EXPORT" <<'PY'
import glob
import os
import sys

EXPORT = sys.argv[1]
TARGET = os.path.join(EXPORT, "target_4134_from_db.blob")
MARKERS = ("笙歌", "appmsg")

try:
    import zstandard as zstd
except ImportError:
    print("pip install zstandard")
    sys.exit(1)

if not os.path.isfile(TARGET):
    print("missing", TARGET)
    sys.exit(0)

blob = open(TARGET, "rb").read()
dumps = sorted(glob.glob(os.path.join(EXPORT, "dict_from_*.bin")))
print(f"dict_from dumps: {len(dumps)}")
any_ok = False
for path in dumps:
    dd = open(path, "rb").read()
    for off in (0, 4):
        try:
            out = zstd.ZstdDecompressor(
                dict_data=zstd.ZstdCompressionDict(dd)
            ).decompress(blob[off:])
            text = out.decode("utf-8", errors="replace")
            hit = [m for m in MARKERS if m in text]
            if hit:
                any_ok = True
                print(f"OK {os.path.basename(path)} offset={off} markers={hit} len={len(out)}")
            else:
                print(f"DECOMP_OK_NO_MARKER {os.path.basename(path)} offset={off} len={len(out)}")
        except Exception as e:
            print(f"FAIL {os.path.basename(path)} offset={off}: {e}")
if not dumps:
    print("no dict_from_*.bin files")
elif not any_ok:
    print("no successful decompress with 笙歌/appmsg markers")
PY
} | tee "$RESULTS"

echo "Done. Symbol log: $SYMBOL_LOG"
echo "Decompress results: $RESULTS"
