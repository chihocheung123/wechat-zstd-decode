WeChat dict_id=5 Capture — v2 Notes
====================================
Date: 2026-06-16

ATTACH STATUS
-------------
LLDB attach to WeChat-Resigned.app WORKED in the prior run (PID 6347).
The process was successfully stopped and the scan module loaded.

WHY MAGIC5=0 (no capture)
---------------------------
Several likely causes from the failed v1 run:

1. FULL HEAP SCAN HUNG
   v1 fell through to scan_all_regions() when the module pass found nothing.
   Round 1 logged MAGIC5_NEW=0 at elapsed=0s, then spent 30+ seconds on the
   full heap and appeared hung (lldb PID 6613 was still attached after 8+ min).

2. DICT NOT LOADED DURING PASSIVE BACKUP UI
   Merely opening「備份與遷移」does not load dict_id=5 into memory.
   The ZSTD/WCDB decompress path in roam_migration runs when migration
   actually starts (user clicks begin backup/transfer). Without that,
   MAGIC5 (37 A4 30 EC 05 00 00 00) will not appear in roam_migration ranges.

3. DICT MAY LIVE OUTSIDE SCANNED MODULES
   dict_5 could be heap-allocated outside roam_migration/WeChat text/data
   sections. v2 intentionally avoids full-heap scan for speed; breakpoints
   on decompress symbols are the primary hook for heap captures.

V2 IMPROVEMENTS
---------------
- _migration_dict5_scan_v2.py replaces v1 for resigned capture
- Scans ONLY roam_migration + WeChat loaded sections (no full heap)
- Scan interval: 2 seconds (was 4)
- Logs ROUND timing (round_ms, per-module scan_ms) to detect hangs
- Sets breakpoints on ZSTD / decompress / WCDB / dict symbols in roam_migration
- On breakpoint: checks ARM64 x0, x1, x2 for MAGIC5 header, dumps 112640 bytes
- Shell script kills stale lldb before attach
- Shell script checks process age: auto-capture only if WeChat-Resigned
  has been running <= 180s (likely still in backup session)

RETRY INSTRUCTIONS
------------------
1. Quit any running WeChat (Cmd+Q).

2. Kill stale lldb if present:
     pgrep -lf lldb
     # kill <lldb-pid> if attached to WeChat

3. Launch WeChat-Resigned.app fresh:
     open $WECHAT_ZSTD_WORKSPACE (default: ./data)/WeChat-Resigned.app

4. Log in, open「備份與遷移」, then CLICK TO START backup/migration.
   Keep the migration flow active for the full 90-second scan.

5. Run capture (best within 3 minutes of launching WeChat):
     cd $WECHAT_ZSTD_WORKSPACE (default: ./data)
     ./capture_dict5_resigned.sh

   If WeChat was already running a while, either restart the app first,
   or force capture:
     CAPTURE_FORCE=1 ./capture_dict5_resigned.sh

6. Check results:
     cat migration_capture.log          # ROUND timing, BP_HIT, MAGIC5_HIT
     cat resigned_capture_summary.txt
     ls -la real_dict_5*.bin

7. On success, validate and decode:
     python3 validate_dict5.py real_dict_5.bin
     python3 bulk_decode_messages.py ...

LOG FILES
---------
  migration_capture.log      — v2 scan + breakpoint log (written by LLDB module)
  resigned_capture_run.log   — full lldb stdout/stderr
  resigned_capture_summary.txt — one-line outcome summary
