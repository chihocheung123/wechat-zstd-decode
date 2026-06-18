# LLDB capture manual (WeChat-Debug / roam_migration)

## Bash terminal vs `(lldb)` prompt

| Where | Examples |
|-------|----------|
| **Bash (Terminal.app / iTerm)** — shell `$` or `%` | `cd ...`, `lldb -p 12786 -s ...`, `./run_lldb_capture_90s.sh`, `LLDB_CAPTURE_SQLITE=1 ./run_lldb_capture_90s.sh` |
| **`(lldb)` prompt** — after LLDB starts | `process continue`, `continue`, `breakpoint list`, `quit` |

**Common mistakes**

- Bare `script` at `(lldb)` opens Python `>>>` — do **not** paste multiline Python (`target`, `slide`, `pid`, `addr` only exist inside the setup module, not in the REPL).
- `LLDB_CAPTURE_SQLITE=1 ./run_lldb_capture_90s.sh` at `(lldb)` → `error: 'LLDB_CAPTURE_SQLITE=1' is not a valid command`. Run batch scripts in **Bash**, not inside LLDB.
- If a `.lldb` script failed (`exit code 1`), fix the error before `process continue` — breakpoints may not be installed.
- Only **one** `lldb` per WeChat process; `quit` before starting batch capture.

---

## Root cause (fixed 2026-06-04)

Batch runs logged thousands of `CONTINUE_EVENT_ERR ... argument 2 of type 'uint32_t'` and **zero breakpoint hits** because `_continue_with_timeout()` called:

```python
listener.WaitForEvent(0.25, event)  # wrong: 0.25 is float; arg2 must be uint32 seconds
```

On macOS LLDB Python bindings, `SBListener.WaitForEvent(num_seconds, event)` expects **`num_seconds` as a uint32 integer** (typically 0–5 for poll/wait), not fractional seconds.

**Fix:** use `WaitForEventForBroadcasterWithType(wait_sec, broadcaster, mask, event)` with `wait_sec = min(1, remaining)` (integer), then `debugger.HandleEvent(event)`. `CONTINUE_EVENT_ERR` is logged at most once per capture window.

---

## Tonight: recommended order

### A) Manual dict_resolve (best if batch hits stay at 0)

**Bash — quit any other `lldb` attached to WeChat first.**

Do not type angle-bracket placeholders literally (`lldb -p <WeChat-Debug-PID>` fails). Use a numeric PID from `pgrep` or `lldb_capture_slide.txt` (`pid=12786` → `lldb -p 12786`).

```bash
cd $WECHAT_ZSTD_WORKSPACE (default: ./data)
PID=$(pgrep -lf 'WeChat-Debug.app/Contents/MacOS/WeChat' | awk '/\/MacOS\/WeChat$/ {print $1; exit}')
lldb -p "$PID" -s lldb_manual_dict_resolve.lldb
# example: lldb -p 12786 -s lldb_manual_dict_resolve.lldb
```

The script runs `command script import` + `wcdb_manual_dict_resolve` (no interactive Python REPL).

**`(lldb)` prompt — after you see `dict_resolve @ 0x...` and breakpoint list:**

```
process continue
```

Open **米迷** chat; scroll **CT=2** messages for 1–2 minutes. On each stop: registers, 256-byte x0 dump, disassembly, `HIT bp=dict_resolve` in `lldb_capture_hits.log`. Then:

```
continue
```

When done:

```
quit
```

Outputs: `lldb_capture_hits.log`, `lldb_capture_slide.txt`, optional `x0_mem_dict_resolve_*.bin`, `real_dict_5_dict_resolve.bin`.

**Re-open manual session later (Bash):** same `lldb -p "$PID" -s lldb_manual_dict_resolve.lldb` block.

**Already inside LLDB with WeChat attached (Bash import path):**

```
command script import "$WECHAT_ZSTD_WORKSPACE (default: ./data)/lldb_capture_setup.py"
wcdb_manual_dict_resolve
```

### B) 90s batch capture (after fix)

**Bash only** — must not have another `lldb` attached:

```bash
cd $WECHAT_ZSTD_WORKSPACE (default: ./data)
./run_lldb_capture_90s.sh
```

During the 90s window: scroll 米迷 chat and reply if possible.

### B2) Aggressive 90s batch (expanded offsets + symbol hunt)

Installs **15** core+decompress-path breakpoints (`AGGRESSIVE_OFFSETS` from `static_0x1e78e8_report.txt`) plus module-scoped symbol hunt (`wcdb_decompress`, `ZSTD_*`).

**Bash only:**

```bash
cd $WECHAT_ZSTD_WORKSPACE (default: ./data)
chmod +x run_lldb_capture_aggressive_90s.sh   # once
./run_lldb_capture_aggressive_90s.sh
```

Or manually:

