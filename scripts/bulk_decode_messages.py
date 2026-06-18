#!/usr/bin/env python3
"""Bulk inventory and ZSTD recovery for WeChat message blobs.

Scans iOS backup export sqlite (Chat_* / Message) and Mac wechat-decrypt
decrypted DBs (Msg_* / message_content). Attempts raw ZSTD and optional
dictionary-assisted decompression; reports dict_id distribution and success rates.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import struct
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

try:
    import zstandard as zstd
except ImportError:
    print("pip install zstandard", file=sys.stderr)
    sys.exit(1)

ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
WCDB_DICT_MAGIC = b"\x37\xa4\x30\xec"

EXPORT_ROOT = Path(__import__("os").environ.get("WECHAT_ZSTD_WORKSPACE", str(Path(__file__).resolve().parent.parent / "data")))
DECRYPTED_ROOT = Path(
    "/Users/patrickchiho/Downloads/wechat_dict_hunt/wechat-decrypt/decrypted/message"
)
DEFAULT_REPORT = EXPORT_ROOT / "bulk_decode_report.txt"
DEFAULT_JSON = EXPORT_ROOT / "bulk_decode_report.json"

# Example targets from user report (米迷 chat)
EXAMPLE_TIMES = {
    1776683436: "2026-04-20 19:10:36",
    1778132396: "2026-05-07 13:39:56",
}
MIMI_CHAT_EXPORT = "Chat_11d8637ec8a3730380f6691705f8a23c"
MIMI_CHAT_DECRYPTED = "Msg_11d8637ec8a3730380f6691705f8a23c"


@dataclass
class BlobStats:
    total_rows: int = 0
    null_content: int = 0
    text_plain: int = 0
    bytes_other: int = 0
    zstd_frames: int = 0
    dict_ids: Counter = field(default_factory=Counter)
    wcdb_ct: Counter = field(default_factory=Counter)
    msg_types: Counter = field(default_factory=Counter)
    raw_zstd_ok: int = 0
    dict_zstd_ok: int = 0
    decompress_fail: int = 0
    appmsg_recovered: int = 0
    failures_sample: list = field(default_factory=list)


@dataclass
class ExampleResult:
    create_time: int
    time_str: str
    source: str
    row_id: str | int | None
    blob_len: int | None
    dict_id: int | str | None
    wcdb_ct: int | None
    msg_type: int | None
    decode_method: str | None
    preview: str | None
    error: str | None = None


def unix_local(ts: int | None) -> str:
    if ts is None:
        return ""
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    except (OSError, ValueError, OverflowError):
        return str(ts)


def zstd_frame_dict_id(blob: bytes) -> int | str | None:
    if not blob or not blob.startswith(ZSTD_MAGIC):
        return None
    try:
        return zstd.get_frame_parameters(blob).dict_id
    except Exception as exc:
        return f"parse_err:{exc}"


def looks_like_xml(text: str) -> bool:
    return "<msg" in text or "<appmsg" in text or text.strip().startswith("<?xml")


def load_dict_candidates(paths: list[Path]) -> list[tuple[str, bytes]]:
    out: list[tuple[str, bytes]] = []
    seen: set[str] = set()
    for p in paths:
        if not p.is_file():
            continue
        key = str(p.resolve())
        if key in seen:
            continue
        seen.add(key)
        try:
            data = p.read_bytes()
        except OSError:
            continue
        if len(data) < 64:
            continue
        out.append((p.name, data))
    return out


def try_raw_zstd(blob: bytes) -> tuple[str | None, str | None]:
    dctx = zstd.ZstdDecompressor()
    for skip in (0, 4, 8):
        if len(blob) <= skip:
            continue
        try:
            out = dctx.decompress(blob[skip:], max_output_size=4_000_000)
            text = out.decode("utf-8", errors="replace")
            if looks_like_xml(text) or len(text) > 8:
                return text, f"raw_zstd_skip{skip}"
        except Exception:
            continue
    return None, None


def try_dict_zstd(blob: bytes, dicts: list[tuple[str, bytes]]) -> tuple[str | None, str | None]:
    for name, raw in dicts:
        for ds in (0, 4, 8):
            base = raw[ds:] if ds else raw
            for sz in (None, 131072, 112640, 65536):
                dbytes = base if sz is None else base[:sz]
                if len(dbytes) < 64:
                    continue
                for dtype in (
                    zstd.DICT_TYPE_AUTO,
                    zstd.DICT_TYPE_FULLDICT,
                    zstd.DICT_TYPE_RAWCONTENT,
                ):
                    try:
                        cd = zstd.ZstdCompressionDict(dbytes, dict_type=dtype)
                        dctx = zstd.ZstdDecompressor(dict_data=cd)
                    except Exception:
                        continue
                    for bs in (0, 4):
                        if len(blob) <= bs:
                            continue
                        try:
                            out = dctx.decompress(blob[bs:], max_output_size=4_000_000)
                            text = out.decode("utf-8", errors="replace")
                            if looks_like_xml(text):
                                return text, f"dict:{name}:skip{ds}:sz{sz or 'full'}:bs{bs}"
                        except Exception:
                            pass
    return None, None


def classify_content(raw, ct: int | None) -> str:
    if raw is None:
        return "null"
    if isinstance(raw, str):
        return "text"
    if not isinstance(raw, bytes):
        return "other"
    if raw.startswith(ZSTD_MAGIC):
        return "zstd"
    if raw.startswith(WCDB_DICT_MAGIC):
        return "wcdb_dict_header"
    try:
        raw.decode("utf-8")
        return "utf8_bytes"
    except Exception:
        return "binary"


def decode_blob(
    blob: bytes, dicts: list[tuple[str, bytes]], try_dicts: bool
) -> tuple[str | None, str | None]:
    text, method = try_raw_zstd(blob)
    if text:
        return text, method
    if try_dicts:
        return try_dict_zstd(blob, dicts)
    return None, None


def find_export_dbs(root: Path) -> list[Path]:
    dbs: list[Path] = []
    for pat in ("**/message_*.sqlite", "**/message_*.db"):
        dbs.extend(sorted(root.glob(pat)))
    # dedupe
    seen: set[str] = set()
    out: list[Path] = []
    for p in dbs:
        k = str(p.resolve())
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


def find_decrypted_dbs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(
        p
        for p in root.glob("message_*.db")
        if p.is_file() and "fts" not in p.name and "resource" not in p.name
    )


def list_message_tables(conn: sqlite3.Connection, flavor: str) -> list[tuple[str, str, str, str, str]]:
    """Return (table, content_col, ct_col, type_col, id_col, time_col)."""
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in cur.fetchall()]
    out = []
    for t in tables:
        if flavor == "export" and t.startswith("Chat_") and not t.startswith("ChatExt"):
            cols = {c[1] for c in cur.execute(f'PRAGMA table_info("{t}")')}
            if "Message" in cols:
                out.append(
                    (
                        t,
                        "Message",
                        "WCDB_CT_Message" if "WCDB_CT_Message" in cols else None,
                        "Type",
                        "MesLocalID",
                        "CreateTime",
                    )
                )
        elif flavor == "decrypted" and t.startswith("Msg_"):
            cols = {c[1] for c in cur.execute(f'PRAGMA table_info("{t}")')}
            if "message_content" in cols:
                out.append(
                    (
                        t,
                        "message_content",
                        "WCDB_CT_message_content" if "WCDB_CT_message_content" in cols else None,
                        "local_type",
                        "local_id",
                        "create_time",
                    )
                )
    return out


def scan_db(
    db_path: Path,
    flavor: str,
    dicts: list[tuple[str, bytes]],
    example_times: set[int],
    examples: dict[int, ExampleResult],
    chat_filter: str | None = None,
    try_dicts: bool = False,
) -> BlobStats:
    stats = BlobStats()
    conn = sqlite3.connect(str(db_path))
    tables = list_message_tables(conn, flavor)
    if chat_filter:
        tables = [t for t in tables if t[0] == chat_filter]
    cur = conn.cursor()

    for table, col, ct_col, type_col, id_col, time_col in tables:
        ct_expr = f'"{ct_col}"' if ct_col else "NULL"
        type_expr = f'"{type_col}"' if type_col else "NULL"
        q = (
            f'SELECT "{id_col}", "{time_col}", {type_expr}, {ct_expr}, "{col}" '
            f'FROM "{table}"'
        )
        try:
            rows = cur.execute(q).fetchall()
        except sqlite3.Error:
            continue

        for row_id, create_time, msg_type, wcdb_ct, content in rows:
            stats.total_rows += 1
            if msg_type is not None:
                stats.msg_types[int(msg_type)] += 1
            if wcdb_ct is not None:
                stats.wcdb_ct[int(wcdb_ct)] += 1

            kind = classify_content(content, wcdb_ct)
            if kind == "null":
                stats.null_content += 1
                continue
            if kind == "text":
                stats.text_plain += 1
                if content and looks_like_xml(str(content)):
                    stats.appmsg_recovered += 1
                continue
            if kind == "utf8_bytes":
                stats.text_plain += 1
                try:
                    if looks_like_xml(content.decode("utf-8", errors="replace")):
                        stats.appmsg_recovered += 1
                except Exception:
                    pass
                continue
            if kind not in ("zstd", "binary", "wcdb_dict_header"):
                stats.bytes_other += 1
                continue

            blob = content if isinstance(content, bytes) else bytes(content)
            did = zstd_frame_dict_id(blob)
            if did is not None:
                stats.zstd_frames += 1
                stats.dict_ids[str(did)] += 1

            text, method = decode_blob(blob, dicts, try_dicts)
            if text and method and method.startswith("raw_zstd"):
                stats.raw_zstd_ok += 1
                if looks_like_xml(text):
                    stats.appmsg_recovered += 1
            elif text and method and method.startswith("dict:"):
                stats.dict_zstd_ok += 1
                if looks_like_xml(text):
                    stats.appmsg_recovered += 1
            else:
                stats.decompress_fail += 1
                if len(stats.failures_sample) < 30:
                    stats.failures_sample.append(
                        {
                            "db": str(db_path),
                            "table": table,
                            "id": row_id,
                            "create_time": create_time,
                            "time_str": unix_local(create_time),
                            "type": msg_type,
                            "wcdb_ct": wcdb_ct,
                            "len": len(blob),
                            "dict_id": did,
                            "header": blob[:12].hex(),
                        }
                    )

            if create_time in example_times:
                ex = examples.setdefault(
                    int(create_time),
                    ExampleResult(
                        create_time=int(create_time),
                        time_str=EXAMPLE_TIMES.get(int(create_time), unix_local(create_time)),
                        source="",
                        row_id=None,
                        blob_len=None,
                        dict_id=None,
                        wcdb_ct=None,
                        msg_type=None,
                        decode_method=None,
                        preview=None,
                    ),
                )
                ex.source = f"{db_path.name}:{table}"
                ex.row_id = row_id
                ex.blob_len = len(blob)
                ex.dict_id = did
                ex.wcdb_ct = wcdb_ct
                ex.msg_type = msg_type
                if text:
                    ex.decode_method = method
                    ex.preview = text[:500]
                else:
                    ex.error = "decompress_failed"

    conn.close()
    return stats


def discover_dict_paths(export_root: Path) -> list[Path]:
    patterns = [
        "real_dict_5*.bin",
        "dict_from_*.bin",
        "wechat_dict_5.bin",
        "dict_id*_dump*.bin",
        "mem_cand_magic_*.bin",
        "deep_cand_*.bin",
        "deep_xml_*.bin",
        "carved_dicts/*.zdict",
        "carved_dicts/*.bin",
    ]
    paths: list[Path] = []
    for pat in patterns:
        paths.extend(export_root.glob(pat))
    return sorted(set(paths))


def _stats_to_json(stats: BlobStats) -> dict:
    d = asdict(stats)
    d["dict_ids"] = dict(stats.dict_ids)
    d["wcdb_ct"] = {str(k): v for k, v in stats.wcdb_ct.items()}
    d["msg_types"] = {str(k): v for k, v in stats.msg_types.items()}
    return d


def summarize_stats(label: str, stats: BlobStats, lines: list[str]) -> None:
    lines.append(f"\n=== {label} ===")
    lines.append(f"total_rows: {stats.total_rows}")
    lines.append(f"plaintext/text: {stats.text_plain}")
    lines.append(f"null: {stats.null_content}")
    lines.append(f"zstd_frames: {stats.zstd_frames}")
    lines.append(f"raw_zstd_ok: {stats.raw_zstd_ok}")
    lines.append(f"dict_zstd_ok: {stats.dict_zstd_ok}")
    lines.append(f"decompress_fail: {stats.decompress_fail}")
    lines.append(f"appmsg_recovered: {stats.appmsg_recovered}")
    if stats.dict_ids:
        lines.append("dict_id distribution:")
        for k, v in stats.dict_ids.most_common():
            lines.append(f"  dict_id={k}: {v}")
    if stats.wcdb_ct:
        lines.append("WCDB_CT distribution:")
        for k, v in sorted(stats.wcdb_ct.items()):
            lines.append(f"  CT={k}: {v}")
    if stats.msg_types:
        t49 = sum(v for k, v in stats.msg_types.items() if int(k) & 0xFFFF == 49 or k == 49)
        lines.append(f"type49_count (approx): {t49}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Bulk WeChat message ZSTD recovery report")
    ap.add_argument("--export-root", type=Path, default=EXPORT_ROOT)
    ap.add_argument("--decrypted-root", type=Path, default=DECRYPTED_ROOT)
    ap.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    ap.add_argument("--json", type=Path, default=DEFAULT_JSON)
    ap.add_argument("--mimi-only", action="store_true", help="Only scan 米迷 chat table")
    ap.add_argument(
        "--try-dicts",
        action="store_true",
        help="Brute-force dictionary candidates on failures (slow)",
    )
    args = ap.parse_args()

    dict_paths = discover_dict_paths(args.export_root)
    dicts = load_dict_candidates(dict_paths)

    lines: list[str] = []
    lines.append("WeChat bulk_decode_messages.py report")
    lines.append(f"Generated: {datetime.now().isoformat()}")
    lines.append(f"Dictionary candidates loaded: {len(dicts)}")
    for name, data in dicts[:20]:
        hdr = data[:8]
        did = struct.unpack("<I", hdr[4:8])[0] if hdr[:4] == WCDB_DICT_MAGIC and len(hdr) >= 8 else "n/a"
        lines.append(f"  - {name} ({len(data)} bytes, wcdb_dict_id={did})")

    export_dbs = find_export_dbs(args.export_root / "db")
    if not export_dbs:
        export_dbs = find_export_dbs(args.export_root)
    decrypted_dbs = find_decrypted_dbs(args.decrypted_root)

    lines.append(f"\nExport DBs found: {len(export_dbs)}")
    for p in export_dbs:
        lines.append(f"  {p}")
    lines.append(f"Decrypted DBs found: {len(decrypted_dbs)}")
    for p in decrypted_dbs:
        lines.append(f"  {p}")

    examples: dict[int, ExampleResult] = {}
    example_times = set(EXAMPLE_TIMES)

    export_total = BlobStats()
    decrypted_total = BlobStats()
    per_db: dict[str, BlobStats] = {}

    chat_export = MIMI_CHAT_EXPORT if args.mimi_only else None
    chat_dec = MIMI_CHAT_DECRYPTED if args.mimi_only else None

    for db in export_dbs:
        st = scan_db(db, "export", dicts, example_times, examples, chat_export, args.try_dicts)
        per_db[f"export:{db.name}"] = st
        for field_name in (
            "total_rows",
            "null_content",
            "text_plain",
            "bytes_other",
            "zstd_frames",
            "raw_zstd_ok",
            "dict_zstd_ok",
            "decompress_fail",
            "appmsg_recovered",
        ):
            setattr(export_total, field_name, getattr(export_total, field_name) + getattr(st, field_name))
        export_total.dict_ids.update(st.dict_ids)
        export_total.wcdb_ct.update(st.wcdb_ct)
        export_total.msg_types.update(st.msg_types)

    for db in decrypted_dbs:
        st = scan_db(db, "decrypted", dicts, example_times, examples, chat_dec, args.try_dicts)
        per_db[f"decrypted:{db.name}"] = st
        for field_name in (
            "total_rows",
            "null_content",
            "text_plain",
            "bytes_other",
            "zstd_frames",
            "raw_zstd_ok",
            "dict_zstd_ok",
            "decompress_fail",
            "appmsg_recovered",
        ):
            setattr(
                decrypted_total,
                field_name,
                getattr(decrypted_total, field_name) + getattr(st, field_name),
            )
        decrypted_total.dict_ids.update(st.dict_ids)
        decrypted_total.wcdb_ct.update(st.wcdb_ct)
        decrypted_total.msg_types.update(st.msg_types)

    summarize_stats("EXPORT TOTAL", export_total, lines)
    summarize_stats("DECRYPTED TOTAL", decrypted_total, lines)

    if export_total.zstd_frames:
        pct = 100.0 * export_total.raw_zstd_ok / export_total.zstd_frames
        lines.append(f"export raw_zstd success rate (of zstd frames): {pct:.1f}%")
    if decrypted_total.zstd_frames:
        pct = 100.0 * decrypted_total.raw_zstd_ok / decrypted_total.zstd_frames
        lines.append(f"decrypted raw_zstd success rate (of zstd frames): {pct:.1f}%")

    lines.append("\n=== Per-database (non-zero zstd) ===")
    for name, st in sorted(per_db.items()):
        if st.zstd_frames:
            lines.append(
                f"{name}: rows={st.total_rows} zstd={st.zstd_frames} "
                f"raw_ok={st.raw_zstd_ok} dict_ok={st.dict_zstd_ok} fail={st.decompress_fail}"
            )

    lines.append("\n=== Example messages ===")
    for ts in sorted(EXAMPLE_TIMES):
        ex = examples.get(ts)
        lines.append(f"\n--- {EXAMPLE_TIMES[ts]} (create_time={ts}) ---")
        if not ex:
            lines.append("  NOT FOUND in scanned DBs")
            continue
        lines.append(f"  source: {ex.source}")
        lines.append(f"  row_id: {ex.row_id} type: {ex.msg_type} CT: {ex.wcdb_ct}")
        lines.append(f"  blob_len: {ex.blob_len} dict_id: {ex.dict_id}")
        if ex.decode_method:
            lines.append(f"  DECODED via {ex.decode_method}")
            lines.append(f"  preview:\n{ex.preview}")
        else:
            lines.append(f"  FAILED: {ex.error}")

    # 米迷 overlap analysis
    lines.append("\n=== 米迷 chat coverage ===")
    try:
        exp_db = args.export_root / "db/Documents/48d99549ba8c5780b0908193c1fab6fd/DB/message_2.sqlite"
        dec_db = args.decrypted_root / "message_0.db"
        if exp_db.is_file():
            c = sqlite3.connect(str(exp_db))
            r = c.execute(
                f'SELECT COUNT(*), MIN(CreateTime), MAX(CreateTime) FROM "{MIMI_CHAT_EXPORT}"'
            ).fetchone()
            lines.append(f"export message_2.sqlite: count={r[0]} time={unix_local(r[1])}..{unix_local(r[2])}")
            c.close()
        if dec_db.is_file():
            c = sqlite3.connect(str(dec_db))
            r = c.execute(
                f'SELECT COUNT(*), MIN(create_time), MAX(create_time) FROM "{MIMI_CHAT_DECRYPTED}"'
            ).fetchone()
            lines.append(
                f"decrypted message_0.db: count={r[0]} time={unix_local(r[1])}..{unix_local(r[2])}"
            )
            c.close()
    except Exception as exc:
        lines.append(f"coverage check error: {exc}")

  # Recommendation block
    lines.append("\n=== RECOMMENDATION ===")
    if decrypted_total.raw_zstd_ok and decrypted_total.decompress_fail == 0 and decrypted_total.zstd_frames:
        lines.append(
            "Decrypted Mac DB: ALL zstd blobs decode with raw ZSTD (dict_id=0). "
            "Use wechat-decrypt export_messages.py / mcp_server on decrypted DB — no dict_5."
        )
    elif decrypted_total.zstd_frames:
        fail = decrypted_total.decompress_fail
        lines.append(
            f"Decrypted Mac DB: {decrypted_total.raw_zstd_ok}/{decrypted_total.zstd_frames} "
            f"zstd blobs decode raw; {fail} still fail."
        )

    if export_total.decompress_fail and export_total.dict_ids.get("5", 0):
        lines.append(
            f"iOS export sqlite: {export_total.dict_ids.get('5', 0)} blobs use ZSTD dict_id=5 "
            f"(WCDB_CT=2). These require dict_5 capture OR a Mac-decrypted copy of the same rows."
        )
        lines.append(
            "Example dates 2026-04-20 and 2026-05-07 fall BEFORE decrypted Mac DB range "
            "(Mac sync starts ~2026-05-19) — only in iOS export; dict_5 or LLDB capture required."
        )

    report_text = "\n".join(lines)
    args.report.write_text(report_text, encoding="utf-8")
    print(report_text)

    payload = {
        "generated": datetime.now().isoformat(),
        "dict_candidates": len(dicts),
        "export": _stats_to_json(export_total),
        "decrypted": _stats_to_json(decrypted_total),
        "per_db": {k: _stats_to_json(v) for k, v in per_db.items()},
        "examples": {str(k): asdict(v) for k, v in examples.items()},
    }
    args.json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWrote {args.report}")
    print(f"Wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
