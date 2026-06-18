from pathlib import Path

import lldb, struct, os
MAGIC5 = b"\x37\xa4\x30\xec\x05\x00\x00\x00"
MAGIC = b"\x37\xa4\x30\xec"
SIZES = [112640, 65536, 262144]
OUT = str(Path(__import__("os").environ.get("WECHAT_ZSTD_WORKSPACE", str(Path(__file__).resolve().parent.parent / "data"))))

def scan_id5(debugger, command, result, internal_dict):
    proc = debugger.GetSelectedTarget().GetProcess()
    err = lldb.SBError()
    hits5, hits = [], []
    addr = 0
    while addr < (1 << 64) - 1:
        region = lldb.SBMemoryRegionInfo()
        if not proc.GetMemoryRegionInfo(addr, region):
            break
        base, end = region.GetRegionBase(), region.GetRegionEnd()
        size = end - base
        if region.IsReadable() and 0 < size < 500*1024*1024:
            chunk = 4*1024*1024
            off = 0
            while off < size:
                n = min(chunk, size-off)
                data = proc.ReadMemory(base+off, n, err)
                if err.Fail():
                    break
                for pat, bucket in ((MAGIC5, hits5), (MAGIC, hits)):
                    start = 0
                    while True:
                        i = data.find(pat, start)
                        if i < 0:
                            break
                        bucket.append(base+off+i)
                        start = i+1
                off += n
        if end <= addr:
            break
        addr = end
    print(f"PID={proc.GetProcessID()} MAGIC5={len(hits5)} MAGIC={len(hits)}")
    for h in hits5:
        for sz in SIZES:
            data = proc.ReadMemory(h, sz, err)
            if err.Fail() or len(data) < sz:
                continue
            path = os.path.join(OUT, f"mem_id5_{hex(h)}_{sz}.bin")
            open(path, "wb").write(data)
            did = struct.unpack("<I", data[4:8])[0] if len(data)>=8 else -1
            print(f"ID5_DUMP {path} dict_id={did}")

def __lldb_init_module(debugger, internal_dict):
    debugger.HandleCommand("command script add -f _lldb_id5_scan.scan_id5 scan_magic_id5")
