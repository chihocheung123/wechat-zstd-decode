#!/usr/bin/env python3
"""Dual-track ZSTD dict_5 hunter + validator for WeChat WCDB blobs."""
from __future__ import annotations

import os
import re
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import zstandard as zstd
except ImportError:
    print("pip install zstandard")
    sys.exit(1)

EXPORT = Path(__import__("os").environ.get("WECHAT_ZSTD_WORKSPACE", str(Path(__file__).resolve().parent.parent / "data")))
TARGET = EXPORT / "target_4134_from_db.blob"
OUT_DICT = EXPORT / "real_dict_5.bin"
MAGIC = b"\x37\xa4\x30\xec"
MAGIC_ID5 = MAGIC + b"\x05\x00\x00\x00"
DICT_SIZES = (112640, 65536, 262144, 131072, 8192)
BLOB_OFFSETS = (0, 4)
MARKERS = ("笙歌", "haha", "appmsg", "<msg>", "<appmsg>")
SKIP_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".html", ".json", ".jsonl",
    ".txt", ".md", ".py", ".sh", ".log", ".csv", ".sqlite", ".db", ".wal",
    ".plist", ".strings", ".pak", ".svg", ".css", ".js", ".map",
}

SCAN_ROOTS = [
    Path("/Users/patrickchiho/Applications/WeChat-Debug.app"),
    EXPORT,
    Path(
        "/Users/patrickchiho/Library/Containers/com.tencent.xinWeChat/Data"
        "/Library/Application Support/com.tencent.xinWeChat"
    ),
]


def log(msg: str) -> None:
    print(msg, flush=True)


def iter_scan_files() -> list[Path]:
    files: list[Path] = []
    seen: set[str] = set()
    for root in SCAN_ROOTS:
        if not root.exists():
            log(f"[skip] missing root {root}")
            continue
        for dp, _, fns in os.walk(root):
            for fn in fns:
                if Path(fn).suffix.lower() in SKIP_EXTS:
                    continue
                p = Path(dp) / fn
                try:
                    if not p.is_file():
                        continue
                    st = p.stat()
                    if st.st_size < 1024 or st.st_size > 512 * 1024 * 1024:
                        continue
                except OSError:
                    continue
                key = str(p.resolve())
                if key in seen:
                    continue
                seen.add(key)
                files.append(p)
    return files


def dict_variants(data: bytes) -> list[tuple[str, bytes]]:
    out: list[tuple[str, bytes]] = [("raw", data)]
    if len(data) >= 8 and data[:4] == MAGIC:
        out.append(("skip4", data[4:]))
        out.append(("skip8", data[8:]))
    return out


def try_decompress(blob: bytes, dict_data: bytes) -> tuple[bytes | None, str]:
    for vname, d in dict_variants(dict_data):
        for dtype in (
            zstd.DICT_TYPE_AUTO,
            zstd.DICT_TYPE_FULLDICT,
            zstd.DICT_TYPE_RAWCONTENT,
        ):
            try:
                cd = zstd.ZstdCompressionDict(d, dict_type=dtype)
            except Exception:
                continue
            for off in BLOB_OFFSETS:
                try:
                    out = zstd.ZstdDecompressor(dict_data=cd).decompress(blob[off:])
                    return out, f"{vname}/dtype{dtype}/off{off}"
                except Exception:
                    pass
    return None, ""


def is_valid_zstd_dict(data: bytes) -> bool:
    for vname, d in dict_variants(data):
        for dtype in (
            zstd.DICT_TYPE_AUTO,
            zstd.DICT_TYPE_FULLDICT,
            zstd.DICT_TYPE_RAWCONTENT,
        ):
            try:
                zstd.ZstdCompressionDict(d, dict_type=dtype)
                return True
            except Exception:
                pass
    return False


def check_candidate(blob: bytes, data: bytes, source: str) -> dict | None:
    out, how = try_decompress(blob, data)
    if out is None:
        return None
    text = out.decode("utf-8", errors="replace")
    hits = [m for m in MARKERS if m in text]
    if not hits:
        return None
    return {
        "source": source,
        "how": how,
        "hits": hits,
        "plaintext": text,
        "dict_data": data,
        "len": len(out),
    }


