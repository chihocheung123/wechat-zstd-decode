# Technical Summary — WeChat ZSTD dict_id=5 Recovery

**Date:** 2026-06-15 – 2026-06-16  
**Verdict:** **NOT SOLVED** — no working `real_dict_5.bin` produced

## Problem

iOS WeChat export stores some messages as **ZSTD-compressed blobs** with a 1-byte **Dictionary_ID = 5** (WCDB compression slot 5, `WCDB_CT_Message=2`). Without the matching 112 KB ZSTD training dictionary, blobs cannot be decompressed with standard `zstandard` APIs.

**Test case:** MesLocalID **4134** — 903-byte blob, ZSTD frame `dict_id=5`.

## Key findings

| Finding | Detail |
|---------|--------|
| Compression type | `WCDB_CT_Message=2` → ZSTD with trained dictionary |
| Dictionary slot | `dict_id=5` (iOS migration/export path) |
| Mac local DB | Uses `dict_id=0` (no trained dict) — separate code path |
| ZSTD magic | `37 A4 30 EC` + 4-byte little-endian dict ID |
| Expected dict size | ~112,640 bytes (`ZSTD_DICT_SIZE`) |
| Static binaries | **0** MAGIC5 hits in WeChat or `roam_migration` |
| Live memory | **0** MAGIC5 hits; one false-positive dict at heap with `dict_id=1790505228` |
| LLDB breakpoints | **0 hits** on `dict_resolve` / decompress path during UI scrolling |
| Candidate grid | 951 candidates tested — **0** produced readable plaintext |

## Pipeline steps attempted

### 1. Static binary extraction (`extract_static_dict5.py`)

Scanned WeChat (301 MB) and `roam_migration` (8.2 MB) for `37 A4 30 EC 05 00 00 00`.

- WeChat: 2 MAGIC-only hits (wrong dict IDs embedded in code/data)
- roam_migration: 0 magic hits
- WCDB.framework: not present as separate binary (statically linked)

**Result:** dict_5 not embedded in Mac WeChat binaries.

### 2. Memory scanning (`_lldb_id5_scan.py`, `scan_all_dict_hits.py`)

Attached LLDB to WeChat-Debug, scanned readable regions for MAGIC5.

- Found 1 MAGIC at `0x126010870` with `dict_id=1790505228` — does not decompress test blobs
- Full heap scan too slow / hung in v1 resigned capture

**Result:** dict_5 not resident in memory during passive use.

### 3. LLDB breakpoint capture (`lldb_capture_setup.py`)

Set breakpoints on `roam_migration` offsets:

- `dict_resolve` @ slide+0x256A20
- `decompress_entry`, `zstd_expand`, `sqlite_value_path`, etc.
- Optional aggressive mode: 15 breakpoints + `ZSTD_*` symbol hunt

Ran 90s batch captures while scrolling target chat, searching markers, expanding messages.

**Result:** `breakpoint_hits=0` — decompress path not triggered in main WeChat process.

### 4. Migration UI capture (`capture_dict5_resigned.sh`, `_migration_dict5_scan_v6.py`)

Attached to WeChat-Resigned during backup/migration UI.

- v1: full heap scan hung
- v2: module-only scan + decompress symbol breakpoints
- Merely opening backup UI does not load dict_5; must **start** migration

**Result:** MAGIC5=0 — dict not loaded during passive backup UI.

### 5. Validation grid (`validate_all_candidates.py`, `validate_dict5.py`)

Exhaustive decompress attempts on all candidate `.bin` files:

- dict_skip: 0/4/8 bytes
- dict sizes: 65536 / 112640 / 131072 / 262144
- blob offsets: 0 / 4
- Patched header dict_id→5: `ZstdCompressionDict` accepts but decompressor fails
- `ZSTD_createDDict_advanced` with forced dictID=5: segfault / no output

**Result:** no candidate dictionary works.

### 6. Automated pipeline (`agent_recover_v2.py`)

Pre-scan → LLDB 90s capture → post-scan → frame-patch brute → validate.

**Result:** FAILURE — no plaintext recovered.

## What worked vs didn't

| Worked | Didn't work |
|--------|-------------|
| LLDB attach to WeChat-Debug / Resigned | Capturing dict_5 at runtime |
| Slide calculation for `roam_migration` | Breakpoint hits during chat scroll |
| ZSTD frame parsing (confirm dict_id=5) | Static dict extraction from binaries |
| Neighbor msg 4133 plaintext ("haha", CT=0) | Decompressing msg 4134 blob (CT=2) |
| Decrypted Mac DB path (separate tool) | iOS export blob path without dict_5 |
| Fixed `WaitForEvent` LLDB bug (uint32) | Full-heap memory scan (too slow) |

## Root cause analysis

1. `dict_id=5` is an **iOS migration/export** dictionary, not used for Mac local DB.
2. dict_5 is **not statically embedded** in scanned WeChat-Debug binaries.
3. dict_5 is **transient** — loaded only when migration/decompress code path runs.
4. Mac WeChat chat scrolling may decompress via a **different process** (WeChatAppEx helper) or a code path that doesn't hit `roam_migration` breakpoints in the main binary.
5. Opening backup UI alone is insufficient; migration must be **actively started**.

## Next manual steps

### Option A: LLDB capture with active UI (highest priority)

1. Quit all LLDB sessions attached to WeChat.
2. Start WeChat-Debug.
3. Run `./bin/run_lldb_capture_aggressive_90s.sh`.
4. **During 90s window:**
   - Open target chat
   - Scroll CT=2 compressed messages
   - Open quote chains, expand long bubbles
   - Search known markers in chat
   - Tap messages to open detail view
5. Check `lldb_capture_hits.log` for `HIT bp=dict_resolve`.
6. If `real_dict_5_*.bin` appears: `python3 scripts/validate_dict5.py real_dict_5.bin`.

### Option B: Migration flow capture

1. Launch WeChat-Resigned fresh.
2. Open 备份与迁移 → **click to start** backup/migration.
3. Within 3 minutes: `./bin/capture_dict5_resigned.sh`.
4. Check `migration_capture.log` for `MAGIC5_HIT`.

### Option C: Attach to WeChatAppEx helper

If main-process breakpoints stay at 0:

```bash
pgrep -lf WeChatAppEx
lldb -p <pid> -o 'image list -o -f roam_migration'
```

Repeat capture in the helper process.

### Option D: iOS device capture

dict_5 may only exist on iOS WeChat during export. Capture from iOS process memory or extract from iOS app bundle.

### Option E: Use decrypted DB path (already works)

For message 4134 specifically, recovery via `wechat-decrypt` decrypted local DB succeeded (dict_id=0). This bypasses the iOS export ZSTD path entirely.

## Artifacts to place in workspace

Copy from your local export folder into `WECHAT_ZSTD_WORKSPACE`:

- `target_4134_from_db.blob` — primary test blob (903 B)
- `example_1776683436.blob`, `example_1778132396.blob` — additional test blobs
- Any `real_dict_5_*.bin` or `dict_from_*.bin` candidates from captures

These are gitignored; the repo contains methodology only.
