#!/usr/bin/env python3
"""Autonomous dict_5 recovery: memory scan + validate + optional lldb capture."""
from __future__ import annotations

import glob
import os
import struct
import subprocess
import sys
import time
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
MAGIC5 = MAGIC + b"\x05\x00\x00\x00"
SIZES = (112640, 65536, 262144, 131072)
BLOB_OFFS = (0, 4)
MARKERS = ("笙歌", "haha", "appmsg", "<msg>", "<appmsg>")


def log(msg: str) -> None:
    print(msg, flush=True)


def get_wechat_pid() -> int | None:
    try:
        out = subprocess.check_output(
            ["pgrep", "-lf", "WeChat-Debug.app/Contents/MacOS/WeChat"], text=True
        )
        for line in out.splitlines():
            if "WeChatAppEx" in line or "crashpad" in line:
                continue
            if "/MacOS/WeChat" in line:
                return int(line.split()[0])
    except (subprocess.CalledProcessError, ValueError, IndexError):
        pass
    return None


def try_decompress(blob: bytes, dict_data: bytes) -> tuple[bytes | None, str]:
    variants = [("raw", dict_data)]
    if dict_data[:4] == MAGIC:
        variants.append(("skip4", dict_data[4:]))
        variants.append(("skip8", dict_data[8:]))
    for vname, d in variants:
        for dtype in (
            zstd.DICT_TYPE_AUTO,
            zstd.DICT_TYPE_FULLDICT,
            zstd.DICT_TYPE_RAWCONTENT,
        ):
            try:
                cd = zstd.ZstdCompressionDict(d, dict_type=dtype)
            except Exception:
                continue
            for off in BLOB_OFFS:
                try:
                    out = zstd.ZstdDecompressor(dict_data=cd).decompress(blob[off:])
                    return out, f"{vname}/dtype{dtype}/off{off}"
                except Exception:
                    pass
    return None, ""


def validate_candidate(data: bytes, source: str) -> dict | None:
    blob = TARGET.read_bytes()
    out, how = try_decompress(blob, data)
    if out is None:
        return None
    text = out.decode("utf-8", errors="replace")
    hits = [m for m in MARKERS if m in text]
    if not hits and "<appmsg" not in text and "<msg" not in text:
        return None
    return {"source": source, "how": how, "hits": hits, "text": text, "data": data}


def validate_all_dumps() -> dict | None:
    patterns = [
        "real_dict_5*.bin",
        "dict_from_*.bin",
        "deep_cand_*.bin",
        "mem_id5_*.bin",
        "fw_cand_*.bin",
    ]
    seen: set[str] = set()
    for pat in patterns:
        for p in sorted(EXPORT.glob(pat)):
            key = str(p.resolve())
            if key in seen or p.is_symlink():
                continue
            seen.add(key)
            try:
                data = p.read_bytes()
            except OSError:
                continue
            if len(data) < 1024:
                continue
            for sz in SIZES:
                chunk = data[:sz] if len(data) >= sz else data
                r = validate_candidate(chunk, str(p.name))
                if r:
                    return r
    return None


def write_lldb_id5_scan() -> Path:
    script = r'''
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
'''
    p = EXPORT / "_lldb_id5_scan.py"
    p.write_text(script)
    return p


