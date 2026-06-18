from pathlib import Path
import lldb
import os
import struct

OUT = str(Path(__import__("os").environ.get("WECHAT_ZSTD_WORKSPACE", str(Path(__file__).resolve().parent.parent / "data"))))
MAGIC5 = b"\x37\xa4\x30\xec\x05\x00\x00\x00"
MAGIC = b"\x37\xa4\x30\xec"
SIZE_112640 = struct.pack("<I", 112640)
SIZES = [112640, 65536, 262144]


def scan_patterns(debugger, command, result, internal_dict):
    proc = debugger.GetSelectedTarget().GetProcess()
    err = lldb.SBError()
    hits5, hits, size_hints, ptr_hits = [], [], [], []
    addr = 0
    while addr < (1 << 64) - 1:
        region = lldb.SBMemoryRegionInfo()
        if not proc.GetMemoryRegionInfo(addr, region):
            break
        base, end = region.GetRegionBase(), region.GetRegionEnd()
        size = end - base
        if region.IsReadable() and 0 < size < 500 * 1024 * 1024:
            chunk = 4 * 1024 * 1024
            off = 0
            while off < size:
                n = min(chunk, size - off)
                data = proc.ReadMemory(base + off, n, err)
                if err.Fail():
                    break
                for pat, bucket in ((MAGIC5, hits5), (MAGIC, hits)):
                    start = 0
                    while True:
                        i = data.find(pat, start)
                        if i < 0:
                            break
                        bucket.append(base + off + i)
                        start = i + 1
                # id=5 near size 112640
                start = 0
                while True:
                    i = data.find(b"\x05\x00\x00\x00", start)
                    if i < 0:
                        break
                    window = data[i : i + 128]
                    if SIZE_112640 in window or b"\x00\xb8\x01\x00" in window:
                        size_hints.append(base + off + i)
                    start = i + 4
                # possible pointers into heap dict (0x126xxxxxx range)
                for align in range(0, len(data) - 8, 8):
                    v = struct.unpack_from("<Q", data, align)[0]
                    if 0x126000000 <= v <= 0x127000000:
                        ptr_hits.append((base + off + align, v))
                off += n
        if end <= addr:
            break
        addr = end

    print(
        f"MAGIC5={len(hits5)} MAGIC={len(hits)} "
        f"size_hints={len(size_hints)} ptr_hits={len(ptr_hits)}"
    )
    seen = set()
    for h in hits5 + hits + size_hints:
        if h in seen:
            continue
        seen.add(h)
        for sz in SIZES:
            data = proc.ReadMemory(h, sz, err)
            if err.Fail() or len(data) < sz:
                continue
            path = os.path.join(OUT, f"pat_cand_{hex(h)}_{sz}.bin")
            open(path, "wb").write(data)
            did = struct.unpack("<I", data[4:8])[0] if len(data) >= 8 else -1
            print(f"DUMP {path} dict_id={did}")

    for loc, ptr in ptr_hits[:20]:
        for sz in SIZES:
            data = proc.ReadMemory(ptr, sz, err)
            if err.Fail() or len(data) < sz:
                continue
            if data[:4] == MAGIC:
                path = os.path.join(OUT, f"ptr_cand_{hex(ptr)}_{sz}.bin")
                open(path, "wb").write(data)
                did = struct.unpack("<I", data[4:8])[0] if len(data) >= 8 else -1
                print(f"PTR_DUMP {path} from={hex(loc)} dict_id={did}")


def __lldb_init_module(debugger, internal_dict):
    debugger.HandleCommand(
        "command script add -f _lldb_pattern_scan.scan_patterns scan_patterns"
    )
