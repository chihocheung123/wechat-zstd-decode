#!/bin/bash
# scan_wechat_fs_for_dict5.sh — Search WeChat on-disk container directories for MAGIC5 pattern.
#
# WHY THIS EXISTS
# ---------------
# Multiple LLDB memory captures (main WeChat, WeChatAppEx, ILinkServiceHost) all returned
# MAGIC5 hits=0. If dict_id=5 is never loaded into Mac process memory during tested triggers,
# it may still exist on-disk — either as a cache file, a bundled resource, or an XPC
# service data directory.
#
# This script scans all known WeChat filesystem paths on macOS for the 8-byte MAGIC5
# header (37 A4 30 EC 05 00 00 00) without requiring LLDB or process attachment.
#
# Usage:
#   ./bin/scan_wechat_fs_for_dict5.sh
#   WECHAT_ZSTD_WORKSPACE=/path/to/data ./bin/scan_wechat_fs_for_dict5.sh
#
# Output:
#   Reports each file searched, hit count, and — on match — copies the 112640-byte
#   candidate to $WORKSPACE/real_dict_5_fs_<filename>.bin for validation.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE="${WECHAT_ZSTD_WORKSPACE:-$REPO_ROOT/data}"
mkdir -p "$WORKSPACE"

LOG="$WORKSPACE/fs_scan.log"
VALIDATE_SCRIPT="$REPO_ROOT/scripts/validate_dict5.py"
PY_SCAN="$WORKSPACE/_fs_scan_helper.py"

# MAGIC5 = ZSTD dict magic (0xEC30A437 LE) + dict_id=5 (LE)
MAGIC5_HEX="37a430ec05000000"
DICT_SIZE=112640

# Write the Python helper script to disk (avoids heredoc-in-$() parsing issues).
cat > "$PY_SCAN" << 'PYEOF'
"""Scan a single file for MAGIC5 pattern; print matching offsets."""
import sys
import os

def scan_file(fpath, magic_hex, max_size=200 * 1024 * 1024):
    magic = bytes.fromhex(magic_hex)
    fsize = os.path.getsize(fpath)
    if fsize < len(magic) or fsize >= max_size:
        return []
    chunk = 65536
    overlap = len(magic) - 1
    hits = []
    with open(fpath, 'rb') as f:
        offset = 0
        prev = b''
        while True:
            data = f.read(chunk)
            if not data:
                break
            buf = prev + data
            pos = 0
            while True:
                idx = buf.find(magic, pos)
                if idx < 0:
                    break
                real = offset - len(prev) + idx
                hits.append(real)
                pos = idx + 1
            prev = buf[-overlap:] if overlap > 0 else b''
            offset += len(data)
    return hits

def extract_bytes(fpath, offset, length, dest):
    with open(fpath, 'rb') as f:
        f.seek(offset)
        data = f.read(length)
    with open(dest, 'wb') as g:
        g.write(data)
    return len(data)

if __name__ == '__main__':
    cmd = sys.argv[1]
    if cmd == 'scan':
        fpath, magic_hex = sys.argv[2], sys.argv[3]
        hits = scan_file(fpath, magic_hex)
        for h in hits:
            print(h)
    elif cmd == 'extract':
        fpath, offset, length, dest = sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), sys.argv[5]
        n = extract_bytes(fpath, offset, length, dest)
        print(f'extracted {n} bytes')
    elif cmd == 'head8':
        fpath = sys.argv[2]
        with open(fpath, 'rb') as f:
            print(f.read(8).hex())
PYEOF

echo "=== WeChat FS MAGIC5 Scan ===" | tee "$LOG"
echo "Started: $(date)" | tee -a "$LOG"
echo "MAGIC5: $MAGIC5_HEX" | tee -a "$LOG"
echo "Expected dict size: $DICT_SIZE bytes" | tee -a "$LOG"
echo "" | tee -a "$LOG"

# ---------------------------------------------------------------------------
# WeChat container/cache paths to search
# ---------------------------------------------------------------------------
SEARCH_ROOTS=(
    "$HOME/Library/Containers/com.tencent.xinWeChat"
    "$HOME/Library/Containers/com.tencent.WeChat"
    "$HOME/Library/Group Containers/group.com.tencent.xinWeChat"
    "$HOME/Library/Group Containers/group.com.tencent.WeChat"
    "$HOME/Library/Application Support/WeChat"
    "$HOME/Library/Application Support/com.tencent.WeChat"
    "$HOME/Library/Caches/com.tencent.xinWeChat"
    "$HOME/Library/Caches/com.tencent.WeChat"
    "/Applications/WeChat.app/Contents"
    "$HOME/Applications/WeChat.app/Contents"
    "$HOME/Applications/WeChat-Debug.app/Contents"
    "$HOME/Desktop/WeChat-Resigned.app/Contents"
    "$HOME/Desktop/WeChat-Resigned-Full.app/Contents"
    # Active resigned bundle used in all LLDB captures (P1 fix: was missing)
    "$HOME/Downloads/wechat_export/WeChat-Resigned.app/Contents"
    "$HOME/Downloads/wechat_export/WeChat-Resigned-Full.app/Contents"
    "${WORKSPACE}"
)

