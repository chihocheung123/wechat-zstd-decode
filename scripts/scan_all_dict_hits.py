from pathlib import Path
import lldb
import os

def __lldb_init_module(debugger, internal_dict):
    target = debugger.GetSelectedTarget()
    process = target.GetProcess()
    magic = b"\x37\xA4\x30\xEC"
    dump_size = 131072
    max_region = 1024 * 1024 * 1024
    out_dir = str(Path(__import__("os").environ.get("WECHAT_ZSTD_WORKSPACE", str(Path(__file__).resolve().parent.parent / "data"))))
    error = lldb.SBError()
    hits = []
    addr = 0
    while addr < (1 << 64) - 1:
        region = lldb.SBMemoryRegionInfo()
        if not process.GetMemoryRegionInfo(addr, region):
            break
        start = region.GetRegionBase()
        end = region.GetRegionEnd()
        size = end - start
        if region.IsReadable() and 0 < size < max_region:
            chunk = 4 * 1024 * 1024
            off = 0
            while off < size:
                n = min(chunk, size - off)
                data = process.ReadMemory(start + off, n, error)
                if error.Fail():
                    break
                pos = 0
                while True:
                    i = data.find(magic, pos)
                    if i < 0:
                        break
                    hits.append(start + off + i)
                    pos = i + 4
                off += n
        if end <= addr:
            break
        addr = end
    hits_path = os.path.join(out_dir, "mem_magic_hits.txt")
    with open(hits_path, "w") as f:
        for h in hits:
            f.write(hex(h) + "\n")
    print(f"magic_hits={len(hits)}")
    for h in hits:
        data = process.ReadMemory(h, dump_size, error)
        if error.Success() and data:
            fn = os.path.join(out_dir, f"dict_mem_{hex(h)}.bin")
            with open(fn, "wb") as f:
                f.write(data)
    print(f"dumped {len(hits)} candidate files")
