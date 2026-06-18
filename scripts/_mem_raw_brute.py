from pathlib import Path
import lldb
import os
import struct

OUT = str(Path(__import__("os").environ.get("WECHAT_ZSTD_WORKSPACE", str(Path(__file__).resolve().parent.parent / "data"))))
BLOB_PATH = os.path.join(OUT, "target_4134_from_db.blob")
DICT_SIZE = 112640
STEP = 4096


def scan_raw(debugger, command, result, internal_dict):
    import zstandard as zstd

    blob = open(BLOB_PATH, "rb").read()
    target = debugger.GetSelectedTarget()
    proc = target.GetProcess()
    err = lldb.SBError()
    tested = 0
    addr = 0
    while addr < (1 << 64) - 1:
        region = lldb.SBMemoryRegionInfo()
        if not proc.GetMemoryRegionInfo(addr, region):
            break
        base, end = region.GetRegionBase(), region.GetRegionEnd()
        size = end - base
        if region.IsReadable() and DICT_SIZE < size < 500 * 1024 * 1024:
            off = 0
            while off + DICT_SIZE <= size:
                data = proc.ReadMemory(base + off, DICT_SIZE, err)
                if not err.Fail() and len(data) == DICT_SIZE:
                    if data.count(0) < DICT_SIZE * 0.85:
                        tested += 1
                        try:
                            cd = zstd.ZstdCompressionDict(
                                data, dict_type=zstd.DICT_TYPE_RAWCONTENT
                            )
                            out = zstd.ZstdDecompressor(dict_data=cd).decompress(blob)
                            path = os.path.join(OUT, "real_dict_5.bin")
                            open(path, "wb").write(data)
                            txt = out.decode("utf-8", errors="replace")
                            print(f"SUCCESS addr=0x{base + off:x} len={len(out)}")
                            print(txt[:4000])
                            return
                        except Exception:
                            pass
                off += STEP
        if end <= addr:
            break
        addr = end
    print(f"NO_MATCH tested={tested}")


def __lldb_init_module(debugger, internal_dict):
    debugger.HandleCommand(
        "command script add -f _mem_raw_brute.scan_raw scan_raw_dict"
    )
