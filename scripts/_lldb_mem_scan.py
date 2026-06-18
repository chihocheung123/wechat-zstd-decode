from pathlib import Path

import lldb, struct, os, sys
MAGIC = b"\x37\xa4\x30\xec"
SIZES = [112640, 65536, 262144]
OUT = str(Path(__import__("os").environ.get("WECHAT_ZSTD_WORKSPACE", str(Path(__file__).resolve().parent.parent / "data"))))
pid = int(sys.argv[1])
debugger = lldb.SBDebugger.Create()
debugger.SetAsync(False)
target = debugger.CreateTarget("")
err = lldb.SBError()
debugger.HandleCommand(f"process attach --pid {pid}")
process = target.GetProcess()
if not process.IsValid():
    print("ATTACH_FAIL")
    sys.exit(2)
hits = []
addr = 0
while addr < (1 << 64) - 1:
    region = lldb.SBMemoryRegionInfo()
    if not process.GetMemoryRegionInfo(addr, region):
        break
    base, end = region.GetRegionBase(), region.GetRegionEnd()
    size = end - base
    if region.IsReadable() and 0 < size < 500*1024*1024:
        chunk = 4*1024*1024
        off = 0
        while off < size:
            n = min(chunk, size-off)
            data = process.ReadMemory(base+off, n, err)
            if err.Fail():
                break
            start = 0
            while True:
                i = data.find(MAGIC, start)
                if i < 0:
                    break
                hits.append(base+off+i)
                start = i+1
            off += n
    if end <= addr:
        break
    addr = end
print(f"MAGIC_HITS {len(hits)}")
for h in hits:
    did = -1
    hdr = process.ReadMemory(h, 8, err)
    if not err.Fail() and len(hdr)>=8:
        did = struct.unpack("<I", hdr[4:8])[0]
    for sz in SIZES:
        data = process.ReadMemory(h, sz, err)
        if err.Fail() or len(data) < sz:
            continue
        path = os.path.join(OUT, f"mem_cand_{hex(h)}_{sz}.bin")
        open(path, "wb").write(data)
        print(f"DUMP {path} addr={hex(h)} sz={sz} dict_id={did}")
debugger.HandleCommand("detach")
debugger.Destroy()
