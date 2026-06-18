#!/usr/bin/env python3
"""Track 3: analyze roam_migration table around 0x3527fc."""
import struct
import os
import subprocess

BIN = "/tmp/roam_migration_arm64"
OUT = "str(WORKSPACE)/static_slot5_table_report.txt"
LO = 0x352000
HI = 0x353500
SLOT_OFF = 0x3527FC

def main():
    if not os.path.isfile(BIN):
        raise SystemExit(f"missing {BIN}")
    with open(BIN, "rb") as f:
        data = f.read()
    size = len(data)
    lines = []
    lines.append(f"roam_migration_arm64 size={size} path={BIN}")
    lines.append(f"hex region 0x{LO:x}-0x{HI:x} (focus slot @ 0x{SLOT_OFF:x})")
    lines.append("")

    chunk = data[LO:HI]
    # uint32 array view (16-byte rows)
    lines.append("=== uint32 rows (4 cols) around 0x3527fc ===")
    rel_focus = SLOT_OFF - LO
    start_row = max(0, (rel_focus // 16) - 8)
    for row_i in range(start_row, start_row + 24):
        off = LO + row_i * 16
        if off + 16 > HI:
            break
        u32 = struct.unpack_from("<4I", data, off)
        mark = " <-- contains 0x3527fc" if off <= SLOT_OFF < off + 16 else ""
        idx5_hint = ""
        if off <= SLOT_OFF < off + 16:
            word_idx = (SLOT_OFF - off) // 4
            idx5_hint = f" word_at_3527fc=u32[{word_idx}]={u32[word_idx]}"
        lines.append(f"0x{off:06x}: " + " ".join(f"{x:08x}" for x in u32) + mark + idx5_hint)

    lines.append("")
    lines.append("=== uint64 rows (2 cols) same region ===")
    for row_i in range(start_row, start_row + 24):
        off = LO + row_i * 16
        if off + 16 > HI:
            break
        u64 = struct.unpack_from("<2Q", data, off)
        mark = " <-- 0x3527fc" if off <= SLOT_OFF < off + 16 else ""
        lines.append(f"0x{off:06x}: " + " ".join(f"{x:016x}" for x in u64) + mark)

    # index-5 as array: if base 0x352000 step 4 -> index at 0x352014; step 8 -> 0x352028
    lines.append("")
    lines.append("=== index-5 interpretations ===")
    base_candidates = [0x352000, 0x3527F0, 0x3527E0]
    for base in base_candidates:
        for esize, name in [(4, "u32"), (8, "u64")]:
            off = base + 5 * esize
            if off + esize <= size:
                if esize == 4:
                    val = struct.unpack_from("<I", data, off)[0]
                else:
                    val = struct.unpack_from("<Q", data, off)[0]
                lines.append(f"base=0x{base:x} elem_size={esize} index5@0x{off:x} = 0x{val:x} ({name})")

    # search 112640 (0x1B800) constants and nearby file offsets in region
    target = 112640
    lines.append("")
    lines.append("=== nearby constants / size 112640 (0x1b800) ===")
    for off in range(LO, HI - 3, 4):
        v = struct.unpack_from("<I", data, off)[0]
        if v in (target, 0x1B800, 5):
            lines.append(f"0x{off:06x}: u32={v}")

    # pointer-like values in __const range (typical mach-o arm64: high 0x1000.... file offsets)
    lines.append("")
    lines.append("=== pointer-like u64 in region (file offset candidates) ===")
    for off in range(LO, HI - 7, 8):
        v = struct.unpack_from("<Q", data, off)[0]
        if 0x1000 <= v < size:
            lines.append(f"0x{off:06x}: file_ptr=0x{v:x}")
            if v + 8 <= size:
                head = data[v:v+8]
                lines.append(f"         -> head {head.hex()}")

    lines.append("")
    lines.append("=== slot-5 registration hypothesis ===")
    lines.append(
        "Region 0x3527fc sits in a dense tables/rodata block: likely compression builtin "
        "dispatch or dict-id -> {size, blob_offset} records. Index 5 (dict id=5) would "
        "select the 112640-byte ZSTD dictionary used for CT=2 / message blobs matching "
        "MesLocalID=4134. Runtime resolves via dict_resolve (slide+0x256a20) reading "
        "context table (slide+0x1e77a0) populated at user_data (slide+0x1e78e8)."
    )

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("wrote", OUT)

if __name__ == "__main__":
    main()
