#!/usr/bin/env bash
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE="${WECHAT_ZSTD_WORKSPACE:-$REPO_ROOT/data}"
mkdir -p "$WORKSPACE"
set -euo pipefail
cd "$(dirname "$0")"
rm -f migration_capture.log resigned_capture_run.log
echo "=== v6 capture starting (run DURING active backup migration) ==="
CAPTURE_FORCE=1 ./capture_dict5_resigned.sh
