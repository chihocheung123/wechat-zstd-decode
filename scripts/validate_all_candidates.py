from pathlib import Path
#!/usr/bin/env python3
"""Validate all pat_cand / ptr_cand / mem dumps against iOS export blobs."""
import glob
import os
import sys

try:
    import zstandard as zstd
except ImportError:
    sys.exit(1)

EXPORT = str(Path(__import__("os").environ.get("WECHAT_ZSTD_WORKSPACE", str(Path(__file__).resolve().parent.parent / "data"))))
BLOBS = {
    "target_4134_from_db.blob": os.path.join(EXPORT, "target_4134_from_db.blob"),
    "example_1776683436.blob": os.path.join(EXPORT, "example_1776683436.blob"),
    "example_1778132396.blob": os.path.join(EXPORT, "example_1778132396.blob"),
}
MARKERS = ("笙歌", "haha", "appmsg", "title", "<msg", "<appmsg", "哈哈", "米迷")
OUT = os.path.join(EXPORT, "validate_all_candidates.log")

def try_all(blob, d):
    for skip in (0, 4, 8):
        dd = d[skip:] if skip else d
        for dtype in (zstd.DICT_TYPE_AUTO, zstd.DICT_TYPE_FULLDICT, zstd.DICT_TYPE_RAWCONTENT):
            try:
                cd = zstd.ZstdCompressionDict(dd, dict_type=dtype)
            except Exception:
                continue
            for off in (0, 4):
                try:
                    out = zstd.ZstdDecompressor(dict_data=cd).decompress(blob[off:])
                    return out, f"skip{skip}/dtype{dtype}/off{off}"
                except Exception:
                    pass
    return None, ""

def main():
    patterns = ["pat_cand_*.bin", "ptr_cand_*.bin", "mem_id5_*.bin", "real_dict_5*.bin", "dict_from_*.bin", "mem_near_dict.bin", "roam_data_*.bin"]
    files = []
    for p in patterns:
        files.extend(glob.glob(os.path.join(EXPORT, p)))
    files = sorted(set(files))
    lines = [f"Validating {len(files)} candidates\n"]
    solved = False
    for path in files:
        d = open(path, "rb").read()
        for bname, bpath in BLOBS.items():
            blob = open(bpath, "rb").read()
            out, how = try_all(blob, d)
            if out:
                text = out.decode("utf-8", errors="replace")
                hits = [m for m in MARKERS if m in text]
                if hits:
                    solved = True
                    lines.append(f"OK {os.path.basename(path)} vs {bname} {how} hits={hits} len={len(out)}")
                    preview = text[:500].replace("\n", "\\n")
                    lines.append(f"  {preview}")
    if not solved:
        lines.append("NO MATCH among candidates")
    text = "\n".join(lines)
    print(text)
    open(OUT, "w").write(text)
    return 0 if solved else 1

if __name__ == "__main__":
    sys.exit(main())
