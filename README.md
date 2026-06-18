# wechat-zstd-decode

Research toolkit for recovering **WeChat WCDB ZSTD dictionary slot 5** (`dict_id=5`) used by iOS export / migration message compression.

## Purpose

WeChat stores some messages with `WCDB_CT_Message=2` (ZSTD-compressed). iOS export blobs use a **trained dictionary** identified as `dict_id=5` in the ZSTD frame header. Without that dictionary, offline decompression fails with `Dictionary mismatch`.

This repo collects scripts developed to:

1. Locate dict_5 in WeChat binaries or live process memory
2. Capture the dictionary at runtime via LLDB breakpoints
3. Validate candidate dictionaries against known compressed test blobs

**Research target:** MesLocalID **4134** — ZSTD frame uses `dict_id=5`, ~903-byte test blob.

## Current status

**FAILURE** — `real_dict_5.bin` was not recovered. dict_5 is not statically embedded in scanned binaries and was not captured in memory. Runtime capture during active migration/decompress UI is still required.

See [SUMMARY.md](SUMMARY.md) for full technical findings.

## Quick start

```bash
git clone <your-repo-url>
cd wechat-zstd-decode
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Point workspace at a directory with test blobs and capture output
export WECHAT_ZSTD_WORKSPACE=~/wechat-zstd-data
mkdir -p "$WECHAT_ZSTD_WORKSPACE"
# Copy target_4134_from_db.blob and other test blobs here

chmod +x bin/*.sh
```

### Recommended capture flow

1. Start **WeChat-Debug** (or WeChat-Resigned for migration capture).
2. Quit any other LLDB sessions.
3. Run batch capture:
   ```bash
   ./bin/run_lldb_capture_90s.sh
   ```
4. **During the 90-second window**, actively use WeChat:
   - Scroll chats with compressed (CT=2) messages
   - Expand long bubbles, open quote chains
   - Or start **Backup & Migration → Transfer/Backup** (not just open the menu)
5. Validate any captured dictionary:
   ```bash
   python3 scripts/validate_dict5.py "$WECHAT_ZSTD_WORKSPACE/real_dict_5.bin"
   ```

### Manual LLDB session

```bash
PID=$(pgrep -lf 'WeChat-Debug.app/Contents/MacOS/WeChat' | awk '/\/MacOS\/WeChat$/ {print $1; exit}')
lldb -p "$PID" -s lldb/lldb_manual_dict_resolve.lldb
# At (lldb) prompt: process continue
```

Full details: [docs/LLDB_MANUAL.md](docs/LLDB_MANUAL.md)

## Directory layout

```
wechat-zstd-decode/
├── README.md
├── SUMMARY.md
├── requirements.txt
├── workspace.py              # Shared WORKSPACE path helper
├── data/                     # Default workspace (gitignored contents)
├── bin/                      # Shell entry points
├── lldb/                     # LLDB scripts + lldb_capture_setup.py
├── scripts/                  # Python tools
│   └── backup/               # WeChat backup/export helpers
└── docs/                     # Manuals, capture notes, agent chat protocol
```

## File inventory

### Shell scripts (`bin/`)

| File | Role |
|------|------|
| `run_lldb_capture_90s.sh` | **Primary** — 90s batch LLDB capture + auto-validate |
| `run_lldb_capture_aggressive_90s.sh` | 15 breakpoints + symbol hunt |
| `run_lldb_capture_60s.sh` | Shorter capture window |
| `run_lldb_capture.sh` | Generic capture runner |
| `run_lldb_capture_attach_diag.sh` | Attach diagnostics |
| `capture_dict5_migration.sh` | Migration UI capture (WeChat-Debug) |
| `capture_dict5_resigned.sh` | Resigned app capture (v2 scanner) |
| `capture_dict5_sudo.sh` | Sudo variant for attach permissions |
| `RUN_CAPTURE_NOW.sh` | One-shot capture launcher |
| `run_symbol_memory_scan.sh` | Symbol + memory scan wrapper |
| `run_lldb_batch_dict_dump.sh` | Batch dict dump |
| `_wechat_app_detect.sh` | Detect running WeChat process |
| `agent-chat` / `agent-check` / `agent-send` | Git ref backed Claude/Codex message channel |

### LLDB (`lldb/`)

