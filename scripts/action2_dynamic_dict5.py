#!/usr/bin/env python3
"""Action 2: 90s LLDB memory scan for ZSTD dict_id=5."""
from __future__ import annotations

from pathlib import Path

import glob
import os
import struct
import subprocess
import sys
import tempfile
import time
from datetime import datetime

try:
    import zstandard as zstd
except ImportError:
    print("pip install zstandard")
    sys.exit(1)

EXPORT = str(Path(__import__("os").environ.get("WECHAT_ZSTD_WORKSPACE", str(Path(__file__).resolve().parent.parent / "data"))))
LOG = os.path.join(EXPORT, "action2_dynamic_dict5.log")
OUT_DICT = os.path.join(EXPORT, "real_dict_5.bin")
MAGIC5 = b"\x37\xa4\x30\xec\x05\x00\x00\x00"
MAGIC4 = b"\x37\xa4\x30\xec"
SIZES = [112640, 65536, 262144]
BLOB_OFFSETS = (0, 4)
SCAN_SECONDS = 90
SCAN_INTERVAL = 5

TEST_BLOBS = {
    "target_4134_from_db.blob": os.path.join(EXPORT, "target_4134_from_db.blob"),
    "example_1776683436.blob": os.path.join(EXPORT, "example_1776683436.blob"),
    "example_1778132396.blob": os.path.join(EXPORT, "example_1778132396.blob"),
}
MARKERS = ("笙歌", "haha", "appmsg", "title", "<msg", "<appmsg", "哈哈", "米迷")

USER_INSTRUCTION = (
    "「請立刻在 Mac 微信中點擊：左下角選單 -> 備份與遷移 (Backup and Migration) "
    "-> 遷移 (Migrate) 或 備份 (Backup) 介面」"
)


def log(msg: str = "") -> None:
    line = f"[{datetime.now().isoformat()}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def find_wechat_pid() -> int | None:
    try:
        out = subprocess.check_output(
            ["pgrep", "-lf", "WeChat-Debug.app/Contents/MacOS/WeChat"], text=True
        )
    except subprocess.CalledProcessError:
        return None
    for line in out.splitlines():
        if "WeChatAppEx" in line or "crashpad" in line:
            continue
        if "/MacOS/WeChat" in line:
            return int(line.split()[0])
    return None


def try_decompress(blob: bytes, dict_data: bytes, off: int) -> tuple[bytes | None, str | None]:
    for dtype in (zstd.DICT_TYPE_AUTO, zstd.DICT_TYPE_FULLDICT, zstd.DICT_TYPE_RAWCONTENT):
        try:
            cd = zstd.ZstdCompressionDict(dict_data, dict_type=dtype)
            out = zstd.ZstdDecompressor(dict_data=cd).decompress(blob[off:])
            return out, None
        except Exception:
            pass
    return None, "all dict types failed"


def validate_dict(dict_data: bytes, label: str) -> bool:
    any_ok = False
    for blob_name, blob_path in TEST_BLOBS.items():
        if not os.path.isfile(blob_path):
            log(f"  SKIP missing {blob_path}")
            continue
        blob = open(blob_path, "rb").read()
        for off in BLOB_OFFSETS:
            out, err = try_decompress(blob, dict_data, off)
            if out is None:
                log(f"  FAIL {label} vs {blob_name} off={off}: {err}")
                continue
            text = out.decode("utf-8", errors="replace")
            hits = [m for m in MARKERS if m in text]
            if hits:
                any_ok = True
                log(f"  OK {label} vs {blob_name} off={off} len={len(out)} markers={hits}")
                preview = text[:600].replace("\n", "\\n")
                log(f"  PLAINTEXT: {preview}")
            else:
                log(f"  WEAK {label} vs {blob_name} off={off} len={len(out)} (no markers)")
    return any_ok


LLDB_SCAN_MODULE = os.path.join(EXPORT, "_action2_id5_scan.py")


def write_lldb_module() -> None:
    content = r'''import lldb, struct, os, time, re

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
'''
    with open(LLDB_SCAN_MODULE, "w") as f:
        f.write(content)


def run_lldb_scan(pid: int) -> str:
    write_lldb_module()
    lldb_cmds = f"""
settings set auto-confirm true
process attach --pid {pid}
command script import "{LLDB_SCAN_MODULE}"
run_90s_scan
detach
quit
"""
    fd, cmdfile = tempfile.mkstemp(suffix=".lldb", dir=EXPORT)
    os.close(fd)
    with open(cmdfile, "w") as f:
        f.write(lldb_cmds)
    try:
        proc = subprocess.run(
            ["lldb", "-b", "-s", cmdfile],
            capture_output=True,
            text=True,
            timeout=SCAN_SECONDS + 180,
        )
        return proc.stdout + "\n" + proc.stderr
    finally:
        os.unlink(cmdfile)


def main() -> int:
    open(LOG, "w").close()
    log("=== Action 2: Dynamic memory capture ===")
    log(USER_INSTRUCTION)
    print("\n" + "=" * 70, flush=True)
    print(USER_INSTRUCTION, flush=True)
    print("=" * 70 + "\n", flush=True)

    pid = find_wechat_pid()
    if pid is None:
        log("BLOCKER: WeChat-Debug not running")
        return 2
    log(f"WeChat-Debug PID={pid}")
    log(f"Starting {SCAN_SECONDS}s LLDB memory scan...")

    output = run_lldb_scan(pid)
    for line in output.splitlines():
        log(line)

    patterns = [
        os.path.join(EXPORT, "mem_id5_*.bin"),
        os.path.join(EXPORT, "bp_*.bin"),
        os.path.join(EXPORT, "pat_cand_*.bin"),
        os.path.join(EXPORT, "real_dict_5*.bin"),
    ]
    candidates = []
    for pat in patterns:
        candidates.extend(glob.glob(pat))
    candidates = sorted(set(candidates))
    log(f"\nValidating {len(candidates)} candidate dumps...")
    solved = False
    best_data = None
    best_label = ""
    for path in candidates:
        data = open(path, "rb").read()
        if len(data) < 8192:
            continue
        label = os.path.basename(path)
        if validate_dict(data, label):
            solved = True
            chunk = data[:112640] if len(data) >= 112640 else data
            if best_data is None or len(chunk) > len(best_data):
                best_data = chunk
                best_label = label

    if solved and best_data:
        with open(OUT_DICT, "wb") as f:
            f.write(best_data)
        log(f"\nVERDICT: SOLVED — wrote {OUT_DICT} from {best_label}")
        return 0

    log("\nVERDICT: NOT SOLVED — dict_id=5 not captured in memory during 90s window")
    log("NEXT: User must open Backup/Migration UI during scan, then re-run action2")
    return 1


if __name__ == "__main__":
    sys.exit(main())
