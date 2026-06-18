from pathlib import Path
import lldb
import os
import struct

MAGIC5 = b"\x37\xa4\x30\xec\x05\x00\x00\x00"
MAGIC = b"\x37\xa4\x30\xec"
SIZES = [112640, 65536, 262144]
OUT = str(Path(__import__("os").environ.get("WECHAT_ZSTD_WORKSPACE", str(Path(__file__).resolve().parent.parent / "data"))))
KEYWORDS = [b"<msg>", b"<appmsg>", b"<title>", b"</msg>"]


def scan(debugger, command, result, internal_dict):
    target = debugger.GetSelectedTarget()
    proc = target.GetProcess()
    err = lldb.SBError()
    hits5 = []
    hits = []
    xml_hits = []
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
                for step in range(0, max(0, len(data) - 112640), 65536):
                    sl = data[step : step + 4096]
                    sc = sum(sl.count(k) for k in KEYWORDS)
                    if sc >= 4:
                        xml_hits.append((base + off + step, sc))
                off += n
        if end <= addr:
            break
        addr = end

    print(f"MAGIC5={len(hits5)} MAGIC={len(hits)} XML_CLUSTERS={len(xml_hits)}")
    dumped = 0
    seen = set()
    for h in hits5 + hits:
        if h in seen:
            continue
        seen.add(h)
        for sz in SIZES:
            data = proc.ReadMemory(h, sz, err)
            if err.Fail() or len(data) < sz:
                continue
            path = os.path.join(OUT, f"deep_cand_{hex(h)}_{sz}.bin")
            open(path, "wb").write(data)
            dumped += 1
            did = struct.unpack("<I", data[4:8])[0] if len(data) >= 8 else -1
            print(f"DUMP {path} dict_id={did}")

    for h, sc in sorted(xml_hits, key=lambda x: -x[1])[:30]:
        for sz in SIZES:
            data = proc.ReadMemory(h, sz, err)
            if err.Fail() or len(data) < sz:
                continue
            path = os.path.join(OUT, f"deep_xml_{hex(h)}_{sz}.bin")
            if os.path.exists(path):
                continue
            open(path, "wb").write(data)
            dumped += 1
            print(f"XML_DUMP {path} score={sc}")
    print(f"TOTAL_DUMPED={dumped}")


def __lldb_init_module(debugger, internal_dict):
    debugger.HandleCommand(
        "command script add -f _mem_deep_scan_lldb.scan scan_dict_deep"
    )
