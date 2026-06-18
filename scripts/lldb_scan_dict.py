from pathlib import Path
import lldb
import struct
import os

MAGIC = b"\x37\xa4\x30\xec"
DICT_SIZE = 112640
OUT_DIR = str(Path(__import__("os").environ.get("WECHAT_ZSTD_WORKSPACE", str(Path(__file__).resolve().parent.parent / "data"))))


def scan_dict(debugger, command, result, internal_dict):
    target = debugger.GetSelectedTarget()
    process = target.GetProcess()
    if not process.IsValid():
        result.AppendMessage("no valid process")
        return
    error = lldb.SBError()
    hits = []
    addr = 0
    regions_scanned = 0
    bytes_scanned = 0
    while addr < (1 << 64) - 1:
        region = lldb.SBMemoryRegionInfo()
        if not process.GetMemoryRegionInfo(addr, region):
            break
        end = region.GetRegionEnd()
        base = region.GetRegionBase()
        size = end - base
        if region.IsReadable() and size > 0 and size < 512 * 1024 * 1024:
            chunk = 4 * 1024 * 1024
            off = 0
            while off < size:
                n = min(chunk, size - off)
                data = process.ReadMemory(base + off, n, error)
                if error.Fail():
                    break
                bytes_scanned += len(data)
                start = 0
                while True:
                    i = data.find(MAGIC, start)
                    if i < 0:
                        break
                    hits.append(base + off + i)
                    start = i + 1
                off += n
            regions_scanned += 1
        if end <= addr:
            break
        addr = end

    result.AppendMessage(f"regions_scanned={regions_scanned} bytes_scanned={bytes_scanned} magic_hits={len(hits)}")

    dict5 = []
    other = []
    for h in hits:
        hdr = process.ReadMemory(h, 8, error)
        if error.Fail() or len(hdr) < 8:
            continue
        magic, dict_id = hdr[:4], struct.unpack("<I", hdr[4:8])[0]
        if magic != MAGIC:
            continue
        entry = (h, dict_id)
        if dict_id == 5:
            dict5.append(entry)
        else:
            other.append(entry)

    result.AppendMessage(f"dict_id5_hits={len(dict5)} other_header_hits={len(other)}")

    def dump_at(path, h, nbytes):
        data = process.ReadMemory(h, nbytes, error)
        if error.Fail():
            data = process.ReadMemory(h, nbytes, error)
        actual = len(data) if not error.Fail() else 0
        if actual == 0:
            result.AppendMessage(f"dump failed {path} @ {hex(h)}")
            return
        with open(path, "wb") as f:
            f.write(data)
        result.AppendMessage(f"wrote {path} {actual} bytes @ {hex(h)}")

    for idx, (h, did) in enumerate(dict5):
        suffix = "" if idx == 0 else f"_{idx}"
        dump_at(os.path.join(OUT_DIR, f"dict_5_dump{suffix}.bin"), h, DICT_SIZE)

    extra = 0
    for h, did in other:
        if extra >= 3:
            break
        dump_at(os.path.join(OUT_DIR, f"dict_id{did}_dump_cmp{extra}.bin"), h, DICT_SIZE)
        extra += 1


def __lldb_init_module(debugger, internal_dict):
    debugger.HandleCommand("command script add -f lldb_scan_dict.scan_dict scan_wcdb_dict")
