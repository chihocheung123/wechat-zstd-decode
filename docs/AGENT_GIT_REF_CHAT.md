# Git Ref Agent Chat

這個專案支援用 Git 自訂 ref 當 Claude Code 和 Codex 的留言板。

留言存在：

```text
refs/agent-chat/main
```

它不是 branch，不會出現在 branch list，不會進工作區，也不會污染程式碼 diff。每條留言是一行 JSON，核心欄位是：

```text
id, from, type, reply_to, time, commit, body
```

## 指令

讀最近留言：

```bash
bin/agent-check
bin/agent-check -n 5
bin/agent-chat check --json
```

新增留言：

```bash
bin/agent-send --from claude --type review -m "完成 cache 實作，已跑單測，風險是失效策略尚未覆蓋批次更新。"
bin/agent-send --from codex --type risk --reply-to m000001 -m "商品更新後沒有清 cache，可能回傳舊價格。"
bin/agent-send --from claude --type done --reply-to m000002 -m "已在商品更新流程補 cache invalidation，並新增測試。"
```

可用 type：

```text
review  寫完後交代改了什麼、怎樣驗證、哪裡沒把握
risk    reviewer 指出的問題，reply_to 指向 review 或 done
done    writer 處理完某條 risk，附上驗證結果
note    其他上下文
```

## Agent 約定

Writer Agent：

```text
1. 動手前先執行 bin/agent-check -n 10
2. 修改程式碼
3. 跑測試或檢查
4. git commit
5. bin/agent-send --from claude --type review -m "..."
```

Reviewer Agent：

```text
1. 先執行 bin/agent-check -n 10
2. 用 git log / git diff 審最新改動
3. 有問題只寫 risk，不直接改程式碼
4. bin/agent-send --from codex --type risk --reply-to <review_id> -m "..."
5. 沒有 blocking issue 時，用 note 或 review 明確說明
```

## 並發

`agent-send` 使用 `git update-ref <old-oid>` 做 compare-and-swap。若兩個 Agent 同時留言，工具會重讀、重試，避免後寫覆蓋前寫。

仍建議高風險任務採用輪流流程：

```text
Claude Code 寫 -> commit -> send review
Codex 讀 -> git diff -> send risk
Claude Code 修 -> commit -> send done
Codex 再審
```

## 分享到遠端

自訂 ref 不會隨一般 `git push` 自動推送。如果兩個 Agent 不在同一個 local repo，需要手動同步：

```bash
git push origin refs/agent-chat/main:refs/agent-chat/main
git fetch origin refs/agent-chat/main:refs/agent-chat/main
```

