#!/usr/bin/env python3
"""Static extraction of ZSTD dict_id=5 from WeChat-Debug binaries."""
from __future__ import annotations

from pathlib import Path

import os
import struct
import sys
from datetime import datetime

try:
    import zstandard as zstd
except ImportError:
    print("ERROR: pip install zstandard")
    sys.exit(1)

EXPORT = str(Path(__import__("os").environ.get("WECHAT_ZSTD_WORKSPACE", str(Path(__file__).resolve().parent.parent / "data"))))
LOG_PATH = os.path.join(EXPORT, "extract_static_dict5.log")

BINARIES = [
    "/Users/patrickchiho/Applications/WeChat-Debug.app/Contents/MacOS/WeChat",
    "/Users/patrickchiho/Applications/WeChat-Debug.app/Contents/Frameworks/roam_migration.framework/Versions/A/roam_migration",
    "/Users/patrickchiho/Applications/WeChat-Debug.app/Contents/Frameworks/WCDB.framework/Versions/A/WCDB",
]

MAGIC5 = b"\x37\xa4\x30\xec\x05\x00\x00\x00"
MAGIC4 = b"\x37\xa4\x30\xec"
SIZES = [112640, 65536, 262144]
OFFSETS = [0, 4]

TEST_BLOBS = {
    "target_4134_from_db.blob": os.path.join(EXPORT, "target_4134_from_db.blob"),
    "example_1776683436.blob": os.path.join(EXPORT, "example_1776683436.blob"),
    "example_1778132396.blob": os.path.join(EXPORT, "example_1778132396.blob"),
}

MARKERS = ("笙歌", "haha", "appmsg", "title", "<msg", "<appmsg", "哈哈", "米迷")

OUT_DICT = os.path.join(EXPORT, "real_dict_5.bin")


