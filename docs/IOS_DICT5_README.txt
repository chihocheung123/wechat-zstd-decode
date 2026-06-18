WeChat dict_id=5 — iOS Device Capture Path
==========================================
Date: 2026-06-16

WHEN TO USE THIS PATH
---------------------
If Mac capture (v5/v6) completes 90 seconds with phase1_magic5=0 and
phase2_magic5=0 during active backup/migration, dict_5 is likely NOT loaded
into the Mac WeChat process.

dict_5 is an iOS export/migration ZSTD dictionary (MAGIC5 header:
37 A4 30 EC 05 00 00 00, 112640 bytes). Mac local DB uses dict_id=0.
The dictionary may only exist on the iOS device while iOS WeChat is running
backup/export — not in roam_migration __TEXT/__DATA or Mac heap even when
the Mac migration UI is active.

v6 Mac scan covers:
  Phase 1 — roam_migration __TEXT/__DATA (~4 MB)
  Phase 2 — all rw- memory regions (<=50 MB each, skip __LINKEDIT)

If both phases report magic5=0 during an active migration, try iOS capture.

iOS CAPTURE (jailbroken or developer-provisioned device)
--------------------------------------------------------
1. Install WeChat on iOS device; ensure lldb can attach (developer disk image).
2. Open WeChat on iOS → Settings → Backup & Migration → start export/backup.
3. Attach lldb to WeChat on device while migration is running:
     lldb
     platform select remote-ios
     process attach --name WeChat
4. Scan for MAGIC5 (37 A4 30 EC 05 00 00 00) in readable memory.
5. Dump 112640 bytes at each hit; copy real_dict_5.bin to this Mac.
6. Validate:
     cd $WECHAT_ZSTD_WORKSPACE (default: ./data)
     python3 validate_dict5.py real_dict_5.bin

NON-JAILBROKEN iOS
------------------
- Encrypted Finder/iTunes backup + export sqlite are already in this workspace.
- Compressed blobs (WCDB_CT=2) still need dict_5 from runtime or another channel.
- Static export DB alone does not contain the dictionary bytes.

MAC FALLBACK WITHOUT dict_5
---------------------------
- Decrypted Mac DB (dict_id=0) covers messages synced after ~2026-05-19.
- See RECOVERY_SUCCESS.txt for MesLocalID 4134 (no dict_5 needed).
- iOS-only Apr–May 2026 messages (~2,435 rows) need dict_5 OR full re-sync to Mac.

REFERENCES
----------
  MIGRATION_CAPTURE_README.txt  — section D (iOS device capture)
  DICT5_RECOVERY_VERDICT.txt    — Mac scan verdict and hypotheses
  capture_dict_5_instructions.txt — when dict_5 is required vs not