scanned_count=0
hit_count=0
skipped_count=0
found_list=""   # tab-separated: "filepath\toffset"

scan_one_file() {
    local fpath="$1"
    local fsize
    fsize=$(stat -f%z "$fpath" 2>/dev/null || echo 0)

    # Skip empty, too-small, or too-large (>= 500 MB) files.
    if [[ "$fsize" -lt 8 ]] || [[ "$fsize" -ge 524288000 ]]; then
        skipped_count=$((skipped_count + 1))
        return
    fi

    scanned_count=$((scanned_count + 1))

    # Fast header check at offset 0 (most likely for standalone dict file).
    local head8
    head8=$(python3 "$PY_SCAN" head8 "$fpath" 2>/dev/null || echo "")
    if [[ "$head8" == "$MAGIC5_HEX" ]]; then
        echo "[HIT@0] $fpath  size=$fsize" | tee -a "$LOG"
        hit_count=$((hit_count + 1))
        local safe
        safe=$(basename "$fpath" | tr ' /\\:' '____')
        local dest="$WORKSPACE/real_dict_5_fs_${safe}.bin"
        # Always extract exactly DICT_SIZE bytes so validate_dict5.py sees the
        # correct-length candidate even when MAGIC5 is embedded in a larger file.
        python3 "$PY_SCAN" extract "$fpath" 0 "$DICT_SIZE" "$dest" 2>/dev/null
        echo "  → Extracted ${DICT_SIZE}B to $dest" | tee -a "$LOG"
        found_list="${found_list}${fpath}	0
"
        return
    fi

    # Deeper byte-level search (files <= 200 MB — covers large WeChat DB/cache files).
    if [[ "$fsize" -le 209715200 ]]; then
        local offsets
        offsets=$(python3 "$PY_SCAN" scan "$fpath" "$MAGIC5_HEX" 2>/dev/null || echo "")
        if [[ -n "$offsets" ]]; then
            while IFS= read -r off; do
                [[ -n "$off" ]] || continue
                echo "[HIT@$off] $fpath  size=$fsize" | tee -a "$LOG"
                hit_count=$((hit_count + 1))
                local safe
                safe=$(basename "$fpath" | tr ' /\\:' '____')
                local dest="$WORKSPACE/real_dict_5_fs_${safe}_off${off}.bin"
                python3 "$PY_SCAN" extract "$fpath" "$off" "$DICT_SIZE" "$dest" 2>/dev/null
                echo "  → Extracted to $dest" | tee -a "$LOG"
                found_list="${found_list}${fpath}	${off}
"
            done <<< "$offsets"
        fi
    fi
}

for root in "${SEARCH_ROOTS[@]}"; do
    if [[ ! -d "$root" ]]; then
        echo "[ skip ] $root  (not found)" | tee -a "$LOG"
        continue
    fi
    echo "[search] $root" | tee -a "$LOG"
    while IFS= read -r fpath; do
        scan_one_file "$fpath"
    done < <(find "$root" -type f \
        \( -name "*.bin" -o -name "*.dat" -o -name "*.cache" -o -name "*.db" \
           -o -name "*.zstd" -o -name "*.dict" -o -name "*.data" \
           -o -name "dict*" -o -name "*dict*" -o -name "*zstd*" \
           -o -name "*.resource" -o -name "*.res" \) \
        2>/dev/null | head -2000)
done

echo "" | tee -a "$LOG"
echo "=== Summary ===" | tee -a "$LOG"
echo "Scanned:     $scanned_count files" | tee -a "$LOG"
echo "Skipped:     $skipped_count files (too small/large)" | tee -a "$LOG"
echo "MAGIC5 hits: $hit_count" | tee -a "$LOG"

if [[ "$hit_count" -gt 0 ]]; then
    echo "" | tee -a "$LOG"
    echo "Candidates copied — validating:" | tee -a "$LOG"
    while IFS="	" read -r fpath off; do
        [[ -n "$fpath" ]] || continue
        safe=$(basename "$fpath" | tr ' /\\:' '____')
        if [[ "$off" == "0" ]]; then
            candidate="$WORKSPACE/real_dict_5_fs_${safe}.bin"
        else
            candidate="$WORKSPACE/real_dict_5_fs_${safe}_off${off}.bin"
        fi
        if [[ -f "$candidate" ]]; then
            echo "" | tee -a "$LOG"
            echo "--- Validating: $candidate ---" | tee -a "$LOG"
            python3 "$VALIDATE_SCRIPT" "$candidate" 2>&1 | tee -a "$LOG" || true
        fi
    done <<< "$found_list"
else
    echo "" | tee -a "$LOG"
    echo "No MAGIC5 hits in on-disk WeChat paths." | tee -a "$LOG"
    echo "dict_id=5 appears not to be cached to disk on this Mac." | tee -a "$LOG"
    echo "Next step: iOS device capture — see docs/IOS_DICT5_README.txt" | tee -a "$LOG"
fi

echo "" | tee -a "$LOG"
echo "Finished: $(date)" | tee -a "$LOG"
echo "Full log: $LOG"
