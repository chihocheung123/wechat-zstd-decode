#!/usr/bin/env python3
"""Automated follow-up: sqlite/metadata scan, dylib validation, brute-force grid."""
from __future__ import annotations
import os, struct, sqlite3, hashlib
from pathlib import Path
import zstandard as zstd

EXPORT = Path(__import__("os").environ.get("WECHAT_ZSTD_WORKSPACE", str(Path(__file__).resolve().parent.parent / "data")))
BLOB = EXPORT / "msg4134.blob"
MAGIC = b"\x37\xa4\x30\xec"
MAGIC_ID5 = MAGIC + b"\x05\x00\x00\x00"
DICT_SKIPS = (0, 4, 8)
DICT_SIZES = (None, 131072, 114688, 65536)  # None = full file
BLOB_SKIPS = (0, 4, 8)
RESULTS = []

def log(s):
    print(s, flush=True)
    RESULTS.append(s)

def zstd_dict_id(data: bytes, skip: int) -> int | None:
    if len(data) < skip + 8:
        return None
    h = data[skip:skip+8]
    if h[:4] != MAGIC:
        return None
    return struct.unpack("<I", h[4:8])[0]

def is_plausible_dict(data: bytes, skip: int = 0) -> tuple[bool, str]:
    if len(data) < skip + 8:
        return False, "short"
    hdr = data[skip:skip+8]
    if hdr[:4] != MAGIC:
        return False, "no_magic"
    did = struct.unpack("<I", hdr[4:8])[0]
  # entropy check on first 4k after header
    sample = data[skip+8:skip+8+4096]
    if len(sample) < 64:
        return False, "tiny"
    unique = len(set(sample))
    if unique < 32:
        return False, f"low_entropy unique={unique}"
    for dtype in (zstd.DICT_TYPE_AUTO, zstd.DICT_TYPE_FULLDICT, zstd.DICT_TYPE_RAWCONTENT):
        try:
            dslice = data[skip:] if skip else data
            zstd.ZstdCompressionDict(dslice, dict_type=dtype)
            return True, f"ok dict_id={did} dtype={dtype}"
        except Exception as e:
            err = str(e)
    return False, f"zstd_reject:{err[:80]}"

def brute_dict_file(path: Path, blob: bytes) -> dict | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    for ds in DICT_SKIPS:
        base = raw[ds:] if ds else raw
        for sz in DICT_SIZES:
            dbytes = base if sz is None else base[:sz]
            if len(dbytes) < 64:
                continue
            for dtype in (zstd.DICT_TYPE_AUTO, zstd.DICT_TYPE_FULLDICT, zstd.DICT_TYPE_RAWCONTENT):
                try:
                    cd = zstd.ZstdCompressionDict(dbytes, dict_type=dtype)
                except Exception:
                    continue
                for bs in BLOB_SKIPS:
                    try:
                        out = zstd.ZstdDecompressor(dict_data=cd).decompress(blob[bs:])
                        return {
                            "path": str(path), "dict_skip": ds, "dict_size": sz or len(dbytes),
                            "blob_skip": bs, "dtype": dtype, "out_len": len(out),
                            "preview": out[:500],
                        }
                    except Exception:
                        pass
    return None

def scan_sqlite_and_files():
    hits = []
    for p in list(EXPORT.rglob("*.sqlite")) + list(EXPORT.rglob("*.db")) + list((EXPORT / "db").rglob("*")):
        if not p.is_file():
            continue
        try:
            data = p.read_bytes()
        except OSError:
            continue
        pos = 0
        while True:
            i = data.find(MAGIC_ID5, pos)
            if i < 0:
                break
            hits.append((str(p), i, "magic_id5"))
            pos = i + 1
        pos = 0
        while True:
            i = data.find(MAGIC, pos)
            if i < 0:
                break
            chunk = data[i:i+112640]
            ok, why = is_plausible_dict(chunk, 0)
            if ok:
                hits.append((str(p), i, why))
            pos = i + 1
    # carved_dicts
    carved = EXPORT / "carved_dicts"
    if carved.is_dir():
        for p in carved.iterdir():
            if p.is_file() and p.stat().st_size > 1024:
                ok, why = is_plausible_dict(p.read_bytes(), 0)
                if ok:
                    hits.append((str(p), 0, why))
    return hits