| File | Role |
|------|------|
| `lldb_capture_setup.py` | **Core** — slide calc, breakpoints, capture commands |
| `lldb_capture_wcdb.lldb` | Batch capture entry |
| `lldb_capture_aggressive.lldb` | Aggressive breakpoint set |
| `lldb_manual_dict_resolve.lldb` | Interactive dict_resolve capture |
| `lldb_capture_dict_resolve_only.lldb` | Single-BP variant |
| `lldb_memory_scan_only.lldb` | Memory scan without full capture |
| `lldb_symbol_and_scan.lldb` | Symbol hunt + scan |
| `lldb_interactive_dict_dump.lldb` | Interactive dict dump |
| `agent_bp_capture.lldb` | Agent breakpoint capture |
| `lldb_step3_*.lldb` | Step-3 breakpoint experiments |

### Python — validation & recovery (`scripts/`)

| File | Role |
|------|------|
| `validate_dict5.py` | **Validate** dict candidate against test blobs |
| `validate_all_candidates.py` | Grid-test all `*.bin` candidates |
| `test_real_dict_5.py` | Quick test after capture |
| `agent_recover_v2.py` | Automated scan + capture + validate pipeline |
| `bulk_decode_messages.py` | Bulk-decode blobs with a working dict |
| `extract_static_dict5.py` | Scan WeChat binaries for embedded dict_5 |
| `extract_dict.py` | Generic dict extraction helper |
| `action2_dynamic_dict5.py` | Dynamic memory capture during migration UI |
| `super_agent_dict5_finder.py` | Multi-strategy dict finder |
| `agent_scanner.py` | Memory / file scanner agent |
| `agent_followup_run.py` | Follow-up recovery run |

### Python — memory / pattern scanning (`scripts/`)

| File | Role |
|------|------|
| `_migration_dict5_scan_v6.py` | Latest migration scanner (module-scoped) |
| `_lldb_id5_scan.py` | MAGIC5 memory scanner via LLDB |
| `_lldb_pattern_scan.py` | Pattern-based memory scanner |
| `_mem_deep_scan_lldb.py` | Deep memory scan |
| `_mem_raw_brute.py` | Raw memory brute scan |
| `_action2_id5_scan.py` | Action2 id5 scan variant |
| `_lldb_mem_scan.py` | LLDB memory scan helper |
| `scan_all_dict_hits.py` | Scan all dict magic hits |
| `auto_scan_dict.py` | Automated dict scan |
| `lldb_dictid5_scan.py` | dict_id=5 LLDB scan |
| `static_slot5_analyze.py` | Static slot-5 analysis |
| `try_wcdb_loader.py` / `v3` | WCDB loader experiments |
| `run_lldb_batch_dict_dump.py` | Python batch dict dump driver |

### Backup utilities (`scripts/backup/`)

| File | Role |
|------|------|
| `extract_wechat_backup.py` | Extract from WeChat backup |
| `export_mimi_html.py` | Export chat to HTML |
| `static_slot5_table.py` | Static slot-5 table analysis |
| `lldb_scan_dict.py` | LLDB dict scan helper |

### Documentation (`docs/`)

| File | Role |
|------|------|
| `LLDB_MANUAL.md` | LLDB capture manual (bash vs lldb prompt) |
| `CAPTURE_V2_README.txt` | Resigned app capture v2 notes |
| `MIGRATION_CAPTURE_README.txt` | Migration capture instructions |
| `IOS_DICT5_README.txt` | iOS dict_5 notes |
| `capture_dict_5_instructions.txt` | Step-by-step capture guide |
| `AGENT_GIT_REF_CHAT.md` | Git ref message protocol for Writer/Reviewer agents |

## Workspace configuration

All tools read `WECHAT_ZSTD_WORKSPACE` (default: `./data`). Place here:

- Test blobs (`target_4134_from_db.blob`, etc.)
- Capture logs (`lldb_capture_hits.log`, …)
- Recovered dictionaries (`real_dict_5.bin`)

Large binaries, SQLite DBs, and chat content are **not** included in this repo.

## Requirements

- macOS with WeChat-Debug or WeChat-Resigned
- Python 3.10+
- LLDB (Xcode Command Line Tools)
- `zstandard` Python package

## License

Research / personal use. WeChat is Tencent's trademark. Use responsibly on your own data.
