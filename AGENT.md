# AGENT.md — 多代理協作指引

本文件為 AI Agent 在此專案工作時的指引，包含專案地圖、角色分工與協作規範。
支援在 **Claude Code**、**OpenCode**、**Antigravity** 之間交接工作。

---

## 專案快照

| 項目 | 值 |
|------|-----|
| 專案 | Cat-Lendar |
| 語言 | Python 3.12（Docker）／Python 3.14（本地開發） |
| 套件管理 | uv |
| 部署 | Google Cloud Run (`asia-east1`) |
| 最新 Revision | `line-calendar-bot-00018-g7v` |
| 測試數量 | 67 個（`uv run python -m pytest tests/ -q`） |

---

## 架構概覽

**共享行事曆模式**：App owner 預先完成一次 OAuth 授權（Desktop app 類型），取得 refresh token 存入 Secret Manager。所有 LINE 用戶共用同一個 Google Calendar，無需個別授權流程。

---

## 關鍵檔案地圖

```
app/
├── config.py                 # 所有環境變數（pydantic-settings）
├── main.py                   # FastAPI 入口，路由掛載（webhook + notify）
├── handlers/message.py       # ★ 核心協調器：接收訊息 → NLP → 執行 → 跨用戶通知
├── services/
│   ├── nlp.py                # ★ Claude API 意圖解析（parse_intent + parse_update_details 二次解析）
│   ├── auth.py               # get_shared_credentials()：使用 app owner refresh token
│   ├── calendar.py           # Google Calendar CRUD（使用 settings.google_calendar_id）
│   ├── calendar_notify.py    # ★ 行事曆異動後推播通知給其他用戶
│   ├── notification.py       # 行程到期提醒發送（Cloud Run Scheduler 觸發）
│   └── line_messaging.py     # LINE reply / push / get_display_name
├── models/
│   ├── intent.py             # CalendarIntent（action, event_details, time_range, original_message…）
│   └── user.py               # UserState, ConversationMessage, ConversationHistory
├── store/
│   ├── firestore.py          # Firestore CRUD（users / user_prefs / user_states / conversation_history / reminders）
│   └── encryption.py         # AES-256-GCM 加解密（ENCRYPTION_KEY）
└── utils/
    ├── datetime_utils.py     # 時區、格式化（Asia/Taipei）
    └── i18n.py               # 繁體中文訊息模板
scripts/
├── get_token.py              # 一次性：app owner 授權取得 refresh token
├── deploy.sh                 # 建置 + 推送 + 部署到 Cloud Run
├── dev.sh                    # 本地開發（uvicorn + ngrok）
└── update_secret.sh          # 更新 Secret Manager 密鑰
tests/                        # 67 個測試，asyncio_mode=auto
```

---

## Firestore 結構

```
users/{line_user_id}
  first_seen: timestamp
  last_seen:  timestamp          ← 每次互動更新，供跨用戶通知使用

user_states/{line_user_id}       ← TTL 5 分鐘（expires_at）
  action, candidates, original_intent, expires_at

conversation_history/{line_user_id}  ← TTL 30 分鐘（updated_at）
  messages: [{role, content, timestamp}, ...]
  updated_at

reminders/{reminder_id}
  line_user_id, event_id, event_summary
  start_time, reminder_at, reminder_minutes
  sent: bool, created_at

user_prefs/{line_user_id}
  default_reminder_minutes: int | null
  notify_on_change: bool              ← 預設 True（未設定時視為開啟）
  updated_at
```

### UserState.action 合法值

| action | 情境 |
|--------|------|
| `select_event_for_update` | 多筆事件更新選擇 |
| `select_event_for_delete` | 多筆事件刪除選擇 |

---

## 環境變數

所有密鑰存放於 **GCP Secret Manager**，本地開發從 `.env` 讀取。
**不要在任何文件中記錄密鑰明文。**

| 變數 | 來源 | 說明 |
|------|------|------|
| `LINE_CHANNEL_SECRET` | Secret Manager | LINE Channel 驗簽金鑰 |
| `LINE_CHANNEL_ACCESS_TOKEN` | Secret Manager | LINE push/reply token |
| `ANTHROPIC_API_KEY` | Secret Manager | Claude API |
| `GOOGLE_CLIENT_ID` | Secret Manager | OAuth Client ID（Desktop app 類型） |
| `GOOGLE_CLIENT_SECRET` | Secret Manager | OAuth Client Secret |
| `GOOGLE_REFRESH_TOKEN` | Secret Manager | App owner 預授權 refresh token |
| `ENCRYPTION_KEY` | Secret Manager | AES-256-GCM 金鑰（base64 32 bytes） |
| `GCP_PROJECT_ID` | deploy.sh 自動讀取 | GCP 專案 ID |
| `GOOGLE_CALENDAR_ID` | config 預設 `primary` | 目標行事曆 ID |

