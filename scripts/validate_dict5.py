#!/usr/bin/env python3
"""Validate a ZSTD dict_id=5 candidate against all iOS export test blobs."""
from __future__ import annotations

from pathlib import Path

import argparse
import glob
import os
import sys

try:
    import zstandard as zstd
except ImportError:
    print("pip install zstandard", file=sys.stderr)
    sys.exit(1)

EXPORT = str(Path(__import__("os").environ.get("WECHAT_ZSTD_WORKSPACE", str(Path(__file__).resolve().parent.parent / "data"))))
TEST_BLOBS = {
    "target_4134_from_db.blob": os.path.join(EXPORT, "target_4134_from_db.blob"),
    "example_1776683436.blob": os.path.join(EXPORT, "example_1776683436.blob"),
    "example_1778132396.blob": os.path.join(EXPORT, "example_1778132396.blob"),
}
MARKERS = ("笙歌", "haha", "appmsg", "title", "<msg", "<appmsg", "哈哈", "米迷")
BLOB_OFFSETS = (0, 4)
DICT_SKIPS = (0, 4, 8)
DICT_TYPES = (
    zstd.DICT_TYPE_AUTO,
    zstd.DICT_TYPE_FULLDICT,
    zstd.DICT_TYPE_RAWCONTENT,
)
PREVIEW_LEN = 600


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


def validate_file(path: str, verbose: bool = True) -> bool:
    if not os.path.isfile(path):
        if verbose:
            print(f"MISSING {path}")
        return False

    dict_data = open(path, "rb").read()
    if len(dict_data) < 8192:
        if verbose:
            print(f"SKIP {path}: too small ({len(dict_data)} bytes)")
        return False

    label = os.path.basename(path)
    any_ok = False

    for blob_name, blob_path in TEST_BLOBS.items():
        if not os.path.isfile(blob_path):
            if verbose:
                print(f"  SKIP missing blob {blob_path}")
            continue

        blob = open(blob_path, "rb").read()
        out, how = try_decompress(blob, dict_data)
        if out is None:
            if verbose:
                print(f"  FAIL {label} vs {blob_name}")
            continue

        text = out.decode("utf-8", errors="replace")
        hits = [m for m in MARKERS if m in text]
        if hits:
            any_ok = True
            if verbose:
                print(f"  OK {label} vs {blob_name} {how} len={len(out)} markers={hits}")
                preview = text[:PREVIEW_LEN].replace("\n", "\\n")
                print(f"  PLAINTEXT: {preview}")
        elif verbose:
            print(f"  WEAK {label} vs {blob_name} {how} len={len(out)} (no markers)")

    return any_ok


def resolve_paths(args_paths: list[str]) -> list[str]:
    if args_paths:
        return [os.path.abspath(p) for p in args_paths]

    default = os.path.join(EXPORT, "real_dict_5.bin")
    if os.path.isfile(default) or os.path.islink(default):
        return [default]

    return sorted(
        set(
            glob.glob(os.path.join(EXPORT, "real_dict_5_*.bin"))
            + glob.glob(os.path.join(EXPORT, "mem_id5_*.bin"))
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate dict_id=5 ZSTD dictionary candidates")
    parser.add_argument("paths", nargs="*", help="Candidate .bin files (default: real_dict_5*.bin)")
    parser.add_argument("-q", "--quiet", action="store_true", help="Only print summary line")
    args = parser.parse_args()

    paths = resolve_paths(args.paths)
    if not paths:
        print("No candidate dictionaries found.")
        return 2

    verbose = not args.quiet
    if verbose:
        print(f"Validating {len(paths)} candidate(s) against {len(TEST_BLOBS)} blobs\n")

    solved = False
    for path in paths:
        if verbose:
            print(f"--- {path} ---")
        if validate_file(path, verbose=verbose):
            solved = True

    if verbose:
        print()
    if solved:
        print("VERDICT: SOLVED — at least one candidate decompresses iOS export blobs")
        return 0

    print("VERDICT: NOT SOLVED — no candidate matched test blobs")
    return 1


if __name__ == "__main__":
    sys.exit(main())
