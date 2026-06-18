"""Shared workspace paths for all scripts."""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
WORKSPACE = Path(os.environ.get("WECHAT_ZSTD_WORKSPACE", REPO_ROOT / "data"))
LLDB_DIR = REPO_ROOT / "lldb"
SCRIPTS_DIR = REPO_ROOT / "scripts"