def scan_file_static(path: Path, blob: bytes) -> dict | None:
    try:
        data = path.read_bytes()
    except OSError as e:
        return None

    # Priority: exact magic + dict_id 5
    pos = 0
    while True:
        i = data.find(MAGIC_ID5, pos)
        if i < 0:
            break
        for sz in DICT_SIZES:
            if i + sz > len(data):
                continue
            chunk = data[i : i + sz]
            r = check_candidate(blob, chunk, f"static:{path}@0x{i:x}+id5 sz={sz}")
            if r:
                return r
        pos = i + 1

    # All magic hits — only if zstd accepts dict header
    pos = 0
    while True:
        i = data.find(MAGIC, pos)
        if i < 0:
            break
        for sz in DICT_SIZES:
            if i + sz > len(data):
                continue
            chunk = data[i : i + sz]
            if not is_valid_zstd_dict(chunk):
                continue
            r = check_candidate(blob, chunk, f"static:{path}@0x{i:x} sz={sz}")
            if r:
                return r
        pos = i + 1
    return None


def track_a_static(blob: bytes) -> dict | None:
    log("\n=== Track A: static disk search ===")
    files = iter_scan_files()
    log(f"scanning {len(files)} files")
    tested = 0
    for p in files:
        tested += 1
        if tested % 200 == 0:
            log(f"  progress {tested}/{len(files)}")
        r = scan_file_static(p, blob)
        if r:
            return r
    log("Track A: no matching dictionary")
    return None


def get_wechat_pid() -> int | None:
    try:
        out = subprocess.check_output(["pgrep", "-x", "WeChat"], text=True)
        for line in out.strip().splitlines():
            pid = int(line.strip())
            return pid
    except (subprocess.CalledProcessError, ValueError):
        pass
    try:
        out = subprocess.check_output(["pgrep", "-lf", "WeChat"], text=True)
        for line in out.splitlines():
            if "MacOS/WeChat" in line and "WeChatAppEx" not in line and "crashpad" not in line:
                pid = int(line.split()[0])
                return pid
    except (subprocess.CalledProcessError, ValueError):
        pass
    return None


def write_lldb_scan_script() -> Path:
    script = r'''
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
'''
    p = EXPORT / "_lldb_mem_scan.py"
    p.write_text(script)
    return p


def track_b_memory(blob: bytes) -> dict | None:
    log("\n=== Track B: dynamic memory scan ===")
    pid = get_wechat_pid()
    if pid is None:
        log("WeChat not running — skip memory scan")
        return None
    log(f"attached target PID {pid}")
    script = write_lldb_scan_script()
    try:
        proc = subprocess.run(
            ["lldb", "-b", "-P", str(script), "--", str(pid)],
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        log("LLDB scan timed out")
        return None
    log(proc.stdout[-4000:] if len(proc.stdout) > 4000 else proc.stdout)
    if proc.stderr:
        log(proc.stderr[-2000:])

    cands = sorted(EXPORT.glob("mem_cand_*.bin"))
    log(f"memory candidates dumped: {len(cands)}")
    for p in cands:
        data = p.read_bytes()
        r = check_candidate(blob, data, f"memory:{p.name}")
        if r:
            r["source"] = f"memory:{p.name} (pid={pid})"
            return r
    log("Track B: no matching dictionary")
    return None


def main() -> int:
    if not TARGET.is_file():
        log(f"ERROR missing {TARGET}")
        return 1
    blob = TARGET.read_bytes()
    log(f"target blob: {TARGET} ({len(blob)} bytes)")
    log(f"frame magic: {blob[:4].hex()}")

    for track in (track_a_static, track_b_memory):
        r = track(blob)
        if r:
            OUT_DICT.write_bytes(r["dict_data"])
            log("\n" + "=" * 60)
            log("SUCCESS")
            log(f"source: {r['source']}")
            log(f"method: {r['how']}")
            log(f"markers: {r['hits']}")
            log(f"decompressed length: {r['len']}")
            log(f"wrote: {OUT_DICT}")
            log("=" * 60)
            log("\n--- RECOVERED PLAINTEXT ---\n")
            log(r["plaintext"])
            log("\n--- END PLAINTEXT ---")
            return 0

    log("\nFAILED: no dictionary produced matching plaintext")
    log("If WeChat is open, scroll 米迷聊天室 chat to load dict into memory and re-run.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
