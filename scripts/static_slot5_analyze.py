#!/usr/bin/env python3
import struct
from pathlib import Path

DYLIB = Path('/tmp/roam_migration_arm64.dylib')
REPORT = Path('str(WORKSPACE)/static_slot5_table_report.txt')
VA_SLOT = 0x3527FC
VA_CONST_REGION = 0x3A0000

def load_sections():
    import subprocess
    out = subprocess.check_output(['otool', '-l', str(DYLIB)], text=True)
    sects = []
    cur = None
    for ln in out.splitlines():
        t = ln.strip()
        if t.startswith('sectname '):
            cur = {'name': t.split()[-1]}
        elif cur is not None and t.startswith('addr '):
            cur['addr'] = int(t.split()[-1], 16)
        elif cur is not None and t.startswith('size '):
            cur['size'] = int(t.split()[-1], 16)
        elif cur is not None and t.startswith('offset ') and 'align' not in t:
            cur['offset'] = int(t.split()[-1])
            if 'addr' in cur and 'size' in cur:
                sects.append(cur)
            cur = None
    return sects

def section_for_va(data, va):
    for s in load_sections():
        if s['addr'] <= va < s['addr'] + s['size']:
            fo = s['offset'] + (va - s['addr'])
            return s, fo
    return None, None

def main():
    data = DYLIB.read_bytes()
    s, fo = section_for_va(data, VA_SLOT)
    lines = []
    lines.append(f"=== static slot5 analyze {DYLIB} ===")
    lines.append(f"VA 0x{VA_SLOT:x} section={s} fileoff=0x{fo:x}" if s else "section NOT FOUND")
    if fo is not None:
        chunk = data[fo:fo+0x200]
        lines.append("hex dump 0x200 bytes at slot table:")
        for i in range(0, len(chunk), 16):
            lines.append(f"  {VA_SLOT+i:08x}: {chunk[i:i+16].hex()}")
        # parse as uint32 table
        u32 = struct.unpack('<' + 'I' * (len(chunk)//4), chunk[: (len(chunk)//4)*4])
        hits5 = [i for i,v in enumerate(u32) if v == 5]
        lines.append(f"uint32 entries == 5: indices {hits5[:20]} count={len(hits5)}")
        # parse as uint64 pointers into __DATA/__const
        u64 = struct.unpack('<' + 'Q' * (len(chunk)//8), chunk[: (len(chunk)//8)*8])
        ptr_like = [(i,v) for i,v in enumerate(u64) if 0x300000 <= v <= 0x400000]
        lines.append(f"uint64 pointer-like in 0x300000-0x400000: {ptr_like[:12]}")
    # scan for 112640-byte zlib-ish blobs: look for size constants and dict id 5 nearby
    target_len = 112640
    lines.append(f"\n=== scan for ~{target_len} blobs near VA 0x{VA_CONST_REGION:x} ===")
    # __DATA_CONST 0x360000-0x39c000, __DATA 0x39c000+
    regions = [(0x309120, 0x4d758, 'TEXT __const'), (0x360000, 0x3c000, 'DATA_CONST'), (0x39c000, 0xc000, 'DATA')]
    for base, size, name in regions:
        s2, fo2 = section_for_va(data, base)
        if fo2 is None:
            continue
        reg = data[fo2:fo2+size]
        # find occurrences of uint32 value 5 followed within 64 bytes by size-like 112640
        for i in range(0, len(reg)-8, 4):
            if struct.unpack_from('<I', reg, i)[0] == 5:
                window = reg[i:i+128]
                if any(struct.unpack_from('<I', window, j)[0] == target_len for j in range(0, 120, 4)):
                    lines.append(f"  id5+size hint at {name}+{i:x} (va 0x{base+i:x})")
        # entropy / not all zero 112640 windows stepped by 4k
        for step in range(0, max(0, len(reg)-target_len), 4096):
            sl = reg[step:step+target_len]
            if sl.count(0) > target_len * 0.98:
                continue
            nz = len(set(sl[:4096]))
            if nz > 50:
                lines.append(f"  non-trivial {target_len}B window {name}+0x{step:x} va=0x{base+step:x} uniq={nz}")

    REPORT.write_text(REPORT.read_text() if REPORT.exists() else "")
    REPORT.write_text((REPORT.read_text() if REPORT.exists() else "") + "\n".join(lines) + "\n")
    print("\n".join(lines[-15:]))

if __name__ == '__main__':
    main()
