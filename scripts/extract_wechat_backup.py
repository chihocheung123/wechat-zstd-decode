#!/usr/bin/env python3
"""Extract WeChat SQLite files from an unencrypted iOS backup."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

WECHAT_DOMAIN = "AppDomain-com.tencent.xin"
DEFAULT_OUT = Path.home() / "Downloads" / "wechat_export"


def find_manifest(backup_root: Path) -> Path:
    for candidate in (
        backup_root / "Manifest.db",
        backup_root / "Snapshot" / "Manifest.db",
    ):
        if candidate.is_file():
            return candidate
    raise SystemExit(f"Manifest.db not found under {backup_root}")


def backup_file_path(backup_root: Path, file_id: str) -> Path | None:
    for base in (backup_root, backup_root / "Snapshot"):
        p = base / file_id[:2] / file_id
        if p.is_file():
            return p
    return None


def copy_from_manifest(backup_root: Path, manifest: Path, out_root: Path) -> list[dict]:
    conn = sqlite3.connect(str(manifest))
    cur = conn.cursor()
    cur.execute(
        """
        SELECT fileID, domain, relativePath
        FROM Files
        WHERE domain = ?
          AND (
            relativePath LIKE '%.sqlite'
            OR relativePath LIKE '%.sqlite-wal'
            OR relativePath LIKE '%.sqlite-shm'
          )
        ORDER BY relativePath
        """,
        (WECHAT_DOMAIN,),
    )
    rows = cur.fetchall()
    conn.close()

    copied: list[dict] = []
    for file_id, domain, rel_path in rows:
        src = backup_file_path(backup_root, file_id)
        if not src:
            continue
        dest = out_root / "db" / rel_path.replace("/", os.sep)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        copied.append(
            {
                "fileID": file_id,
                "domain": domain,
                "relativePath": rel_path,
                "localPath": str(dest),
            }
        )
    return copied


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: extract_wechat_backup.py <backup_root> [out_root]")
        raise SystemExit(1)

    backup_root = Path(sys.argv[1]).expanduser().resolve()
    out_root = (
        Path(sys.argv[2]).expanduser().resolve()
        if len(sys.argv) >= 3
        else DEFAULT_OUT
    )
    if not backup_root.is_dir():
        raise SystemExit(f"Backup folder not found: {backup_root}")

    manifest = find_manifest(backup_root)
    out_root.mkdir(parents=True, exist_ok=True)
    db_root = out_root / "db"

    print(f"Backup: {backup_root}")
    print(f"Manifest: {manifest}")
    print(f"Output: {out_root}")

    copied = copy_from_manifest(backup_root, manifest, out_root)
    (out_root / "manifest_files.json").write_text(
        json.dumps(copied, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Copied {len(copied)} WeChat sqlite files -> {db_root}")

    summary = {
        "exported_at": datetime.now().isoformat(),
        "backup_root": str(backup_root),
        "manifest": str(manifest),
        "out_root": str(out_root),
        "sqlite_files": len(copied),
    }
    (out_root / "extract_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
