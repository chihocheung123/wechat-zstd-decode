dict_id=5 migration capture (備份與遷移)
========================================

YES — use regular WeChat.app for backup/migration UI
----------------------------------------------------
WeChat-Debug (4.1.7, adhoc) often cannot open 備份與遷移. Regular WeChat.app
(4.1.9, App Store build) includes roam_migration.framework and the full menu.

Only one can run at a time (same bundle id). Quit WeChat-Debug before launching
/Applications/WeChat.app.

Quick start (regular WeChat)
----------------------------
1. Quit WeChat-Debug and any lldb attached to WeChat.
2. Open /Applications/WeChat.app and sign in.
3. Run:
     cd $WECHAT_ZSTD_WORKSPACE (default: ./data)
     ./capture_dict5_migration.sh --app regular
4. After the 5s countdown: menu → 備份與遷移 → 遷移 or 備份; keep open 90s.
5. Check real_dict_5.bin or: python3 validate_dict5.py

If attach fails ("Not allowed to attach to process")
----------------------------------------------------
Use the sudo wrapper in an interactive Terminal:

     ./capture_dict5_sudo.sh --app regular

You will be prompted for your macOS login password. Sudo grants root task_for_pid,
which is required for many App Store / hardened-runtime processes.

Auto-detect (either app running)
--------------------------------
  ./capture_dict5_migration.sh
  ./capture_dict5_migration.sh --app auto    # prefers regular WeChat if running

WeChat-Debug fallback
---------------------
  ./capture_dict5_migration.sh --app debug

LLDB attach notes
-----------------
- WeChat-Debug: adhoc-signed; lldb attach usually works without sudo.
- Regular WeChat: Apple-signed + hardened runtime + app sandbox; user lldb attach
  often fails with task_for_pid KERN_FAILURE (0x5). Use capture_dict5_sudo.sh.

Developer Mode status (this Mac)
--------------------------------
Checked 2026-06-15 on macOS 26.4.1 (Build 25E253):

  sysctl kern.developer_mode     → unknown oid (not exposed on this OS)
  devmode_status                 → not installed
  spctl developer-mode           → no status subcommand (only enable-terminal)
  /Library/Preferences/...developer_mode → domain does not exist

Cannot confirm Developer Mode on/off from CLI on this build. If sudo attach
still fails, check System Settings → Privacy & Security for Developer Mode
(iOS-style toggle may appear on newer macOS) and ensure Terminal/iTerm is listed
under Developer Tools (spctl developer-mode enable-terminal).

Why attach failed (2026-06-15 run, pid 96721)
----------------------------------------------
migration_capture_run.log:

  process attach --pid 96721
  error: attach failed (Not allowed to attach to process)

Console / debugserver (subsystem com.apple.dt.lldb):

  task_for_pid(96721) failed: err = 0x00000005 ((os/kern) failure)

Interpretation:
- debugserver could not obtain a task port for the WeChat process.
- App Store WeChat has hardened runtime + app sandbox and lacks get-task-allow.
- The 90s memory scan never ran because attach failed before migration_capture_90s.
- User was in backup UI but capture could not read process memory.

Other attach errors
-------------------
  "process already being debugged"
    → Quit other lldb sessions first (only one debugger per process).

  sudo: a password is required
    → Run capture_dict5_sudo.sh in interactive Terminal, not background agent.

Inspect denial logs yourself
----------------------------
  /usr/bin/log show --predicate 'subsystem == "com.apple.dt.lldb"' --last 30m
  /usr/bin/log show --predicate 'eventMessage CONTAINS "task_for_pid"' --last 1h

Best retry path (recommended order)
-----------------------------------
1. Quit all lldb / debug sessions attached to WeChat.
2. Launch /Applications/WeChat.app (regular, not WeChat-Debug).
3. In Terminal:
     cd $WECHAT_ZSTD_WORKSPACE (default: ./data)
     ./capture_dict5_sudo.sh --app regular
4. Enter sudo password when prompted.
5. After 5s countdown: 左下角選單 → 備份與遷移 → keep migration UI open 90s.
6. Verify: python3 validate_dict5.py && ls -la real_dict_5*.bin

If sudo attach still fails
--------------------------

A) WeChat-Debug + lldb (attach works, but no backup menu)
   - Attach succeeds on adhoc WeChat-Debug, but 備份與遷移 is often missing.
   - Prior scans on WeChat-Debug found MAGIC5=0 even with UI activity.
   - Manually dlopen roam_migration into a running process is NOT practical:
     the framework is already embedded in regular WeChat; you cannot inject it
     into WeChat-Debug to unlock the backup UI. DYLD_INSERT_LIBRARIES is blocked
     by hardened runtime / library validation on both builds.

B) Memory read without attach (macOS limitations)
   - No /proc on macOS. vmmap, heap, sample attach via task_for_pid and hit the
     same restriction.
   - Core dump (kill -ABRT) of sandboxed GUI apps is blocked without attach.
   - lldb memory read requires a successful attach.

C) Frida / other tools
   - frida is not installed on this Mac (which frida → not found).
   - Frida also needs task port / code injection; same hardened-runtime wall.
   - Not a viable bypass for App Store WeChat without jailbreak-level privileges.

D) iOS device capture (most reliable for dict_id=5)
   dict_5 is an iOS export/migration dictionary. It may only load on iOS during
   backup/export, not on Mac even with migration UI open.

   Options on a jailbroken or developer-provisioned iOS device:
   - lldb attach to WeChat on device while starting iOS backup/export.
   - Scan for MAGIC5 37 A4 30 EC 05 00 00 00, dump 112640 bytes.
   - Copy real_dict_5.bin back to this Mac and run validate_dict5.py.

   On non-jailbroken iOS: use encrypted iTunes/Finder backup + existing export
   sqlite (already in workspace); dict must still be recovered from runtime or
   another channel — static export DB blobs alone do not contain the dict.

E) Hybrid path without dict_5
   Mac wechat-decrypt DB covers messages synced after ~2026-05-19 (dict_id=0).
   See RECOVERY_SUCCESS.txt for MesLocalID 4134. iOS-only Apr–May messages
   still need dict_5 or full re-sync to Mac.

pgrep main process:
  pgrep -lf '/Applications/WeChat.app/Contents/MacOS/WeChat'
  pgrep -lf 'WeChat-Debug.app/Contents/MacOS/WeChat'

Scripts
-------
  capture_dict5_migration.sh   — user lldb attach (works for WeChat-Debug)
  capture_dict5_sudo.sh        — sudo lldb attach (retry for regular WeChat)
  _migration_dict5_scan.py     — 90s MAGIC5 scan (shared by both)
  migration_capture_run.log    — last capture stdout / errors
  migration_capture_summary.txt — hit count + validate result
