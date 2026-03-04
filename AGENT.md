# AGENT.md — 多代理協作指引

本文件為 AI Agent 在此專案工作時的指引，包含專案地圖、角色分工與協作規範。

---

## 專案快照

| 項目 | 值 |
|------|-----|
| 專案 | LINE Calendar Bot |
| 語言 | Python 3.12 |
| 套件管理 | uv |
| 部署 | Google Cloud Run (`asia-east1`) |
| 服務 URL | `https://line-calendar-bot-132888979367.asia-east1.run.app` |
| GCP 專案 | `amateur-intelligence-service` |
| 最新 Revision | `line-calendar-bot-00007-xgt` |

---

## 關鍵檔案地圖

```
app/
├── config.py              # 所有環境變數（pydantic-settings）
├── main.py                # FastAPI 入口，路由掛載
├── handlers/message.py    # ★ 核心協調器：接收訊息 → NLP → 執行
├── services/
│   ├── nlp.py             # ★ Claude API 意圖解析（唯一 AI 呼叫點）
│   ├── auth.py            # ★ Google OAuth + PKCE + token refresh
│   ├── calendar.py        # Google Calendar CRUD
│   └── line_messaging.py  # LINE reply / push
├── models/
│   ├── intent.py          # CalendarIntent（action, event_details, time_range…）
│   └── user.py            # UserToken, OAuthState（含 code_verifier）, UserState
├── store/
│   ├── firestore.py       # Firestore CRUD（users / oauth_states / user_states）
│   └── encryption.py      # AES-256-GCM 加解密
└── utils/
    ├── datetime_utils.py  # 時區、格式化（Asia/Taipei）
    └── i18n.py            # 繁體中文訊息模板
scripts/
├── deploy.sh              # 建置 + 推送 + 部署到 Cloud Run
├── dev.sh                 # 本地開發（uvicorn + ngrok）
├── setup_gcp.sh           # 一次性 GCP 基礎建設
└── update_secret.sh       # 更新 Secret Manager 密鑰
tests/                     # pytest，30 個測試，asyncio_mode=auto
```

---

## 已知限制與陷阱

| 問題 | 說明 |
|------|------|
| PKCE 必要 | Google 強制要求 PKCE（`code_verifier` / `code_challenge`），`auth.py` 已實作 |
| redirect_uri 格式 | `deploy.sh` 偵測 URL 可能回傳 `suzmi2nvla-de.a.run.app` 格式，需手動確認與穩定 URL（`132888979367.asia-east1.run.app`）一致 |
| Python 3.14 警告 | `line-bot-sdk` 使用 Pydantic V1，與 Python 3.14 不相容；Docker image 使用 3.12 無此問題 |
| OAuth Testing 狀態 | GCP OAuth consent screen 處於 Testing，只有 test users 可授權 |
| Firestore TTL | `oauth_states` 10 分鐘、`user_states` 5 分鐘自動清除，需在 GCP 設定 TTL policy |

---

## 代理角色空間

以下為保留的代理角色定義，可依需求啟用。

### 🔧 Developer Agent
**職責**：實作新功能、修復 bug
**工作範圍**：`app/`、`tests/`
**進入條件**：先閱讀 `handlers/message.py` 與 `services/nlp.py` 了解資料流
**完成條件**：`uv run pytest tests/ -q` 全部通過，且修改最小化

### 🧪 Test Agent
**職責**：為新功能補充測試、驗證回歸
**工作範圍**：`tests/`
**規範**：
- 測試不得連線外部服務（Firestore / Claude / LINE）
- 外部依賴一律 mock（`unittest.mock.AsyncMock`）
- 使用 `os.environ.setdefault` 設定測試環境變數

### 🚀 Deploy Agent
**職責**：部署新版本到 Cloud Run
**指令**：
```bash
source ~/Project/.env
GCP_PROJECT_ID=amateur-intelligence-service bash scripts/deploy.sh
```
**部署後確認**：
1. Health check HTTP 200
2. Cloud Run 環境變數 `GOOGLE_REDIRECT_URI` 指向穩定 URL（`132888979367.asia-east1.run.app`）

### 📋 Review Agent
**職責**：審查程式碼品質、安全性
**重點關注**：
- `store/encryption.py`：token 加解密正確性
- `services/auth.py`：PKCE 完整性、state CSRF 防護
- `routes/webhook.py`：LINE signature 驗證

### 📝 Docs Agent
**職責**：維護 `README.md`、`DEPLOYMENT.md`、`AGENT.md`
**每次部署後需更新**：
- `README.md` 與 `DEPLOYMENT.md` 的「最新 Revision」
- `DEPLOYMENT.md` 的疑難排解（新增問題與解法）

---

## 協作規範

### 修改流程

```
1. 閱讀相關原始碼（勿假設）
2. 最小化修改範圍
3. 執行 uv run pytest tests/ -q → 全部通過
4. git commit（Conventional Commits 格式）
5. 部署：bash scripts/deploy.sh
6. 更新文件
```

### Commit 格式

```
<type>: <description>

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
```

| type | 用途 |
|------|------|
| `feat` | 新功能 |
| `fix` | Bug 修復 |
| `test` | 測試新增/修改 |
| `docs` | 文件更新 |
| `refactor` | 重構（不改行為） |
| `chore` | 維護性工作 |

### 環境變數取得

```bash
# 從 ~/Project/.env 取得 ANTHROPIC_API_KEY、GOOGLE_CLIENT_ID/SECRET
source ~/Project/.env

# LINE 金鑰（固定值）
LINE_CHANNEL_SECRET=6888743ac3a18aa116b33872b6e60a1d
LINE_CHANNEL_ACCESS_TOKEN=UUmKJxC2uix/...（見 Secret Manager）
```

### Firestore 結構

```
users/{line_user_id}
  encrypted_access_token, encrypted_refresh_token, token_expiry, scopes

oauth_states/{state_token}          ← TTL 10 分鐘
  line_user_id, expires_at, code_verifier

user_states/{line_user_id}          ← TTL 5 分鐘
  action, candidates, original_intent, expires_at
```

---

## 擴充方向（待辦）

- [ ] 支援 recurring events（每週定期行程）
- [ ] 多 Google 帳號切換
- [ ] 提醒功能（透過 LINE push message）
- [ ] 群組 bot 支援
- [ ] 使用量統計（每日 Claude API 呼叫次數）
