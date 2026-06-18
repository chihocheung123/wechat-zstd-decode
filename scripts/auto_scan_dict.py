from pathlib import Path
import lldb
import os

def __lldb_init_module(debugger, internal_dict):
    target = debugger.GetSelectedTarget()
    process = target.GetProcess()

    magic = b"\x37\xA4\x30\xEC"
    dict_size = 112640
    max_region = 1024 * 1024 * 1024

    print("\n🔍 正在掃描微信記憶體尋找 ZSTD dict_5 特徵...")
    error = lldb.SBError()
    found_count = 0
    hits = []

    out_dir = str(Path(__import__("os").environ.get("WECHAT_ZSTD_WORKSPACE", str(Path(__file__).resolve().parent.parent / "data"))))
    if not os.path.exists(out_dir):
        os.makedirs(out_dir)

    addr = 0
    while addr < (1 << 64) - 1:
        region = lldb.SBMemoryRegionInfo()
        if not process.GetMemoryRegionInfo(addr, region):
            break
        start = region.GetRegionBase()
        end = region.GetRegionEnd()
        size = end - start
        if (
            region.IsReadable()
            and not region.IsExecutable()
            and 0 < size < max_region
        ):
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

    for h in hits:
        print(f"📍 發現符合 ZSTD Magic 的記憶體地址: {hex(h)}")
        data = process.ReadMemory(h, dict_size, error)
        if error.Success() and len(data) == dict_size:
            filename = f"dict_from_{hex(h)}.bin"
            filepath = os.path.join(out_dir, filename)
            with open(filepath, "wb") as f:
                f.write(data)
            print(f"   ✅ 已成功 Dump {dict_size} 字节 -> {filename}")
            found_count += 1

    print(f"\n🎉 掃描結束！共導出 {found_count} 個字典候選檔。（magic_hits={len(hits)}）")
