# Source from bin/*.sh: source "$(dirname "$0")/../workspace.sh"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export REPO_ROOT
export WECHAT_ZSTD_WORKSPACE="${WECHAT_ZSTD_WORKSPACE:-$REPO_ROOT/data}"
export WECHAT_ZSTD_REPO="$REPO_ROOT"