def scan_wechat_frameworks():
    roots = [
        Path("/Users/patrickchiho/Applications/WeChat-Debug.app"),
    ]
    findings = []
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix not in (".dylib", "") and "Framework" not in str(p):
                continue
            try:
                st = p.stat()
                if st.st_size < 4096 or st.st_size > 200*1024*1024:
                    continue
                data = p.read_bytes()
            except OSError:
                continue
            name = p.name.lower()
            if "mmcronet" not in name and "wcdb" not in name and "zstd" not in name:
                continue
            pos = 0
            while True:
                i = data.find(MAGIC, pos)
                if i < 0:
                    break
                for skip in (0, 4, 8):
                    ok, why = is_plausible_dict(data[i:], skip)
                    if ok:
                        outpath = EXPORT / f"fw_cand_{p.name}_{i:x}_skip{skip}.bin"
                        chunk = data[i:i+131072]
                        outpath.write_bytes(chunk)
                        findings.append((str(p), i, skip, why, str(outpath)))
                pos = i + 4
    return findings

def try_zstd_dict_id_api(blob: bytes):
    # zstd has no dict_id-only decompress; document attempt
    try:
        zstd.ZstdDecompressor().decompress(blob)
        return "raw_ok"
    except Exception as e:
        return f"raw_fail:{e}"

def main():
    blob = BLOB.read_bytes()
    log(f"blob={BLOB} len={len(blob)}")
    log(f"zstd_no_dict: {try_zstd_dict_id_api(blob)}")

    sqlite_hits = scan_sqlite_and_files()
    log(f"sqlite/metadata magic_id5 hits: {len([h for h in sqlite_hits if 'id5' in h[2]])}")
    log(f"sqlite plausible dict hits: {len(sqlite_hits)}")
    for h in sqlite_hits[:20]:
        log(f"  {h}")

    fw = scan_wechat_frameworks()
    log(f"framework plausible dict candidates: {len(fw)}")
    for f in fw[:15]:
        log(f"  {f}")

    # All dict *.bin in export
    dict_files = sorted(EXPORT.glob("*.bin"))
    dict_files += sorted(EXPORT.glob("fw_cand_*.bin"))
    dict_files += sorted(EXPORT.glob("mem_cand_*.bin"))
    seen = set()
    unique = []
    for p in dict_files:
        if p.name in ("DECODED_4134.bin",):
            continue
        key = p.resolve()
        if key in seen:
            continue
        seen.add(key)
        unique.append(p)

    log(f"brute-force files: {len(unique)}")
    success = None
    validations = []
    for p in unique:
        raw = p.read_bytes()
        for skip in (0, 4, 8):
            ok, why = is_plausible_dict(raw, skip)
            if ok or skip == 0:
                validations.append((p.name, skip, why))
        r = brute_dict_file(p, blob)
        if r:
            success = r
            out = EXPORT / "DECODED_4134.bin"
            out.write_bytes(r["preview"] if False else zstd.ZstdDecompressor(
                dict_data=zstd.ZstdCompressionDict(
                    (raw[r["dict_skip"]:][:r["dict_size"]] if r["dict_size"] else raw[r["dict_skip"]:]),
                    dict_type=r["dtype"],
                )
            ).decompress(blob[r["blob_skip"]:]))
            # re-decompress full
            dslice = raw[r["dict_skip"]:][:r["dict_size"]] if r["dict_size"] else raw[r["dict_skip"]:]
            cd = zstd.ZstdCompressionDict(dslice, dict_type=r["dtype"])
            full = zstd.ZstdDecompressor(dict_data=cd).decompress(blob[r["blob_skip"]:])
            out.write_bytes(full)
            log(f"SUCCESS {r}")
            break
    if not success:
        log("brute-force: no success")
    log("--- validations (plausible zstd dict header) ---")
    for v in validations[:30]:
        log(f"  {v}")

    summary_path = EXPORT / "agent_followup_run.log"
    summary_path.write_text("\n".join(RESULTS) + "\n")
    return 0 if success else 1

if __name__ == "__main__":
    raise SystemExit(main())
