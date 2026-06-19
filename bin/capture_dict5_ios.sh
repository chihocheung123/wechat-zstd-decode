#!/usr/bin/env bash
# capture_dict5_ios.sh — WeChat dict_id=5 iOS device capture
#
# USAGE
#   ./bin/capture_dict5_ios.sh [--ip DEVICE_IP] [--port PORT] [--mode MODE]
#
# MODES
#   developer (default) — non-jailbroken, DeveloperDiskImage required
#   jailbreak           — jailbroken device, debugserver running on device
#
# PREREQUISITES (developer mode — non-jailbroken)
#   idevicedebugserverproxy alone does NOT attach WeChat automatically.
#   You must first launch WeChat on-device via ios-deploy (or Xcode) so it
#   starts under debugserver supervision, THEN run this script to connect.
#
#   Recommended flow:
#     Step 1: brew install ios-deploy libimobiledevice
#     Step 2 (terminal A): ios-deploy --debug --bundle-id com.tencent.xin
#             — this launches WeChat and blocks with debugserver on USB port
#     Step 3 (terminal B): idevicedebugserverproxy ${PORT}
#             — proxies debugserver to localhost:${PORT}
#     Step 4: run this script (it connects via process connect)
#
#   Alternative: use idevicedebugserverproxy ${PORT} AND manually trigger a
#   WeChat relaunch via Xcode's Attach to Process, then run this script.
#
# PREREQUISITES (jailbreak mode)
#   On device: /Developer/usr/bin/debugserver *:${PORT} -waitfor WeChat
#   (This attaches debugserver as WeChat launches — no separate ios-deploy needed)
#   ssh tunnel optional: ssh -L 1234:127.0.0.1:1234 root@DEVICE_IP
#
# After 90 s scan, if real_dict_5.bin appears, validate with:
#   python3 scripts/validate_dict5.py data/real_dict_5.bin

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORKSPACE="${WECHAT_ZSTD_WORKSPACE:-${REPO_ROOT}/data}"
SCAN_MODULE="${REPO_ROOT}/scripts/_migration_dict5_scan_v6.py"
VALIDATE_SCRIPT="${REPO_ROOT}/scripts/validate_dict5.py"
LOG="${WORKSPACE}/ios_capture.log"

DEVICE_IP="localhost"
PORT=1234
MODE="developer"

# ── arg parse ─────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --ip)    DEVICE_IP="$2"; shift 2 ;;
        --port)  PORT="$2";      shift 2 ;;
        --mode)  MODE="$2";      shift 2 ;;
        --help|-h)
            sed -n '2,30p' "$0" | grep '^#' | sed 's/^# \?//'
            exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

mkdir -p "${WORKSPACE}"

echo "=== WeChat iOS dict_id=5 capture ===" | tee "${LOG}"
echo "mode=${MODE} device=${DEVICE_IP}:${PORT}" | tee -a "${LOG}"
echo "workspace=${WORKSPACE}" | tee -a "${LOG}"
echo "" | tee -a "${LOG}"

# ── pre-flight ────────────────────────────────────────────────────────────────
if [[ ! -f "${SCAN_MODULE}" ]]; then
    echo "ERROR: scan module not found: ${SCAN_MODULE}" | tee -a "${LOG}"
    exit 1
fi

if ! command -v lldb &>/dev/null; then
    echo "ERROR: lldb not found. Install Xcode Command Line Tools." | tee -a "${LOG}"
    exit 1
fi

# ── build lldb commands ───────────────────────────────────────────────────────
TMP_LLDB="$(mktemp /tmp/wechat_ios_capture_XXXXXX.lldb)"
trap 'rm -f "${TMP_LLDB}"' EXIT

cat > "${TMP_LLDB}" <<LLDB_EOF
# iOS remote platform setup
# Note: idevicedebugserverproxy and jailbreak debugserver both expose gdb-remote
# protocol — connect via 'process connect', NOT 'platform connect'.
platform select remote-ios

# Connect to the debugserver/gdb-remote proxy running on this Mac's port.
# The --waitfor behaviour is handled by debugserver on-device (*:PORT -waitfor WeChat).
process connect --plugin process.gdb-remote connect://${DEVICE_IP}:${PORT}

# Load scan module
command script import ${SCAN_MODULE}

# Run 90-second MAGIC5 scan
migration_capture_90s_v6

quit
LLDB_EOF

echo "--- LLDB script: ${TMP_LLDB} ---" | tee -a "${LOG}"
cat "${TMP_LLDB}" | tee -a "${LOG}"
echo "" | tee -a "${LOG}"

