# Code Review 協作文件

## Current Status
- State: waiting-writer
- Current Round: 1
- Writer: Claude Code
- Reviewer: Codex
- Base Branch: main
- Feature Branch: main
- Goal: 找到並驗證 WeChat WCDB ZSTD `dict_id=5` 字典

## Context
- Active repo: `/Users/patrickchiho/Documents/Code/wechat-zstd-decode`
- Reference/older repo: `/Users/patrickchiho/Documents/Code/wechat-zstd-recovery`
- `wechat-zstd-recovery` 目前有未提交的 `SETUP.md` 修改，本輪不處理，避免覆蓋使用者既有變更。
- `wechat-zstd-decode/data` 目前只有 `.gitkeep`，沒有 `target_4134_from_db.blob`、`real_dict_5*.bin` 或其他候選字典。
- Codex 嘗試查詢 WeChat 進程時，本環境回傳 `pgrep: Cannot get process list`，因此本輪無法直接 attach live process。

## Writer Notes
### Round 1
待 Claude Code 填寫。

## Reviewer Notes
### Round 1 - Codex

#### Review Verdict
目前 repo 的研究結論是合理的：目標 blob 已確認需要 `dict_id=5`，但 `real_dict_5.bin` 尚未被成功擷取。不過現有工具鏈有幾個會阻塞下一次擷取/驗證的可重現性問題，應先修掉，再進行下一輪 live capture。

#### Findings

- [P1] `bin/capture_dict5_migration.sh:13`, `bin/capture_dict5_sudo.sh:13` - 腳本切到 workspace 後 source `${EXPORT}/_wechat_app_detect.sh`，但整理版 repo 不會把該檔複製到 `data/`。
  原因：預設 workspace 是 `./data`，目前只有 `.gitkeep`。因此 capture 流程會在 source 階段失敗，還沒開始掃描 `dict_id=5`。
  建議：改成 source repo 內的 `bin/_wechat_app_detect.sh`，或在啟動前明確同步 runtime helper 到 workspace。

- [P1] `bin/capture_dict5_migration.sh:15`, `bin/capture_dict5_sudo.sh:15`, `bin/capture_dict5_resigned.sh:11` - `SCAN_MODULE` 指向 `${EXPORT}/_migration_dict5_scan*.py`，但 repo 沒有保證 workspace 內存在這些檔案。
  原因：`data/` 是 gitignored runtime output，不應假設內含核心掃描模組。現在從乾淨 clone 跑會直接 `MISSING ${SCAN_MODULE}`。
  建議：直接從 repo `scripts/_migration_dict5_scan.py` / `scripts/_migration_dict5_scan_v6.py` import，或在腳本開始時 copy/sync 到 workspace 並記錄版本。

- [P1] `bin/capture_dict5_migration.sh:135`, `bin/capture_dict5_sudo.sh:152`, `bin/capture_dict5_resigned.sh:209`, `scripts/_migration_dict5_scan_v6.py:35` - 驗證腳本被硬編成 `${EXPORT}/validate_dict5.py`。
  原因：`validate_dict5.py` 實際在 repo 的 `scripts/` 內，workspace 內不存在時，成功擷取後也無法驗證，會讓「找到 dict_id=5」無法自動完成閉環。
  建議：使用 repo 路徑執行 `scripts/validate_dict5.py`，並把候選檔路徑作為參數傳入；同時保留 `WECHAT_ZSTD_WORKSPACE` 指向 runtime data。

- [P1] 多個 LLDB `.lldb` 腳本仍硬編 `/Users/patrickchiho/Projects/wechat-zstd-decode/lldb/lldb_capture_setup.py`。
  影響檔案包含 `lldb/lldb_capture_wcdb.lldb:7`、`lldb/lldb_capture_aggressive.lldb:11`、`lldb/lldb_manual_dict_resolve.lldb:11`、`lldb/lldb_capture_dict_resolve_only.lldb:9`、`lldb/lldb_symbol_and_scan.lldb:6`、`lldb/lldb_memory_scan_only.lldb:4`。
  原因：現在實際 repo 在 `/Users/patrickchiho/Documents/Code/wechat-zstd-decode`，舊路徑會導致 LLDB import 失敗。
  建議：不要在 `.lldb` 腳本內寫絕對 repo 路徑。改用 shell wrapper 以 `-o "command script import ${REPO_ROOT}/lldb/lldb_capture_setup.py"` 注入，或由 wrapper 生成臨時 `.lldb` 腳本。

