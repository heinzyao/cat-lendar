# LINE Calendar Bot

透過 LINE 訊息以自然語言管理 Google Calendar 的聊天機器人。每位使用者各自授權自己的 Google Calendar，支援多人同時使用。

## 功能

- **新增行程**：「明天下午三點開會到五點」
- **查詢行程**：「這週有什麼行程？」「明天幾點有空？」
- **修改行程**：「把明天的開會改到後天」
- **刪除行程**：「取消週五的晚餐」
- **多筆模糊匹配**：找到多筆符合事件時列出選單讓使用者選擇

## 系統架構

```
LINE User ──webhook──> Cloud Run (FastAPI)
                         ├── Claude API (自然語言解析)
                         ├── Google Calendar API (CRUD)
                         └── Firestore (加密 token 儲存)
```

| 元件 | 技術 |
|------|------|
| 語言 / 框架 | Python 3.12 + FastAPI |
| 部署平台 | Google Cloud Run (asia-east1) |
| NLP | Claude API (claude-sonnet-4-5) |
| 資料庫 | Cloud Firestore |
| Token 加密 | AES-256-GCM |
| 密鑰管理 | Google Secret Manager |
| 套件管理 | uv |

## 快速開始

### 前置需求

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- [ngrok](https://ngrok.com/)（本地開發用）
- Google Cloud SDK (`gcloud`)

### 本地開發

```bash
# 安裝依賴
uv sync

# 複製環境變數範本
cp .env.example .env
# 填入所有必要的 API 金鑰（見下方說明）

# 啟動開發伺服器 + ngrok
bash scripts/dev.sh
```

啟動後腳本會輸出：
- **Webhook URL**：貼到 LINE Developers Console
- **OAuth Redirect URI**：加入 GCP OAuth 2.0 Credentials

### 部署到 Google Cloud

首次部署（含 GCP 基礎建設設定）：

```bash
# 一次性 GCP 設定（Firestore、Secret Manager、Service Account 等）
bash scripts/setup_gcp.sh

# 部署
bash scripts/deploy.sh
```

詳細步驟請參閱 [DEPLOYMENT.md](DEPLOYMENT.md)。

## 環境變數

| 變數 | 說明 |
|------|------|
| `LINE_CHANNEL_SECRET` | LINE Channel Secret |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Channel Access Token |
| `ANTHROPIC_API_KEY` | Claude API 金鑰 |
| `GOOGLE_CLIENT_ID` | Google OAuth Client ID |
| `GOOGLE_CLIENT_SECRET` | Google OAuth Client Secret |
| `GOOGLE_REDIRECT_URI` | OAuth 回調 URL（`https://<your-service>/oauth/callback`） |
| `ENCRYPTION_KEY` | AES-256-GCM 加密金鑰（base64，32 bytes） |
| `GCP_PROJECT_ID` | GCP 專案 ID |
| `TIMEZONE` | 時區（預設 `Asia/Taipei`） |

產生 ENCRYPTION_KEY：

```bash
python3 -c "import os, base64; print(base64.b64encode(os.urandom(32)).decode())"
```

## 專案結構

```
line-calendar-bot/
├── app/
│   ├── main.py              # FastAPI 入口，/health 端點
│   ├── config.py            # pydantic-settings 設定
│   ├── routes/
│   │   ├── webhook.py       # POST /webhook（LINE 事件接收）
│   │   └── oauth.py         # GET /oauth/callback
│   ├── services/
│   │   ├── nlp.py           # Claude API 意圖解析
│   │   ├── calendar.py      # Google Calendar CRUD
│   │   ├── line_messaging.py# LINE 回覆 / 推播
│   │   └── auth.py          # Google OAuth flow + token refresh
│   ├── models/
│   │   ├── intent.py        # CalendarIntent, EventDetails
│   │   └── user.py          # UserToken, OAuthState, UserState
│   ├── store/
│   │   ├── firestore.py     # Firestore CRUD
│   │   └── encryption.py    # AES-256-GCM 加解密
│   ├── handlers/
│   │   └── message.py       # 訊息處理協調器
│   └── utils/
│       ├── datetime_utils.py# 時區 / 時間格式化
│       └── i18n.py          # 繁體中文訊息模板
├── scripts/
│   ├── setup_gcp.sh         # 一次性 GCP 基礎建設
│   ├── deploy.sh            # 部署到 Cloud Run
│   ├── dev.sh               # 本地開發（uvicorn + ngrok）
│   └── update_secret.sh     # 更新 Secret Manager 密鑰
├── tests/
├── Dockerfile
├── pyproject.toml
└── DEPLOYMENT.md            # 完整部署指南
```

## 使用說明

### 首次使用

1. 傳送任何訊息給 LINE Bot
2. Bot 回傳授權連結 → 點擊，瀏覽器開啟 Google 登入
3. 完成授權後 Bot 確認成功 → 可開始使用

### 支援的指令範例

```
新增行程
「明天早上十點牙醫」
「下週三下午兩點到四點開專案會議，地點在 101 大樓」
「2月14日情人節晚餐」

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

其他
「說明」或「help」  → 顯示功能說明
「解除授權」        → 移除 Google Calendar 授權
```

### 注意事項

- 授權連結必須在**外部瀏覽器**開啟（LINE 內建瀏覽器不支援 Google OAuth）
- 一個 LINE 帳號對應一個 Google 帳號
- Access token 自動更新，無需重新授權

## 開發

### 執行測試

```bash
uv run pytest tests/ -v
```

共 30 個測試，涵蓋：

| 測試檔案 | 涵蓋範圍 |
|---------|---------|
| `tests/test_encryption.py` | AES-256-GCM 加解密、nonce 隨機性、錯誤金鑰拒絕 |
| `tests/test_datetime_utils.py` | RFC3339 格式、時區轉換、事件時間格式化、星期名稱 |
| `tests/test_models.py` | CalendarIntent 驗證、ActionType、confidence 邊界 |
| `tests/test_api.py` | /health、webhook 簽名驗證、/oauth/callback 路由 |

### 更新密鑰

```bash
bash scripts/update_secret.sh
```

## 已部署服務

| 項目 | 值 |
|------|-----|
| GCP 專案 | `amateur-intelligence-service` |
| 服務 URL | `https://line-calendar-bot-132888979367.asia-east1.run.app` |
| Webhook URL | `https://line-calendar-bot-132888979367.asia-east1.run.app/webhook` |
| OAuth Callback | `https://line-calendar-bot-132888979367.asia-east1.run.app/oauth/callback` |
| 區域 | `asia-east1` |
| 最新 Revision | `line-calendar-bot-00005-d7h` |

## License

MIT