# ── instructions by mode ──────────────────────────────────────────────────────
if [[ "${MODE}" == "developer" ]]; then
    echo "┌──────────────────────────────────────────────────────────────┐"
    echo "│  DEVELOPER MODE — complete these steps BEFORE pressing Enter │"
    echo "│                                                              │"
    echo "│  1. Connect iPhone via USB; trust this computer on device   │"
    echo "│                                                              │"
    echo "│  2. Terminal A — launch WeChat under debugserver:           │"
    echo "│       ios-deploy --debug --bundle-id com.tencent.xin        │"
    echo "│     (blocks here; WeChat opens on device supervised)        │"
    echo "│     Alternative: Xcode → Debug → Attach to Process → WeChat │"
    echo "│                                                              │"
    echo "│  3. Terminal B — proxy debugserver to localhost:${PORT}:        │"
    echo "│       idevicedebugserverproxy ${PORT}                           │"
    echo "│                                                              │"
    echo "│  4. On iPhone: go to                                        │"
    echo "│       Me → Settings → Chat → Backup & Migrate Chat History  │"
    echo "│       → start migration TO this Mac                         │"
    echo "│                                                              │"
    echo "│  5. While migration runs, press Enter here to start scan    │"
    echo "└──────────────────────────────────────────────────────────────┘"
elif [[ "${MODE}" == "jailbreak" ]]; then
    echo "┌─────────────────────────────────────────────────────────┐"
    echo "│  JAILBREAK MODE — steps before running this script      │"
    echo "│                                                          │"
    echo "│  1. On device (SSH or terminal):                         │"
    echo "│       /Developer/usr/bin/debugserver \\                  │"
    echo "│         *:${PORT} -waitfor WeChat                           │"
    echo "│  2. Optionally SSH-tunnel:                               │"
    echo "│       ssh -L ${PORT}:127.0.0.1:${PORT} root@${DEVICE_IP}        │"
    echo "│  3. On iPhone: open WeChat → start Backup/Migration       │"
    echo "│  4. Press Enter here to start scan                       │"
    echo "└─────────────────────────────────────────────────────────┘"
fi

echo ""
read -r -p "Press Enter to start iOS LLDB capture (Ctrl-C to abort)..."
echo ""

# ── run ───────────────────────────────────────────────────────────────────────
export WECHAT_ZSTD_REPO="${REPO_ROOT}"
export WECHAT_ZSTD_WORKSPACE="${WORKSPACE}"
export MIGRATION_CAPTURE_APP_LABEL="WeChat-iOS"
export WECHAT_ZSTD_VALIDATE_SCRIPT="${VALIDATE_SCRIPT}"

echo "Starting LLDB iOS capture…" | tee -a "${LOG}"
lldb --batch -s "${TMP_LLDB}" 2>&1 | tee -a "${LOG}"

# ── results ───────────────────────────────────────────────────────────────────
echo ""
echo "=== Capture finished. Checking for real_dict_5.bin ===" | tee -a "${LOG}"

DICT_FILE=""
for f in "${WORKSPACE}"/real_dict_5*.bin; do
    if [[ -f "$f" ]]; then
        DICT_FILE="$f"
        break
    fi
done

if [[ -n "${DICT_FILE}" ]]; then
    echo "✅ Found: ${DICT_FILE}" | tee -a "${LOG}"
    echo "Running validation…" | tee -a "${LOG}"
    if python3 "${VALIDATE_SCRIPT}" "${DICT_FILE}" 2>&1 | tee -a "${LOG}"; then
        echo ""
        echo "🎉 dict_id=5 FOUND AND VALIDATED: ${DICT_FILE}"
        echo "   Run: python3 scripts/validate_dict5.py ${DICT_FILE}"
    else
        echo "⚠️  File found but validation failed — may not be the correct dict."
    fi
else
    echo "❌ No real_dict_5.bin produced. See log: ${LOG}" | tee -a "${LOG}"
    echo ""
    echo "Troubleshooting:"
    echo "  1. Confirm WeChat migration was actively running during the 90s scan"
    echo "  2. Check ${LOG} for PHASE1_RANGE / PHASE2_RANGE counts"
    echo "  3. If phase2_ranges=0, the process may not have been attached correctly"
    echo "  4. Try jailbreak mode for deeper memory access"
    echo "  5. See docs/IOS_DICT5_README.txt for alternative approaches"
fi