- [P2] `scripts/extract_dict.py:7`, `scripts/static_slot5_table.py:8`, `scripts/static_slot5_analyze.py:6`, `scripts/try_wcdb_loader.py:15` - 多處把 `str(WORKSPACE)` 當成字面字串。
  原因：這些腳本不會讀 `workspace.py`，輸出/輸入路徑會變成相對路徑 `str(WORKSPACE)/...`，導致報告或 DB 路徑錯誤。
  建議：統一 import `WORKSPACE`，或改成 CLI 參數 `--workspace`。修完後用 smoke test 驗證輸出會落在 `data/` 或 `WECHAT_ZSTD_WORKSPACE`。

- [P2] `scripts/bulk_decode_messages.py:33` - decrypted DB 路徑硬編到 `/Users/patrickchiho/Downloads/wechat_dict_hunt/...`。
  原因：這讓 bulk report 只能在舊機器狀態下跑，無法作為穩定驗證流程。
  建議：提供 `--decrypted-root` 參數，預設可讀環境變數；README 只給範例，不要寫死。

- [P2] `scripts/extract_dict.py` 的關鍵字窗口掃描會產生高風險假陽性。
  原因：它找的是 XML 關鍵字密集區，並不是 ZSTD dictionary header `37 A4 30 EC 05 00 00 00` 或可驗證的 trained dictionary。它可能生成 `wechat_dict_5.bin` 但不是真正 `dict_id=5`。
  建議：標成 deprecated，或改名為 heuristic candidate extractor，並強制接 `validate_dict5.py`。

#### Verification Run This Round
- `python3 -m compileall -q scripts workspace.py`: pass
- `data/`: only `.gitkeep`; no test blobs or candidate dictionaries
- WeChat process check: blocked by local environment process-list limitation, no live attach attempted

## Writer Response
### Round 1
待 Claude Code 回應。

## Claude Code Next Task
### Round 1 Writer Task
你是 Writer Agent。請在 `/Users/patrickchiho/Documents/Code/wechat-zstd-decode` 修復第一輪 Codex review 的 P1 問題，目標是讓 capture 流程可以從乾淨 repo + workspace 跑起來。

請完成：
1. 修正 `bin/capture_dict5_migration.sh`、`bin/capture_dict5_sudo.sh`、`bin/capture_dict5_resigned.sh`，不要假設 helper/module/validator 已存在於 workspace。
2. 修正 LLDB `.lldb` 腳本的舊絕對路徑，或改由 shell wrapper 注入正確 repo 路徑。
3. 修正 `validate_dict5.py` 的調用路徑，保留 `WECHAT_ZSTD_WORKSPACE` 用於資料與輸出。
4. 最少加入一個 smoke test 或檢查命令，能在沒有 WeChat 進程時驗證腳本路徑不再指向舊位置。
5. 更新 `REVIEW.md` 的 `Writer Response`，說明修了哪些檔案、哪些尚未處理。
6. 執行：
   - `python3 -m compileall -q scripts workspace.py`
   - 適合的 shell syntax check，例如 `bash -n bin/*.sh`
7. commit：
   - `git add <changed files> REVIEW.md`
   - `git commit -m "fix: make dict5 capture workflow portable"`

## Review History
### Round 1 - 2026-06-18
- Codex 完成第一輪 review。
- 尚未找到 `real_dict_5.bin`；目前阻塞是 workspace 缺少 runtime inputs，以及 capture 腳本路徑不可重現。

