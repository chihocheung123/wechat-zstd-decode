#!/bin/bash
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE="${WECHAT_ZSTD_WORKSPACE:-$REPO_ROOT/data}"
mkdir -p "$WORKSPACE"
set -euo pipefail
cd "$WORKSPACE"
export BATCH_CAPTURE_SECONDS=60
exec ./run_lldb_capture_90s.sh