class Logger:
    def __init__(self, path: str):
        self.path = path
        self.lines: list[str] = []

    def log(self, msg: str = "") -> None:
        line = msg.rstrip()
        print(line)
        self.lines.append(line)

    def flush(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("\n".join(self.lines) + "\n")


def readable_score(text: str) -> tuple[int, list[str]]:
    hits = [m for m in MARKERS if m in text]
    printable = sum(1 for c in text if c.isprintable() or c in "\n\r\t")
    ratio = printable / max(len(text), 1)
    score = len(hits) * 10 + int(ratio * 5)
    return score, hits


def try_decompress(blob: bytes, dict_data: bytes, blob_offset: int) -> tuple[bytes | None, str | None]:
    try:
        cd = zstd.ZstdCompressionDict(dict_data)
        dctx = zstd.ZstdDecompressor(dict_data=cd)
        out = dctx.decompress(blob[blob_offset:])
        return out, None
    except Exception as e:
        return None, str(e)


def validate_dict(dict_data: bytes, label: str, log: Logger) -> bool:
    any_success = False
    for blob_name, blob_path in TEST_BLOBS.items():
        if not os.path.isfile(blob_path):
            log.log(f"  SKIP missing blob: {blob_path}")
            continue
        blob = open(blob_path, "rb").read()
        for off in OFFSETS:
            out, err = try_decompress(blob, dict_data, off)
            if out is None:
                log.log(f"  FAIL {label} vs {blob_name} blob_off={off}: {err}")
                continue
            try:
                text = out.decode("utf-8")
            except UnicodeDecodeError:
                text = out.decode("utf-8", errors="replace")
            score, hits = readable_score(text)
            if hits or (score >= 5 and len(out) > 20):
                any_success = True
                log.log(f"  OK {label} vs {blob_name} blob_off={off} len={len(out)} markers={hits} score={score}")
                preview = text[:800].replace("\n", "\\n")
                log.log(f"  PLAINTEXT_PREVIEW: {preview}")
            else:
                log.log(
                    f"  WEAK {label} vs {blob_name} blob_off={off} len={len(out)} "
                    f"markers={hits} score={score} (decompressed but not readable)"
                )
    return any_success


def extract_candidates(data: bytes, match_off: int, source: str, log: Logger) -> list[tuple[bytes, str]]:
    candidates: list[tuple[bytes, str]] = []
    base_offsets = [match_off]
    if match_off >= 4:
        base_offsets.append(match_off - 4)
    if match_off >= 0:
        base_offsets.append(match_off)

    seen: set[tuple[int, int]] = set()
    for start in base_offsets:
        for size in SIZES:
            key = (start, size)
            if key in seen:
                continue
            seen.add(key)
            end = start + size
            if start < 0 or end > len(data):
                log.log(f"  SKIP extract start=0x{start:x} size={size} (out of range)")
                continue
            chunk = data[start:end]
            label = f"{os.path.basename(source)}@0x{start:x}+{size}"
            candidates.append((chunk, label))
            log.log(f"  CANDIDATE {label} ({len(chunk)} bytes)")
    return candidates


def scan_binary(path: str, log: Logger) -> tuple[list[int], list[tuple[int, int]]]:
    magic5_hits: list[int] = []
    magic_only_hits: list[tuple[int, int]] = []

    if not os.path.isfile(path):
        log.log(f"MISSING: {path}")
        return magic5_hits, magic_only_hits

    log.log(f"\n=== SCAN {path} ({os.path.getsize(path)} bytes) ===")
    with open(path, "rb") as f:
        data = f.read()

    off = 0
    while True:
        i = data.find(MAGIC5, off)
        if i < 0:
            break
        magic5_hits.append(i)
        log.log(f"  MAGIC5+dict_id=5 hit at offset 0x{i:x} ({i})")
        off = i + 1

    off = 0
    while True:
        i = data.find(MAGIC4, off)
        if i < 0:
            break
        dict_id = struct.unpack("<I", data[i + 4 : i + 8])[0] if i + 8 <= len(data) else -1
        if dict_id != 5:
            magic_only_hits.append((i, dict_id))
            log.log(f"  MAGIC-only hit at 0x{i:x} dict_id={dict_id} (0x{dict_id & 0xFFFFFFFF:08x})")
        off = i + 1

    return magic5_hits, magic_only_hits


def main() -> int:
    log = Logger(LOG_PATH)
    log.log(f"extract_static_dict5.py — {datetime.now().isoformat()}")
    log.log(f"MAGIC5 pattern: {MAGIC5.hex()}")
    log.log(f"Output dict: {OUT_DICT}")

    all_magic5: dict[str, list[int]] = {}
    all_magic_only: dict[str, list[tuple[int, int]]] = {}
    best_dict: bytes | None = None
    best_label = ""
    solved = False

    for path in BINARIES:
        hits5, hits_magic = scan_binary(path, log)
        all_magic5[path] = hits5
        all_magic_only[path] = hits_magic

        for match_off in hits5:
            with open(path, "rb") as f:
                data = f.read()
            for chunk, label in extract_candidates(data, match_off, path, log):
                if validate_dict(chunk, label, log):
                    log.log(f"\n*** VALIDATION SUCCESS: {label} ***")
                    if best_dict is None or len(chunk) >= len(best_dict):
                        best_dict = chunk
                        best_label = label
                    solved = True

    log.log("\n=== SUMMARY: MAGIC5+dict_id=5 hits ===")
    total5 = 0
    for path, hits in all_magic5.items():
        log.log(f"  {os.path.basename(path)}: {len(hits)} hits {['0x%x' % h for h in hits]}")
        total5 += len(hits)
    if total5 == 0:
        log.log("  (none found)")

    log.log("\n=== SUMMARY: MAGIC-only hits (dict_id != 5) ===")
    total_magic = 0
    for path, hits in all_magic_only.items():
        if not hits:
            continue
        log.log(f"  {os.path.basename(path)}: {len(hits)} magic-only hits")
        for off, did in hits[:50]:
            log.log(f"    0x{off:x} dict_id={did}")
        if len(hits) > 50:
            log.log(f"    ... and {len(hits) - 50} more")
        total_magic += len(hits)
    if total_magic == 0:
        log.log("  (none found)")

    if solved and best_dict is not None:
        with open(OUT_DICT, "wb") as f:
            f.write(best_dict)
        log.log(f"\nVERDICT: SOLVED — wrote {OUT_DICT} ({len(best_dict)} bytes) from {best_label}")
        log.flush()
        return 0

    log.log("\nVERDICT: STATIC EXTRACTION FAILED — proceed to dynamic memory capture (Action 2)")
    log.flush()
    return 1


if __name__ == "__main__":
    sys.exit(main())
