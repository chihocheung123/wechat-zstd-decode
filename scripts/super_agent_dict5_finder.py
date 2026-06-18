#!/usr/bin/env python3
"""Comprehensive static ZSTD dict_id=5 scanner across all WeChat install paths."""
from __future__ import annotations

from pathlib import Path

import os
import struct
import sys
import time
from collections import defaultdict
from datetime import datetime

try:
    import zstandard as zstd
except ImportError:
    print("ERROR: pip install zstandard", file=sys.stderr)
    sys.exit(1)

EXPORT = str(Path(__import__("os").environ.get("WECHAT_ZSTD_WORKSPACE", str(Path(__file__).resolve().parent.parent / "data"))))
LOG_PATH = os.path.join(EXPORT, "super_agent_dict5_finder.log")
OUT_DICT = os.path.join(EXPORT, "real_dict_5.bin")

SCAN_ROOTS = [
    "/Applications/WeChat.app",
    "/Users/patrickchiho/Applications/WeChat-Debug.app",
    "/Users/patrickchiho/Library/Containers/com.tencent.xinWeChat",
    "/Users/patrickchiho/Library/Group Containers/group.com.tencent.xinWeChat",
    "/Users/patrickchiho/Library/Group Containers/5A4RE8SF68.com.tencent.xinWeChat",
    EXPORT,
]

PRIORITY_SUBSTRINGS = (
    "roam_migration",
    "WCDB",
    "WeChat",
    "wcdb",
)

MAGIC5 = b"\x37\xa4\x30\xec\x05\x00\x00\x00"
MAGIC4 = b"\x37\xa4\x30\xec"
SIZES = (112640, 65536, 262144)
EXTRACT_OFFSETS = (0, 4)
BLOB_OFFSETS = (0, 4)
DICT_SKIPS = (0, 4, 8)
DICT_TYPES = (
    zstd.DICT_TYPE_AUTO,
    zstd.DICT_TYPE_FULLDICT,
    zstd.DICT_TYPE_RAWCONTENT,
)

TEST_BLOBS = {
    "target_4134_from_db.blob": os.path.join(EXPORT, "target_4134_from_db.blob"),
    "example_1776683436.blob": os.path.join(EXPORT, "example_1776683436.blob"),
    "example_1778132396.blob": os.path.join(EXPORT, "example_1778132396.blob"),
}

MARKERS = ("笙歌", "haha", "appmsg", "title", "refermsg", "<msg", "<appmsg", "哈哈", "米迷")

SKIP_DIR_NAMES = {
    ".git",
    ".svn",
    "node_modules",
    "__pycache__",
    ".Trash",
    "html",
    "jsonl",
    "carved_dicts",
}

SKIP_FILE_SUFFIXES = (
    ".html",
    ".json",
    ".jsonl",
    ".txt",
    ".md",
    ".log",
    ".py",
    ".sh",
    ".csv",
    ".xml",
    ".plist",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".icns",
    ".mp4",
    ".mov",
    ".wav",
    ".mp3",
)

CHUNK_SIZE = 64 * 1024 * 1024
MAX_FILE_BYTES = 4 * 1024 * 1024 * 1024