```bash
LLDB_CAPTURE_AGGRESSIVE=1 LLDB_CAPTURE_SYMBOL_HUNT=1 ./run_lldb_capture_90s.sh
# equivalent: lldb -b -p "$PID" -s lldb_capture_aggressive.lldb
```

**UI trigger tips (do all during the 90s window):**

1. Open **米迷** chat (not another contact).
2. Scroll **CT=2** compressed messages (long bubbles / appmsg).
3. **Open quote chain** (引用) — tap quoted reply to force parent message load.
4. **Expand** long message content (「全文」/ double-tap bubble).
5. **Search** in chat: `haha`, `哈哈`, or `笙歌` — jump to hits and open each.
6. **Tap a message** to open detail view — triggers sqlite read / `wcdb_decompress` path.

If still 0 hits after aggressive batch, infrastructure is OK — the decompress path may run in **WeChatAppEx** helper (not main process). Check: `lldb -p <AppEx-pid> -o 'image list -o -f roam_migration'`.

**Expect in `lldb_capture_hits.log`:** `CONTINUE_START`, `CONTINUE_END`, and **no flood** of `CONTINUE_EVENT_ERR`. If UI triggers dict paths, `HIT bp=...` lines and `lldb_capture_hit_count.txt` > 0.

Verify only (no continue) — **Bash:**

```bash
LLDB_CAPTURE_VERIFY=1 ./run_lldb_capture_90s.sh
```

Syntax check (no attach) — **Bash:**

```bash
LLDB_CAPTURE_SYNTAX_ONLY=1 ./run_lldb_capture_90s.sh
```

Symbol hunt only (already attached at `(lldb)`):

```
command script import "$WECHAT_ZSTD_WORKSPACE (default: ./data)/lldb_capture_setup.py"
wcdb_symbol_hunt
process continue
```

Combine aggressive + SQLite:

```bash
LLDB_CAPTURE_AGGRESSIVE=1 LLDB_CAPTURE_SQLITE=1 ./run_lldb_capture_90s.sh
```

### C) SQLite watch during batch

**Bash only.** If you were in manual LLDB, type `quit` at `(lldb)` first, then:

```bash
cd $WECHAT_ZSTD_WORKSPACE (default: ./data)
LLDB_CAPTURE_SQLITE=1 ./run_lldb_capture_90s.sh
```

Or with explicit duration:

```bash
LLDB_CAPTURE_SQLITE=1 BATCH_CAPTURE_SECONDS=90 ./run_lldb_capture_90s.sh
```

Then grep the log for `HIT bp=sqlite3_column_blob` and inspect register lines. Compare blob size to `target_4134_from_db.blob` / `target_4134_message.blob` (903 bytes each).

---

## Files

| File | Role |
|------|------|
| `lldb_capture_setup.py` | Attach, slide, breakpoints, `wcdb_capture_run`, `wcdb_manual_dict_resolve` |
| `lldb_capture_wcdb.lldb` | Batch: import + `wcdb_capture_run` |
| `lldb_capture_aggressive.lldb` | Batch: aggressive offsets + symbol hunt |
| `lldb_manual_dict_resolve.lldb` | Interactive single-BP dict_resolve (import + `wcdb_manual_dict_resolve`) |
| `run_lldb_capture_90s.sh` | Finds PID, clears logs, runs batch 90s |
| `run_lldb_capture_aggressive_90s.sh` | Wrapper: aggressive 90s batch |
| `lldb_capture_hits.log` | Session + HIT lines |
| `lldb_capture_slide.txt` | roam_migration slide and VAs |

---

## Troubleshooting

- **Only one debugger** per process; `quit` other LLDB sessions before `./run_lldb_capture_90s.sh`.
- **Attach denied:** run WeChat-Debug.app; check `lldb_capture_attach_denied.txt`.
- **Stuck at `>>>`:** you entered Python REPL via bare `script` — type `quit()` then use `wcdb_capture_run` or exit LLDB and use the fixed `.lldb` scripts.
- **Breakpoint locations hit count = 0:** offsets/build mismatch — try aggressive batch (B2), SQLite (section C), or manual dict_resolve (section A).
- **Zero `HIT bp=` with clean `CONTINUE_START`/`CONTINUE_END`:** infrastructure OK; runtime path not exercised — retry B2 + UI tips (quote chain, expand, search haha/哈哈).
- **Aggressive mode:** `LLDB_CAPTURE_AGGRESSIVE=1 ./run_lldb_capture_90s.sh` — 12+ roam_migration BPs + optional ZSTD/wcdb symbol BPs; check log for `AGGRESSIVE_OFFSETS_START` / `SYMBOL_HUNT`.
- **`run_lldb_capture_90s.sh` log truncate:** use `printf '' > lldb_capture_hit_count.txt` — a bare filename line is interpreted as a shell command.
