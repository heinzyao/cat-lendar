# cat-lendar

LINE chatbot：用自然語言管理一份**共享**的 Google 日曆。所有 LINE 用戶共用 app owner 的同一個日曆（Service Account 存取），沒有個別授權流程。NLP 走 Gemini（`app/services/nlp.py`，`google-genai` SDK）。

## 指令

```bash
uv run python -m pytest tests/ -q        # 測試；asyncio_mode=auto
./scripts/dev.sh                          # 本地開發（uvicorn + ngrok）
GOOGLE_CALENDAR_ID=<共享日曆 ID> ./scripts/deploy.sh   # 部署；此變數必填，未設會直接中止
```

- Cloud Run 服務名仍是 `line-calendar-bot`（`asia-east1`），專案改名前留下的，URL 不變，不要改。
- Docker image 用 Python 3.12；本地 venv 是 3.14（`line-bot-sdk` 的 Pydantic V1 警告只在本地出現）。

## 架構

```
app/
├── main.py                  # FastAPI 入口
├── config.py                # 所有環境變數（pydantic-settings），必填值缺了啟動即失敗
├── routes/
│   ├── webhook.py           # POST /webhook — LINE 訊息
│   └── notify.py            # Cloud Scheduler 內部端點（NOTIFY_SECRET 驗證）：
│                            #   POST /internal/notify 每分鐘推播提醒、POST /internal/sync 每 5 分鐘同步日曆
├── handlers/message.py      # ★ 核心：訊息 → NLP → 日曆操作 → 跨用戶通知
├── services/
│   ├── nlp.py               # ★ parse_intent / parse_update_details（schema 由 response_schema 約束）
│   ├── calendar.py          # Google Calendar CRUD
│   ├── calendar_notify.py   # 異動後推播給其他用戶
│   ├── calendar_sync.py     # 日曆 → Firestore 同步（狀態存 system/calendar_sync）
│   ├── notification.py      # 到期提醒發送
│   ├── auth.py              # Service Account 憑證
│   └── line_messaging.py    # reply / push / display name
├── models/                  # CalendarIntent、UserState 等
├── store/firestore.py       # Firestore CRUD
└── utils/                   # 時區（Asia/Taipei）、i18n 繁中訊息模板
```

### Firestore

```
users/{line_user_id}                 first_seen, last_seen — 只有傳過訊息的人才會收到跨用戶通知
user_prefs/{line_user_id}            default_reminder_minutes, notify_on_change（未設定視為開啟）
user_states/{line_user_id}           多筆選擇的暫存，TTL 5 分鐘（expires_at）
conversation_history/{line_user_id}  對話記憶，TTL 30 分鐘
reminders/{reminder_id}              line_user_id, event_id, reminder_at, sent …
system/calendar_sync                 同步狀態
```

- TTL 要在 GCP 端設 TTL policy，程式不會自己清。
- `UserState.action` 只有 `select_event_for_update`、`select_event_for_delete`。
- GCP 上還有 `oauth_states` collection 與其 TTL policy：OAuth 時代的殘留，程式已不用，看到不是 bug。

## 行為契約（改動時要保持）

- **一次新增多筆**：`CalendarIntentPayload.events` 有值時優先於 `event_details`。單筆失敗只標 ❌（`i18n.EVENT_CREATE_FAILED`），不中斷其餘；只有一筆時才照舊 raise。前幾筆已寫進日曆，整批回錯會讓使用者重送而重複建立。
- **跨用戶通知在 reply 之後發**，避免拖到 reply token 過期；要 await 完成，不可丟到背景（Cloud Run 回應後會節流 CPU）。
- **💡 推定說明**：Gemini 曾在這個自由文字欄位失控重複洗版。prompt 限 40 字之外，`_with_assumption_note` 以 `_NOTE_MAX_LEN`（80）截斷——不要拿掉程式端截斷。
- `parse_update_details` 失敗時 fallback 到 `intent.event_details`；選擇暫存的 candidates 要帶 end/location/description，二次解析才算得出持續時間。
- 新增的日曆事件 description 會附加操作者 `[LINE: {user_id}]`。

## 環境變數與 Secret

本地從 `.env` 讀；production 由 `deploy.sh` 從 Secret Manager 掛載（LINE 兩個 secret 在 Secret Manager 名為 `CATLENDAR_LINE_*`）。

`LINE_CHANNEL_SECRET`、`LINE_CHANNEL_ACCESS_TOKEN`、`GEMINI_API_KEY`、`GOOGLE_SERVICE_ACCOUNT_JSON`、`NOTIFY_SECRET`、`GOOGLE_CALENDAR_ID`、`GCP_PROJECT_ID`

- Gemini 配額綁 GCP 專案而非 key，且與 diffords-cocktails 共用。
- 新環境要先 `gcloud services enable generativelanguage.googleapis.com`。
- Service Account 金鑰失效：重跑 `scripts/setup_service_account.sh`。

## 測試

- 測試不得連外（Firestore / Gemini / LINE / Calendar），一律 mock；外部依賴用 `unittest.mock.AsyncMock`，環境變數用 `os.environ.setdefault` 注入。
- Production 驗證用簽章模擬 webhook，不要透過使用者的 LINE app。