class Logger:
    def __init__(self, path: str):
        self.path = path
        self._fh = open(path, "w", encoding="utf-8")

    def log(self, msg: str = "") -> None:
        line = msg.rstrip()
        print(line, flush=True)
        self._fh.write(line + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


def root_label(path: str) -> str:
    if path == EXPORT:
        return "wechat_export"
    return os.path.basename(path.rstrip("/"))


def should_skip_dir(dirname: str, full_path: str) -> bool:
    if dirname in SKIP_DIR_NAMES:
        return True
    if dirname.endswith(".appex"):
        return False
    return False


def should_skip_file(path: str) -> bool:
    lower = path.lower()
    for suf in SKIP_FILE_SUFFIXES:
        if lower.endswith(suf):
            return True
    return False


def iter_files(roots: list[str], log: Logger) -> list[str]:
    files: list[str] = []
    skipped_dirs: list[str] = []
    missing_roots: list[str] = []

    for root in roots:
        if not os.path.isdir(root):
            missing_roots.append(root)
            log.log(f"MISSING ROOT: {root}")
            continue
        for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
            dirnames[:] = [
                d
                for d in dirnames
                if not should_skip_dir(d, os.path.join(dirpath, d))
            ]
            for name in filenames:
                full = os.path.join(dirpath, name)
                if should_skip_file(full):
                    continue
                files.append(full)

    priority = [f for f in files if any(p in f for p in PRIORITY_SUBSTRINGS)]
    rest = [f for f in files if f not in set(priority)]
    ordered = priority + rest

    log.log(f"Discovered {len(ordered)} files across {len(roots)} roots")
    if missing_roots:
        log.log(f"Missing roots: {missing_roots}")
    return ordered


def find_patterns_in_data(
    data: bytes, base_offset: int = 0
) -> tuple[list[int], list[tuple[int, int]]]:
    magic5_hits: list[int] = []
    magic_only: list[tuple[int, int]] = []

    off = 0
    while True:
        i = data.find(MAGIC5, off)
        if i < 0:
            break
        magic5_hits.append(base_offset + i)
        off = i + 1

    off = 0
    while True:
        i = data.find(MAGIC4, off)
        if i < 0:
            break
        abs_off = base_offset + i
        dict_id = struct.unpack("<I", data[i + 4 : i + 8])[0] if i + 8 <= len(data) else -1
        if dict_id != 5:
            magic_only.append((abs_off, dict_id))
        off = i + 1

    return magic5_hits, magic_only


def scan_file(path: str, log: Logger) -> tuple[list[int], list[tuple[int, int]], bytes | None]:
    magic5_hits: list[int] = []
    magic_only: list[tuple[int, int]] = []
    file_data: bytes | None = None

    try:
        size = os.path.getsize(path)
    except OSError as e:
        log.log(f"SKIP size {path}: {e}")
        return magic5_hits, magic_only, file_data

    if size == 0:
        return magic5_hits, magic_only, file_data

    if size > MAX_FILE_BYTES:
        log.log(f"SKIP huge file ({size} bytes): {path}")
        return magic5_hits, magic_only, file_data

    try:
        if size <= CHUNK_SIZE:
            with open(path, "rb") as f:
                file_data = f.read()
            h5, hm = find_patterns_in_data(file_data, 0)
            magic5_hits.extend(h5)
            magic_only.extend(hm)
        else:
            with open(path, "rb") as f:
                overlap = len(MAGIC5) - 1
                pos = 0
                prev_tail = b""
                while True:
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    combined = prev_tail + chunk
                    base = pos - len(prev_tail)
                    h5, hm = find_patterns_in_data(combined, base)
                    magic5_hits.extend(h5)
                    magic_only.extend(hm)
                    prev_tail = chunk[-overlap:] if len(chunk) >= overlap else chunk
                    pos += len(chunk)
                    if len(chunk) < CHUNK_SIZE:
                        break
            file_data = None
    except (OSError, PermissionError) as e:
        log.log(f"SKIP read {path}: {e}")
        return [], [], None

    return magic5_hits, magic_only, file_data


def load_file_region(path: str, start: int, size: int, cached: bytes | None) -> bytes | None:
    end = start + size
    if cached is not None:
        if start < 0 or end > len(cached):
            return None
        return cached[start:end]
    try:
        with open(path, "rb") as f:
            f.seek(start)
            return f.read(size)
    except (OSError, PermissionError):
        return None


def extract_candidates(
    path: str, match_off: int, cached: bytes | None, log: Logger
) -> list[tuple[bytes, str]]:
    candidates: list[tuple[bytes, str]] = []
    base_starts = {match_off}
    if match_off >= 4:
        base_starts.add(match_off - 4)

    seen: set[tuple[int, int]] = set()
    for start in sorted(base_starts):
        for size in SIZES:
            key = (start, size)
            if key in seen:
                continue
            seen.add(key)
            chunk = load_file_region(path, start, size, cached)
            if chunk is None or len(chunk) < 8192:
                log.log(f"  SKIP extract {path}@0x{start:x}+{size} (out of range/too small)")
                continue
            label = f"{path}@0x{start:x}+{size}"
            candidates.append((chunk, label))
            log.log(f"  CANDIDATE {label} ({len(chunk)} bytes)")
    return candidates


def try_decompress(blob: bytes, dict_data: bytes) -> tuple[bytes | None, str]:
    for skip in DICT_SKIPS:
        dd = dict_data[skip:] if skip else dict_data
        for dtype in DICT_TYPES:
            try:
                cd = zstd.ZstdCompressionDict(dd, dict_type=dtype)
            except Exception:
                continue
            for off in BLOB_OFFSETS:
                try:
                    out = zstd.ZstdDecompressor(dict_data=cd).decompress(blob[off:])
                    return out, f"skip{skip}/dtype{dtype}/off{off}"
                except Exception:
                    pass
    return None, ""


def validate_dict(dict_data: bytes, label: str, log: Logger) -> tuple[bool, str]:
    any_success = False
    best_preview = ""
    for blob_name, blob_path in TEST_BLOBS.items():
        if not os.path.isfile(blob_path):
            log.log(f"  SKIP missing blob: {blob_path}")
            continue
        blob = open(blob_path, "rb").read()
        out, how = try_decompress(blob, dict_data)
        if out is None:
            log.log(f"  FAIL {label} vs {blob_name}")
            continue
        text = out.decode("utf-8", errors="replace")
        hits = [m for m in MARKERS if m in text]
        if hits:
            any_success = True
            log.log(f"  OK {label} vs {blob_name} {how} len={len(out)} markers={hits}")
            preview = text[:800].replace("\n", "\\n")
            log.log(f"  PLAINTEXT: {preview}")
            if not best_preview:
                best_preview = preview
        else:
            log.log(f"  WEAK {label} vs {blob_name} {how} len={len(out)} (no markers)")
    return any_success, best_preview


def main() -> int:
    log = Logger(LOG_PATH)
    t0 = time.time()
    log.log(f"super_agent_dict5_finder.py — {datetime.now().isoformat()}")
    log.log(f"MAGIC5: {MAGIC5.hex()}")
    log.log(f"Scan roots ({len(SCAN_ROOTS)}):")
    for r in SCAN_ROOTS:
        exists = os.path.isdir(r)
        log.log(f"  {'OK' if exists else 'MISSING'} {r}")
    log.log("")

    files = iter_files(SCAN_ROOTS, log)
    per_root_files: dict[str, int] = defaultdict(int)
    per_root_magic5: dict[str, int] = defaultdict(int)
    per_root_magic_only: dict[str, int] = defaultdict(int)

    all_magic5: dict[str, list[int]] = defaultdict(list)
    all_magic_only: dict[str, list[tuple[int, int]]] = defaultdict(list)

    best_dict: bytes | None = None
    best_label = ""
    best_preview = ""
    solved = False
    validation_attempts = 0
    validation_successes = 0

    total = len(files)
    for idx, path in enumerate(files, 1):
        assigned_root = next((r for r in SCAN_ROOTS if path.startswith(r)), "other")
        per_root_files[root_label(assigned_root if assigned_root != "other" else path)] += 1

        if idx % 200 == 0 or idx == total:
            elapsed = time.time() - t0
            log.log(f"PROGRESS {idx}/{total} ({elapsed:.1f}s) — last: {path}")

        hits5, hits_magic, cached = scan_file(path, log)
        if not hits5 and not hits_magic:
            continue

        rl = root_label(assigned_root)
        if hits5:
            per_root_magic5[rl] += len(hits5)
            all_magic5[path].extend(hits5)
            log.log(f"\n*** MAGIC5 HIT ({len(hits5)}) in {path} ***")
            for h in hits5:
                log.log(f"  offset 0x{h:x}")

        if hits_magic:
            per_root_magic_only[rl] += len(hits_magic)
            all_magic_only[path].extend(hits_magic)
            if not hits5:
                log.log(f"\nMAGIC-only ({len(hits_magic)}) in {path}")
            for off, did in hits_magic[:10]:
                log.log(f"  magic-only 0x{off:x} dict_id={did} (0x{did & 0xFFFFFFFF:08x})")
            if len(hits_magic) > 10:
                log.log(f"  ... +{len(hits_magic) - 10} more magic-only")

        for match_off in hits5:
            for chunk, label in extract_candidates(path, match_off, cached, log):
                validation_attempts += 1
                ok, preview = validate_dict(chunk, label, log)
                if ok:
                    validation_successes += 1
                    log.log(f"\n*** VALIDATION SUCCESS: {label} ***")
                    if best_dict is None or len(chunk) >= len(best_dict):
                        best_dict = chunk
                        best_label = label
                        best_preview = preview
                    solved = True

    log.log("\n=== PER-DIRECTORY SUMMARY ===")
    for root in SCAN_ROOTS:
        rl = root_label(root)
        exists = os.path.isdir(root)
        log.log(
            f"{rl}: exists={exists} files_scanned={per_root_files.get(rl, 0)} "
            f"magic5_hits={per_root_magic5.get(rl, 0)} "
            f"magic_only_hits={per_root_magic_only.get(rl, 0)}"
        )

    log.log("\n=== PRIORITY FRAMEWORK CHECK ===")
    priority_paths = [
        "/Applications/WeChat.app/Contents/Frameworks/roam_migration.framework/Versions/A/roam_migration",
        "/Applications/WeChat.app/Contents/Frameworks/WCDB.framework/Versions/A/WCDB",
        "/Applications/WeChat.app/Contents/MacOS/WeChat",
        "/Users/patrickchiho/Applications/WeChat-Debug.app/Contents/Frameworks/roam_migration.framework/Versions/A/roam_migration",
        "/Users/patrickchiho/Applications/WeChat-Debug.app/Contents/Frameworks/WCDB.framework/Versions/A/WCDB",
    ]
    for pp in priority_paths:
        if not os.path.isfile(pp):
            log.log(f"  MISSING {pp}")
            continue
        h5 = all_magic5.get(pp, [])
        hm = all_magic_only.get(pp, [])
        log.log(f"  {pp}: magic5={len(h5)} magic_only={len(hm)}")
        for h in h5:
            log.log(f"    MAGIC5 @ 0x{h:x}")

    log.log("\n=== ALL MAGIC5 HIT FILES ===")
    if not all_magic5:
        log.log("  (none)")
    else:
        for path, hits in sorted(all_magic5.items()):
            log.log(f"  {path}: {len(hits)} hit(s) {[hex(h) for h in hits]}")

    log.log("\n=== NEAR-MISSES (magic without dict_id=5) — top files ===")
    if not all_magic_only:
        log.log("  (none)")
    else:
        ranked = sorted(all_magic_only.items(), key=lambda kv: len(kv[1]), reverse=True)
        for path, hits in ranked[:30]:
            log.log(f"  {path}: {len(hits)} magic-only")
            for off, did in hits[:5]:
                log.log(f"    0x{off:x} dict_id={did}")
            if len(hits) > 5:
                log.log(f"    ... +{len(hits) - 5} more")

    elapsed = time.time() - t0
    log.log(f"\n=== TOTALS ===")
    log.log(f"Files scanned: {total}")
    log.log(f"MAGIC5+dict_id=5 hits: {sum(len(v) for v in all_magic5.values())}")
    log.log(f"Magic-only hits: {sum(len(v) for v in all_magic_only.values())}")
    log.log(f"Validation attempts: {validation_attempts}")
    log.log(f"Validation successes: {validation_successes}")
    log.log(f"Elapsed: {elapsed:.1f}s")

    if solved and best_dict is not None:
        with open(OUT_DICT, "wb") as f:
            f.write(best_dict)
        log.log(f"\nVERDICT: SOLVED")
        log.log(f"Wrote {OUT_DICT} ({len(best_dict)} bytes) from {best_label}")
        log.log(f"PLAINTEXT: {best_preview}")
        log.close()
        return 0

    log.log("\nVERDICT: NOT SOLVED — no candidate validated against iOS export blobs")
    log.close()
    return 1


if __name__ == "__main__":
    sys.exit(main())