---

## 已知限制與陷阱

| 問題 | 說明 |
|------|------|
| Python 3.14 警告 | `line-bot-sdk` 使用 Pydantic V1，Docker image 固定使用 3.12 無此問題 |
| Firestore TTL | `user_states` 5 分鐘、`conversation_history` 30 分鐘，需在 GCP 設定 TTL policy |
| refresh token 失效 | 重新執行 `scripts/get_token.py` 並更新 Secret Manager |
| 跨用戶通知 | 只有曾傳訊息給 bot 的用戶才會被登記到 `users/` 集合，才能收到通知 |
| get_display_name | LINE Profile API 需用戶加 bot 為好友，失敗時 fallback 到 `用戶 ...末四碼` |

---

## 代理角色與工具分配

### Claude Code
**適合工作**：完整功能開發、跨多檔案重構、測試撰寫
**進入條件**：閱讀 `handlers/message.py`（核心流程）與 `store/firestore.py`（資料結構）
**完成條件**：`uv run python -m pytest tests/ -q` 全部通過

### OpenCode
**適合工作**：單一模組修改、Bug 修復、程式碼審查
**進入條件**：閱讀目標模組及其直接依賴
**完成條件**：修改最小化，測試通過

### Antigravity
**適合工作**：探索性分析、文件撰寫、架構建議
**進入條件**：閱讀本 AGENT.md 與 README.md 取得全貌
**完成條件**：產出可直接使用的文件或清楚的實作建議

### 共通規則
- 測試不得連線外部服務（Firestore / Claude / LINE）—— 一律 mock
- 外部依賴使用 `unittest.mock.AsyncMock`
- 環境變數使用 `os.environ.setdefault` 注入

---

## 協作交接規範

當一個 agent 需要將工作交給另一個 agent 時，在 commit message 或 PR description 說明：

```
交接事項：
- 已完成：<已做的事>
- 待完成：<下一步要做的事>
- 注意：<需要特別留意的邊界條件或陷阱>
```

---

## 修改流程

```
1. 閱讀相關原始碼（勿假設）
2. 最小化修改範圍
3. 執行 uv run python -m pytest tests/ -q → 全部通過
4. git commit（Conventional Commits 格式）
5. 部署：bash scripts/deploy.sh
6. 更新 AGENT.md 的「最新 Revision」
```

## Commit 格式

```
<type>: <description>
```

| type | 用途 |
|------|------|
| `feat` | 新功能 |
| `fix` | Bug 修復 |
| `test` | 測試新增/修改 |
| `docs` | 文件更新 |
| `refactor` | 重構（不改行為） |
| `chore` | 維護性工作 |

---

## 已完成功能

- [x] 共享 Google Calendar（app owner 一次性授權，所有用戶共用）
- [x] 多筆事件選擇流程（update / delete）
- [x] NLP 二次解析（parse_update_details）：相對時間更新保持持續時間
- [x] 對話記憶（conversation memory）：多輪對話上下文理解，支援代名詞、省略、補充資訊
- [x] 行程提醒（LINE push message，Cloud Run Scheduler 觸發）
- [x] 預設提醒設定（每個新行程自動套用）
- [x] description 欄位附加操作者 LINE ID `[LINE: {user_id}]`
- [x] 跨用戶異動通知（新增／修改／刪除後推播給其他所有用戶）
- [x] NLP 推定模式：模糊指令自動推定合理預設值，減少反覆詢問
- [x] 跨用戶通知修復：改為 await 同步執行，避免 Cloud Run CPU throttling

- [x] 通知訂閱設定：用戶可開關接收異動通知（「開啟通知」/「關閉通知」）
- [x] 時間格式加年份：所有行程顯示改為 YYYY/MM/DD 格式
---

## 擴充方向（待辦）

- [ ] 支援 recurring events（每週定期行程）
- [ ] 使用量統計（每日 Claude API 呼叫次數）
