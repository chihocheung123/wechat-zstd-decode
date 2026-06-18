from pathlib import Path
import lldb, struct, os, sys
MAGIC = b"\x37\xa4\x30\xec"
ID5 = MAGIC + b"\x05\x00\x00\x00"
OUT = str(Path(__import__("os").environ.get("WECHAT_ZSTD_WORKSPACE", str(Path(__file__).resolve().parent.parent / "data"))))
pid = int(sys.argv[1]) if len(sys.argv) > 1 else 0

def scan(debugger, command, result, internal_dict):
    target = debugger.GetSelectedTarget()
    process = target.GetProcess()
    if not process.IsValid():
        print("NO_PROCESS")
        return
    err = lldb.SBError()
    magic_hits = []
    id5_hits = []
    addr = 0
    regions = 0
    while addr < (1 << 64) - 1:
        region = lldb.SBMemoryRegionInfo()
        if not process.GetMemoryRegionInfo(addr, region):
            break
        base, end = region.GetRegionBase(), region.GetRegionEnd()
        size = end - base
        if region.IsReadable() and 0 < size < 500*1024*1024:
            regions += 1
            chunk = 4*1024*1024
            off = 0
            while off < size:
                n = min(chunk, size-off)
                data = process.ReadMemory(base+off, n, err)
                if err.Fail():
                    break
                pos = 0
                while True:
                    i = data.find(MAGIC, pos)
                    if i < 0:
                        break
                    magic_hits.append(base+off+i)
                    pos = i + 1
                pos = 0
                while True:
                    i = data.find(ID5, pos)
                    if i < 0:
                        break
                    id5_hits.append(base+off+i)
                    pos = i + 1
                off += n
        if end <= addr:
            break
        addr = end
    print(f"REGIONS {regions} MAGIC {len(magic_hits)} ID5 {len(id5_hits)}")
    dumped = []
    for h in id5_hits + [x for x in magic_hits if x not in id5_hits]:
        hdr = process.ReadMemory(h, 8, err)
        did = struct.unpack("<I", hdr[4:8])[0] if not err.Fail() and len(hdr)>=8 else -1
        for sz in (112640, 131072, 65536):
            data = process.ReadMemory(h, sz, err)
            if err.Fail() or len(data) < sz:
                continue
            tag = "id5" if h in id5_hits else "magic"
            fn = f"mem_cand_{tag}_{hex(h)}_{sz}.bin"
            path = os.path.join(OUT, fn)
            open(path, "wb").write(data)
            dumped.append((path, h, sz, did))
            print(f"DUMP {fn} addr={hex(h)} sz={sz} dict_id={did}")
    with open(os.path.join(OUT, "agent_dictid5_scan.txt"), "w") as f:
        f.write(f"magic={len(magic_hits)} id5={len(id5_hits)} dumped={len(dumped)}\n")
        for d in dumped:
            f.write(str(d)+"\n")

def __lldb_init_module(debugger, internal_dict):
    debugger.HandleCommand("command script add -f lldb_dictid5_scan.scan dictid5_scan")
