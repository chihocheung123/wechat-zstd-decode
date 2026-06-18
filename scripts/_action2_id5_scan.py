from pathlib import Path
import lldb, struct, os, time, re

MAGIC5 = b"\x37\xa4\x30\xec\x05\x00\x00\x00"
MAGIC4 = b"\x37\xa4\x30\xec"
SIZES = [112640, 65536, 262144]
OUT = str(Path(__import__("os").environ.get("WECHAT_ZSTD_WORKSPACE", str(Path(__file__).resolve().parent.parent / "data"))))
SCAN_SECONDS = 90
SCAN_INTERVAL = 5

def scan_once(proc, err, seen):
    hits5, hits_magic = [], []
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
                for pat, bucket in ((MAGIC5, hits5), (MAGIC4, hits_magic)):
                    start = 0
                    while True:
                        i = data.find(pat, start)
                        if i < 0:
                            break
                        addr_hit = base+off+i
                        if addr_hit not in seen:
                            bucket.append(addr_hit)
                            seen.add(addr_hit)
                        start = i+1
                off += n
        if end <= addr:
            break
        addr = end
    return hits5, hits_magic

def run_90s_scan(debugger, command, result, internal_dict):
    proc = debugger.GetSelectedTarget().GetProcess()
    if not proc.IsValid():
        print("NO_PROCESS")
        return
  # roam_migration breakpoints
    target = debugger.GetSelectedTarget()
    ROAM_OFFSETS = [("dict_resolve", 0x256A20), ("decompress_entry", 0x2715E0)]
    slide = None
    res = lldb.SBCommandReturnObject()
    debugger.GetCommandInterpreter().HandleCommand("image list -o -f roam_migration", res)
    for line in (res.GetOutput() or "").splitlines():
        if "roam_migration" in line:
            m = re.match(r"\[\s*\d+\]\s+(0x[0-9a-fA-F]+)", line.strip())
            if m:
                slide = int(m.group(1), 16)
                break
    if slide:
        for name, off in ROAM_OFFSETS:
            target.BreakpointCreateByAddress(slide + off)
        print(f"ROAM_BP slide=0x{slide:x} count={len(ROAM_OFFSETS)}")
    else:
        print("ROAM_BP not_loaded")

    seen = set()
    all_magic_other = []
    start = time.time()
    round_n = 0
    err = lldb.SBError()
    while time.time() - start < SCAN_SECONDS:
        round_n += 1
        hits5, hits_magic = scan_once(proc, err, seen)
        for h in hits_magic:
            hdr = proc.ReadMemory(h, 8, err)
            did = struct.unpack("<I", hdr[4:8])[0] if len(hdr) >= 8 else -1
            if did != 5:
                all_magic_other.append((h, did))
        print(f"ROUND={round_n} MAGIC5_NEW={len(hits5)} elapsed={int(time.time()-start)}s")
        for h in hits5:
            for sz in SIZES:
                data = proc.ReadMemory(h, sz, err)
                if err.Fail() or len(data) < sz:
                    continue
                path = os.path.join(OUT, f"mem_id5_{h:x}_{sz}.bin")
                open(path, "wb").write(data)
                print(f"DUMP_ID5 {path}")
        if proc.GetState() == lldb.eStateStopped:
            proc.Continue()
        time.sleep(SCAN_INTERVAL)

    print(f"TOTAL_MAGIC5_DUMPS done")
    for h, did in all_magic_other[:40]:
        print(f"MAGIC_HIT addr=0x{h:x} dict_id={did}")

def __lldb_init_module(debugger, internal_dict):
    debugger.HandleCommand("command script add -f _action2_id5_scan.run_90s_scan run_90s_scan")
