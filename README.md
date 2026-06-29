# Cat-Lendar

[English](#english) | [繁體中文](#繁體中文)

---

## English

A LINE chatbot that manages a shared calendar using natural language via LINE messages. All users share the same Google Calendar without the need for individual authorization.

### Features

- **Add Event**: "Meeting tomorrow from 3 PM to 5 PM"
- **Query Events**: "What's on the schedule for this week?" "What time am I free tomorrow?"
- **Modify Event**: "Change tomorrow's meeting to the day after tomorrow"
- **Delete Event**: "Cancel Friday's dinner"
- **Fuzzy Matching Menu**: Displays a choice menu when multiple events match
- **Event Reminders**: "Meeting tomorrow at 2 PM, remind me 15 minutes before"
- **Default Reminder**: "Set default reminder 30 minutes before" (Auto-applied to new events)
- **Cross-user Notification**: Push notifications to other users when someone adds, edits, or deletes an event.
- **Context-aware Memory**: Multi-turn conversation capability, understands pronouns and implicit context.
- **Notification Settings**: "Turn off notifications" "Turn on notifications" — Easily toggle event sync notifications.
- **API Rate Limiting**: Max 10 Gemini API calls per minute per user (sliding window) to prevent API abuse.

### System Architecture

**Shared Calendar Mode**: A Service Account is granted access to the shared Google Calendar once, and all users share that calendar. No individual login is required.

```text
LINE User (Anyone)
   │  Send Message
   ▼
Cloud Run (FastAPI)
   └── POST /webhook
         │
         ├── Gemini API          (Natural Language Parsing)
         ├── Google Calendar API (Shared Calendar CRUD)
         └── Cloud Firestore     (Dialogue State, Reminders, User Registry)
                │
                └── Secret Manager (API Keys, Service Account Key)
```

| Component | Technology |
|-----------|------------|
| Language / Framework | Python 3.12 + FastAPI |
| Deployment | Google Cloud Run (asia-east1) |
| NLP | Gemini API (gemini-2.5-flash) |
| Database | Cloud Firestore |
| Secrets | Google Secret Manager |
| Package Manager| uv |

### Quick Start

#### Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- [ngrok](https://ngrok.com/) (for local development)
- Google Cloud SDK (`gcloud`)

#### Local Development

```bash
# Install dependencies
uv sync

# Copy env template
cp .env.example .env
# Fill in all necessary API keys (see details below)

# Start dev server + ngrok
bash scripts/dev.sh
```

#### Deploy to Google Cloud

```bash
bash scripts/deploy.sh
```

Please refer to [DEPLOYMENT.md](DEPLOYMENT.md) for detailed steps.

### Environment Variables

| Variable | Description |
|----------|-------------|
| `LINE_CHANNEL_SECRET` | LINE Channel Secret |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Channel Access Token |
| `GEMINI_API_KEY` | Gemini API Key |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Service Account JSON key (Run `scripts/setup_service_account.sh` for one-time setup) |
| `GOOGLE_CALENDAR_ID` | Shared Calendar ID granted to the service account |
| `ENCRYPTION_KEY` | AES-256-GCM encryption key (base64, 32 bytes) |
| `GCP_PROJECT_ID` | GCP Project ID |
| `NOTIFY_SECRET` | `X-Internal-Secret` header for `/internal/*` endpoints (called by Cloud Scheduler) |
| `TIMEZONE` | Timezone (Default: `Asia/Taipei`) |

Generate ENCRYPTION_KEY:

```bash
python3 -c "import os, base64; print(base64.b64encode(os.urandom(32)).decode())"
```

Set up the Service Account (one-time setup):

```bash
bash scripts/setup_service_account.sh
```

### Project Structure

```text
cat-lendar/
├── app/
│   ├── main.py                 # FastAPI Endpoint, /health checks
│   ├── config.py               # pydantic-settings bindings
│   ├── routes/
│   │   ├── webhook.py          # POST /webhook (LINE event reception)
│   │   └── notify.py           # POST /notify (Scheduled reminder triggering)
│   ├── services/
│   │   ├── nlp.py              # Gemini API intent parsing
│   │   ├── calendar.py         # Google Calendar CRUD
│   │   ├── calendar_notify.py  # Cross-user event notifications
│   │   ├── notification.py     # Scheduled reminder dispatcher
│   │   ├── line_messaging.py   # LINE reply / push / get_display_name
│   │   └── auth.py             # Shared Google Credentials
│   ├── models/
│   │   ├── intent.py           # CalendarIntent, EventDetails
│   │   └── user.py             # UserState, ConversationMessage
│   ├── store/
│   │   ├── firestore.py        # Firestore CRUD operations
│   │   └── encryption.py       # AES-256-GCM encryption/decryption
│   ├── handlers/
│   │   └── message.py          # Message handling coordinator
│   └── utils/
│       ├── datetime_utils.py   # Timezone/Datetime formatters
│       └── i18n.py             # Traditional Chinese message templates
├── scripts/
│   ├── setup_service_account.sh # One-time Service Account setup
│   ├── deploy.sh               # Build + Push + Deploy to Cloud Run
│   ├── dev.sh                  # Local Dev (uvicorn + ngrok)
│   └── update_secret.sh        # Update Secret Manager keys
├── tests/                      # 70 tests, asyncio_mode=auto
├── Dockerfile
├── pyproject.toml
└── DEPLOYMENT.md               # Complete deployment guide
```

### Usage Instructions

#### Supported Commands Examples

```text
Add Event
"Dentist appointment tomorrow at 10 AM"
"Project meeting next Wed from 2 to 4 PM at Tower 101"
"Valentine's dinner Feb 14, remind 30 mins before"

Query Events
"What is scheduled today?"
"Events for this week"
"Schedule for next Mon to Fri"

Modify Event
"Move tomorrow's dentist to the same time the day after tomorrow"
"Postpone the meeting by one hour"

Delete Event
"Cancel today's dentist"
"Delete Friday's dinner"

Reminder Settings
"Set a 15 min reminder for tomorrow's meeting"
"Set default reminder an hour before"
"Turn off default reminder"

Notification Settings
"Turn off notifications" → Stops receiving event modification alerts from others
"Turn on notifications"  → Resumes receiving event modification alerts (enabled by default)

Other
"Help" or "說明"          → Show function guide
```

### Development

#### Running Tests

```bash
uv run python -m pytest tests/ -q
```

Total of 70 tests covering:

| Test File | Coverage |
|-----------|----------|
| `test_encryption.py` | AES-256-GCM encryption/decryption |
| `test_datetime_utils.py` | Timezone formatting and conversion |
| `test_models.py` | CalendarIntent validation, ActionType |
| `test_api.py` | /health, webhook signature validation |
| `test_nlp_update.py` | Secondary NLP parsing |
| `test_message_update.py` | Multi-event menu selection update/delete |
| `test_conversation_memory.py` | Dialogue state memory, multi-turn NLP context |
| `test_calendar_notify.py` | Cross-user push notifications |
| `test_notification.py` | Scheduled reminder triggering |

### License

MIT

---

## 繁體中文

透過 LINE 訊息以自然語言管理共享行事曆的聊天機器人。所有用戶共用同一個 Google Calendar，無需個別授權。

### 功能

- **新增行程**：「明天下午三點開會到五點」
- **查詢行程**：「這週有什麼行程？」「明天幾點有空？」
- **修改行程**：「把明天的開會改到後天」
- **刪除行程**：「取消週五的晚餐」
- **多筆模糊匹配**：找到多筆符合事件時列出選單讓使用者選擇
- **行程提醒**：「明天下午 2 點開會，提前 15 分鐘提醒」
- **預設提醒**：「設定預設提醒 30 分鐘前」（所有新行程自動套用）
- **跨用戶通知**：任何人新增／修改／刪除行程時，自動推播通知其他用戶
- **對話記憶**：多輪對話上下文理解，支援代名詞與省略句

- **通知設定**：「關閉通知」「開啟通知」——自由開關行程異動通知
- **API 速率限制**：每位用戶每分鐘最多 10 次 Gemini API 呼叫（滑動視窗演算法），防止 API 濫用
### 系統架構

**共享行事曆模式**：以 Service Account 一次性授權存取共享 Google Calendar，所有用戶共用同一個行事曆，無需個別登入。

```text
LINE User（任何人）
   │  傳送訊息
   ▼
Cloud Run (FastAPI)
   └── POST /webhook
         │
         ├── Gemini API          (自然語言解析)
         ├── Google Calendar API (共享行程 CRUD)
         └── Cloud Firestore     (對話狀態、提醒、用戶登記)
                │
                └── Secret Manager (API 金鑰、Service Account 金鑰)
```

| 元件 | 技術 |
|------|------|
| 語言 / 框架 | Python 3.12 + FastAPI |
| 部署平台 | Google Cloud Run (asia-east1) |
| NLP | Gemini API (gemini-2.5-flash) |
| 資料庫 | Cloud Firestore |
| 密鑰管理 | Google Secret Manager |
| 套件管理 | uv |

### 快速開始

#### 前置需求

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- [ngrok](https://ngrok.com/)（本地開發用）
- Google Cloud SDK (`gcloud`)

#### 本地開發

```bash
# 安裝依賴
uv sync

# 複製環境變數範本
cp .env.example .env
# 填入所有必要的 API 金鑰（見下方說明）

# 啟動開發伺服器 + ngrok
bash scripts/dev.sh
```

#### 部署到 Google Cloud

```bash
bash scripts/deploy.sh
```

詳細步驟請參閱 [DEPLOYMENT.md](DEPLOYMENT.md)。

### 環境變數

| 變數 | 說明 |
|------|------|
| `LINE_CHANNEL_SECRET` | LINE Channel Secret |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Channel Access Token |
| `GEMINI_API_KEY` | Gemini API 金鑰 |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Service Account JSON 金鑰（執行 `scripts/setup_service_account.sh` 一次性設定） |
| `GOOGLE_CALENDAR_ID` | 已分享給 service account 的共享行事曆 ID |
| `ENCRYPTION_KEY` | AES-256-GCM 加密金鑰（base64，32 bytes） |
| `GCP_PROJECT_ID` | GCP 專案 ID |
| `NOTIFY_SECRET` | `/internal/*` 端點的 `X-Internal-Secret` 驗證標頭（由 Cloud Scheduler 呼叫） |
| `TIMEZONE` | 時區（預設 `Asia/Taipei`） |

產生 ENCRYPTION_KEY：

```bash
python3 -c "import os, base64; print(base64.b64encode(os.urandom(32)).decode())"
```

設定 Service Account（一次性）：

```bash
bash scripts/setup_service_account.sh
```

### 專案結構

```text
cat-lendar/
├── app/
│   ├── main.py                 # FastAPI 入口，/health 端點
│   ├── config.py               # pydantic-settings 設定
│   ├── routes/
│   │   ├── webhook.py          # POST /webhook（LINE 事件接收）
│   │   └── notify.py           # POST /notify（到期提醒排程）
│   ├── services/
│   │   ├── nlp.py              # Gemini API 意圖解析
│   │   ├── calendar.py         # Google Calendar CRUD
│   │   ├── calendar_notify.py  # 跨用戶異動推播通知
│   │   ├── notification.py     # 行程提醒發送
│   │   ├── line_messaging.py   # LINE reply / push / get_display_name
│   │   └── auth.py             # 共享 Google Credentials
│   ├── models/
│   │   ├── intent.py           # CalendarIntent, EventDetails
│   │   └── user.py             # UserState, ConversationMessage
│   ├── store/
│   │   ├── firestore.py        # Firestore CRUD
│   │   └── encryption.py       # AES-256-GCM 加解密
│   ├── handlers/
│   │   └── message.py          # 訊息處理協調器
│   └── utils/
│       ├── datetime_utils.py   # 時區 / 時間格式化
│       └── i18n.py             # 繁體中文訊息模板
├── scripts/
│   ├── setup_service_account.sh # 一次性設定 Service Account
│   ├── deploy.sh               # 建置 + 推送 + 部署到 Cloud Run
│   ├── dev.sh                  # 本地開發（uvicorn + ngrok）
│   └── update_secret.sh        # 更新 Secret Manager 密鑰
├── tests/                      # 70 個測試，asyncio_mode=auto
├── Dockerfile
├── pyproject.toml
└── DEPLOYMENT.md               # 完整部署指南
```

### 使用說明

#### 支援的指令範例

```text
新增行程
「明天早上十點牙醫」
「下週三下午兩點到四點開專案會議，地點在 101 大樓」
「2月14日情人節晚餐，提前 30 分鐘提醒」

查詢行程
「今天有什麼行程？」
「這週的行程」
「下週一到週五的安排」

修改行程
「把明天的牙醫改到後天同一時間」
「把開會時間延後一小時」

刪除行程
「取消今天的牙醫」
「刪除週五晚餐」

提醒設定
「幫明天的開會設定 15 分鐘前提醒」
「設定預設提醒 30 分鐘前」
「關閉預設提醒」

通知設定
「關閉通知」      → 不再接收其他人的行程異動通知
「開啟通知」      → 恢復接收異動通知（預設為開啟）
其他
「說明」或「help」  → 顯示功能說明
```

### 開發

#### 執行測試

```bash
uv run python -m pytest tests/ -q
```

共 70 個測試，涵蓋：

| 測試檔案 | 涵蓋範圍 |
|---------|---------|
| `test_encryption.py` | AES-256-GCM 加解密 |
| `test_datetime_utils.py` | 時區轉換、時間格式化 |
| `test_models.py` | CalendarIntent 驗證、ActionType |
| `test_api.py` | /health、webhook 簽名驗證 |
| `test_nlp_update.py` | NLP 二次解析 |
| `test_message_update.py` | 多事件選擇後更新／刪除 |
| `test_conversation_memory.py` | 對話記憶讀寫、NLP 多輪上下文 |
| `test_calendar_notify.py` | 跨用戶異動推播通知 |
| `test_notification.py` | 行程到期提醒發送 |

### License

MIT