def run_memory_scan(pid: int) -> None:
    script = write_lldb_id5_scan()
    log(f"LLDB memory scan PID={pid}")
    proc = subprocess.run(
        [
            "lldb", "-b", "-p", str(pid),
            "-o", f"command script import {script}",
            "-o", "scan_magic_id5",
            "-o", "detach", "-o", "quit",
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    log(proc.stdout[-3000:] if len(proc.stdout) > 3000 else proc.stdout)
    if proc.stderr:
        log(proc.stderr[-1000:])


def trigger_wechat_ui() -> None:
    script = '''
tell application "WeChat-Debug" to activate
delay 1
tell application "System Events"
  tell process "WeChat"
    set frontmost to true
    keystroke "f" using command down
    delay 0.4
    keystroke "haha"
    delay 0.3
    key code 36
    delay 0.8
    repeat 40 times
      key code 125
      delay 0.15
    end repeat
    key code 53
  end tell
end tell
'''
    try:
        subprocess.run(["osascript", "-e", script], timeout=30, capture_output=True)
    except Exception as e:
        log(f"UI automation skipped: {e}")


def run_lldb_capture(pid: int, seconds: int = 90) -> None:
    log(f"LLDB capture {seconds}s with UI trigger")
    env = os.environ.copy()
    env["BATCH_CAPTURE_SECONDS"] = str(seconds)
    env["BATCH_CAPTURE_DETACH"] = "1"
    env["BATCH_CAPTURE_QUIT"] = "1"

    def ui_thread():
        time.sleep(3)
        trigger_wechat_ui()

    import threading
    t = threading.Thread(target=ui_thread, daemon=True)
    t.start()

    proc = subprocess.run(
        ["bash", str(EXPORT / "run_lldb_capture_90s.sh")],
        cwd=str(EXPORT),
        env=env,
        capture_output=True,
        text=True,
        timeout=seconds + 120,
    )
    log(proc.stdout[-2000:] if len(proc.stdout) > 2000 else proc.stdout)


def patch_frame_dict_id(blob: bytes, new_id: int) -> list[bytes]:
    """Try stripping/patching ZSTD frame dict ID field."""
    variants = [blob]
    b = bytearray(blob)
    # Frame descriptor byte 4; try clearing dict ID flag (bits 2-3)
    for desc_mask in (0xE7, 0xCF, 0x9F):  # clear dict ID flag combinations
        b2 = bytearray(blob)
        b2[4] = b2[4] & desc_mask
        variants.append(bytes(b2))
    # Try patching bytes 5-8 with various dict ids
    for off in (5, 6, 7, 8, 9):
        for did in (new_id, 0, 5):
            b3 = bytearray(blob)
            if off + 4 <= len(b3):
                struct.pack_into("<I", b3, off, did)
                variants.append(bytes(b3))
    return variants


def try_frame_patches(dict_data: bytes, blob: bytes) -> dict | None:
    did = struct.unpack("<I", dict_data[4:8])[0] if len(dict_data) >= 8 else 0
    for skip in (0, 8):
        raw = dict_data[skip:]
        for dtype in (zstd.DICT_TYPE_FULLDICT, zstd.DICT_TYPE_RAWCONTENT, zstd.DICT_TYPE_AUTO):
            try:
                cd = zstd.ZstdCompressionDict(raw, dict_type=dtype)
            except Exception:
                continue
            for patched in patch_frame_dict_id(blob, did):
                for off in BLOB_OFFS:
                    try:
                        out = zstd.ZstdDecompressor(dict_data=cd).decompress(patched[off:])
                        text = out.decode("utf-8", errors="replace")
                        if any(m in text for m in MARKERS) or "<appmsg" in text:
                            return {"text": text, "data": dict_data, "how": f"patch dtype={dtype}"}
                    except Exception:
                        pass
    return None


def main() -> int:
    if not TARGET.is_file():
        log(f"ERROR missing {TARGET}")
        return 1
    blob = TARGET.read_bytes()
    fp = zstd.get_frame_parameters(blob)
    log(f"target {len(blob)}B frame dict_id={fp.dict_id} content_size={fp.content_size}")

    pid = get_wechat_pid()
    if pid:
        run_memory_scan(pid)
        run_lldb_capture(pid, 90)
        run_memory_scan(pid)
    else:
        log("WeChat not running — skip LLDB")

    r = validate_all_dumps()
    if r:
        OUT_DICT.write_bytes(r["data"])
        log("SUCCESS " + r["source"] + " " + r["how"])
        log(r["text"])
        return 0

    # frame patch attempts on known dicts
    for p in EXPORT.glob("dict_from_*.bin"):
        rr = try_frame_patches(p.read_bytes(), blob)
        if rr:
            OUT_DICT.write_bytes(rr["data"])
            log("SUCCESS patch " + p.name)
            log(rr["text"])
            return 0

    log("FAILED — no working dictionary found")
    return 2


if __name__ == "__main__":
    sys.exit(main())
