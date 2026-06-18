from pathlib import Path
#!/usr/bin/env python3
"""Test real_dict_5*.bin against target_4134_message.blob (zstd offsets 0 and 4)."""
import glob
import os
import sys

try:
    import zstandard as zstd
except ImportError:
    print("pip install zstandard")
    sys.exit(1)

EXPORT = str(Path(__import__("os").environ.get("WECHAT_ZSTD_WORKSPACE", str(Path(__file__).resolve().parent.parent / "data"))))
TARGET = os.path.join(EXPORT, "target_4134_message.blob")
MARKERS = ("笙歌", "appmsg")

def try_decompress(blob: bytes, dict_data: bytes, offset: int):
    try:
        cd = zstd.ZstdCompressionDict(dict_data)
        dctx = zstd.ZstdDecompressor(dict_data=cd)
        out = dctx.decompress(blob[offset:])
        return out
    except Exception as e:
        return e

def main():
    if not os.path.isfile(TARGET):
        print("missing", TARGET)
        return 1
    blob = open(TARGET, "rb").read()
    dumps = sorted(
        glob.glob(os.path.join(EXPORT, "real_dict_5*.bin"))
        + glob.glob(os.path.join(EXPORT, "dict_from_*.bin"))
    )
    if not dumps:
        print("no real_dict_5*.bin or dict_from_*.bin files")
        return 0
    any_ok = False
    for path in dumps:
        dd = open(path, "rb").read()
        for off in (0, 4):
            r = try_decompress(blob, dd, off)
            if isinstance(r, bytes):
                text = r.decode("utf-8", errors="replace")
                hit = [m for m in MARKERS if m in text]
                print(f"OK {path} offset={off} markers={hit} len={len(r)}")
                any_ok = True
            else:
                print(f"FAIL {path} offset={off}: {r}")
    return 0 if any_ok else 2

if __name__ == "__main__":
    sys.exit(main())
