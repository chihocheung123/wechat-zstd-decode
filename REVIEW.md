# Code Review 協作文件

## Current Status
- State: waiting-runtime-capture
- Current Round: 3
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

### Round 2 - Codex Review of `48c1491`

#### Review Verdict
No blocking issues found in `fix: make dict5 capture workflow portable`.

The first-round P1 blockers are resolved:
- Capture scripts no longer require scanner/helper/validator files to be pre-copied into workspace.
- Regular, sudo, and resigned capture entries now import repo `scripts/_migration_dict5_scan_v6.py`.
- Validator calls now use repo `scripts/validate_dict5.py` while keeping runtime artifacts in `WECHAT_ZSTD_WORKSPACE`.
- LLDB scripts no longer import the old `/Users/patrickchiho/Projects/wechat-zstd-decode` path.

#### Verification Run
- `python3 -m compileall -q scripts workspace.py`: pass
- `bash -n bin/*.sh`: pass
- `LLDB_CAPTURE_SYNTAX_ONLY=1 ./bin/run_lldb_capture_90s.sh`: pass
- `WECHAT_ZSTD_REPO=... lldb -b -s lldb/lldb_capture_wcdb.lldb`: import pass; stops at expected no-process condition because this environment cannot list WeChat PID.

#### Residual Risk
- Live `dict_id=5` capture is still unverified because no WeChat process can be enumerated from this Codex environment.
- `data/` still lacks `target_4134_from_db.blob` and candidate dictionaries, so `validate_dict5.py` cannot prove a real `real_dict_5.bin` yet.

#### Next Runtime Step
Run this from an interactive local Terminal, not from Codex sandbox:

```bash
cd /Users/patrickchiho/Documents/Code/wechat-zstd-decode
export WECHAT_ZSTD_WORKSPACE="$PWD/data"
./bin/capture_dict5_migration.sh --app regular
```

During the 90-second window, use WeChat Backup & Migration actively. If `real_dict_5.bin` appears, validate:

```bash
python3 scripts/validate_dict5.py data/real_dict_5.bin
```

## Writer Response
### Round 1 - Codex Local Fallback
Claude CLI 由於外部資料傳輸風險被本環境拒絕執行，因此本輪由 Codex 以本地方式完成 Writer 修復，並保留 Git/REVIEW.md 交接紀錄。

已修復：
- `bin/capture_dict5_migration.sh`、`bin/capture_dict5_sudo.sh`、`bin/capture_dict5_resigned.sh` 不再假設 scanner/helper/validator 位於 workspace。
- 三個 capture 入口統一使用 repo 內的 `scripts/_migration_dict5_scan_v6.py`。
- 驗證改用 repo 內的 `scripts/validate_dict5.py`，並以 `WECHAT_ZSTD_WORKSPACE` 保存 runtime data。
- `scripts/_migration_dict5_scan_v6.py` 改由 `WECHAT_ZSTD_REPO` / `WECHAT_ZSTD_VALIDATE_SCRIPT` 定位 repo 與 validator。
- LLDB `.lldb` 腳本不再硬編舊的 `/Users/patrickchiho/Projects/wechat-zstd-decode` 路徑，改用 `WECHAT_ZSTD_REPO` 或目前目錄推導。

驗證：
- `rg` 確認 `bin/`、`lldb/`、`scripts/` 已無舊 `Projects/wechat-zstd-decode` 路徑。
- `python3 -m compileall -q scripts workspace.py`: pass
- `bash -n bin/*.sh`: pass
- `LLDB_CAPTURE_SYNTAX_ONLY=1 ./bin/run_lldb_capture_90s.sh`: pass，成功 import `/Users/patrickchiho/Documents/Code/wechat-zstd-decode/lldb/lldb_capture_setup.py`
- `WECHAT_ZSTD_REPO=... lldb -b -s lldb/lldb_capture_wcdb.lldb`: pass，成功 import；後續因本環境無法列出 WeChat PID 而停止，符合預期。
- `WECHAT_ZSTD_REPO=... lldb -b -s lldb/lldb_memory_scan_only.lldb`: pass，成功 import；後續提示需先 attach，符合預期。

尚未處理：
- P2 cleanup 尚未做。
- 尚未 live attach WeChat，因本環境無法列出進程，且沒有 workspace blob/candidate dictionary。

## Writer Notes
### Round 3 — Claude Code

**核心發現：WeChatAppEx 從未被 capture 過**

讀完所有腳本後發現一個重大漏洞：所有 capture 腳本都 **硬排除** WeChatAppEx（`case "$cmd" in *WeChatAppEx*) continue ;; esac`），但 SUMMARY.md 第 4 點明確說 dict_5 解壓可能在 WeChatAppEx 進程，而非主進程。診斷腳本 `run_lldb_capture_attach_diag.sh` 雖然會列出 WeChatAppEx 的 roam_migration，但沒有腳本真的掃描它。

**本輪改動：**

1. `bin/_wechat_app_detect.sh` — 新增兩個函式：
   - `find_wechatappex_pids()` — 找出所有 WeChatAppEx PID，設 `WECHATAPPEX_PIDS`、`WECHATAPPEX_COUNT`
   - `wechatappex_has_roam_migration()` — 快速 image-list 確認 roam_migration 是否已載入該 PID

2. `bin/capture_dict5_wechatappex.sh` — **新腳本**：
   - 找出 WeChatAppEx PID（可能有多個）
   - 優先選有 roam_migration 的 PID；如果都沒有則 fallback 到第一個（它可能在 90s 內才載入）
   - 以相同的 `_migration_dict5_scan_v6.py` MAGIC5 掃描邏輯跑 90 秒
   - 支援 `--diag` 模式：只列出 PID 和 roam_migration 狀態，不掃描

**驗證：**
- `bash -n bin/capture_dict5_wechatappex.sh`: pass
- `bash -n bin/_wechat_app_detect.sh`: pass
- `python3 -m compileall -q scripts workspace.py`: pass
- old path check (`Projects/wechat-zstd`): clean

**尚未處理：**
- P2 items（bulk_decode_messages.py 硬路徑、extract_dict.py heuristic 標記）— 留給後續輪次

**交接給 Codex 的問題：**
- `find_wechatappex_pids()` 用 `pgrep -f 'WeChatAppEx.app/Contents/MacOS/WeChatAppEx'`，若 WeChat 是 App Store 版路徑不同，請確認 pattern 是否需要調整
- `wechatappex_has_roam_migration()` 用 `lldb -b` attach 只查 image list，會短暫中斷 WeChatAppEx（幾毫秒）——這個副作用是否可接受？

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
- Codex local fallback 修復第一輪 P1 portability blockers，準備提交 `fix: make dict5 capture workflow portable`。

### Round 2 - 2026-06-18
- Codex review `48c1491`，未發現 blocking issue。
- 下一步需要在本機互動 Terminal 執行 runtime capture，Codex 沙盒無法完成 live attach。
