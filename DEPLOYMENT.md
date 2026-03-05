# Cat-Lendar — 部署與設定指南

## 目錄

1. [系統需求](#系統需求)
2. [架構概覽](#架構概覽)
3. [GCP 基礎設施設定（首次）](#gcp-基礎設施設定首次)
4. [LINE Bot 設定](#line-bot-設定)
5. [App Owner 授權設定](#app-owner-授權設定)
6. [部署到 Cloud Run](#部署到-cloud-run)
7. [本地開發](#本地開發)
8. [日常維運](#日常維運)
9. [Firestore 資料結構](#firestore-資料結構)
10. [常見問題](#常見問題)

---

## 系統需求

| 工具 | 版本 | 安裝方式 |
|------|------|---------|
| Python | ≥ 3.12 | `brew install python@3.12` |
| uv | 任意 | `brew install uv` |
| Docker | 任意 | [Docker Desktop](https://www.docker.com/products/docker-desktop/) |
| gcloud CLI | 任意 | `brew install google-cloud-sdk` |
| ngrok | 任意（本地開發用） | `brew install ngrok` |

```bash
# 確認版本
python3 --version   # Python 3.12.x
uv --version        # uv x.x.x
docker --version    # Docker x.x.x
gcloud --version    # Google Cloud SDK x.x.x
```

---

## 架構概覽

```
LINE User
   │  傳送訊息
   ▼
Cloud Run (FastAPI)
   └── POST /webhook       ← LINE Messaging API
         │
         ├── Claude API             (自然語言解析)
         ├── Google Calendar API    (共享行事曆 CRUD)
         └── Cloud Firestore        (對話狀態、提醒)
                │
                └── Secret Manager (加密金鑰、API 金鑰)
```

**共享行事曆模式**：所有 LINE 用戶共用 app owner 的單一 Google Calendar。行程的 description 欄位自動附加 `[LINE: {user_id}]` 標記操作者。App owner 預先完成一次 OAuth 授權，取得 refresh token 存入 Secret Manager。

### Firestore Collections

| Collection | 用途 | TTL |
|-----------|------|-----|
| `user_states/{line_user_id}` | 多筆匹配選擇暫存狀態（5 分鐘） | `expires_at` |
| `conversation_history/{line_user_id}` | 對話記憶（30 分鐘） | `updated_at` |
| `reminders/{reminder_id}` | 行程提醒佇列 | 無（sent 後保留） |
| `user_prefs/{line_user_id}` | 預設提醒設定 | 無 |

---

## GCP 基礎設施設定（首次）

> 整個專案只需執行一次。

### 步驟 1：登入 gcloud

```bash
gcloud auth login
gcloud config set project <YOUR_PROJECT_ID>
```

### 步驟 2：執行 setup_gcp.sh

腳本會自動完成以下工作：

- 啟用必要的 GCP APIs（Cloud Run、Firestore、Secret Manager 等）
- 建立 Artifact Registry repository（`asia-east1/line-bot`）
- 建立 Firestore 資料庫（asia-east1，Native mode）
- 設定 Firestore TTL policy
- 建立 Service Account（最小權限）
- 互動式輸入各 API 金鑰，存入 Secret Manager
- 自動產生 AES-256 加密金鑰（`ENCRYPTION_KEY`）

```bash
export GCP_PROJECT_ID=your-project-id   # 或從 gcloud config 讀取
./scripts/setup_gcp.sh
```

腳本執行過程中會依序提示輸入：

| 提示 | 填入來源 |
|------|---------|
| `LINE_CHANNEL_SECRET` | LINE Developers Console > Channel Secret |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Developers Console > Channel access token |
| `ANTHROPIC_API_KEY` | [Anthropic Console](https://console.anthropic.com/) |
| `GOOGLE_CLIENT_ID` | GCP Console > OAuth 2.0 Credentials |
| `GOOGLE_CLIENT_SECRET` | GCP Console > OAuth 2.0 Credentials |
| `ENCRYPTION_KEY` | **自動產生，無需輸入** |

> **注意**：`GOOGLE_REFRESH_TOKEN` 需在 [App Owner 授權設定](#app-owner-授權設定) 步驟中另行建立。

---

## LINE Bot 設定

### 步驟 1：建立 LINE Channel

1. 前往 [LINE Developers Console](https://developers.line.biz/)
2. 登入後點擊 **Create a new provider**（或選擇現有 Provider）
3. 點擊 **Create a new channel** → 選擇 **Messaging API**
4. 填寫必要資訊：
   - **Channel name**：例如「日曆助手」
   - **Channel description**：（任意）
   - **Category / Subcategory**：（任意）
5. 同意條款，建立 Channel

### 步驟 2：取得金鑰

| 項目 | 位置 | 對應設定 |
|------|------|---------|
| Channel Secret | Basic settings tab | `LINE_CHANNEL_SECRET` |
| Channel access token | Messaging API tab > 底部 > Issue | `LINE_CHANNEL_ACCESS_TOKEN` |

> Channel access token 選擇 **long-lived**（長期有效），點擊 **Issue** 產生。

### 步驟 3：設定 Webhook（部署後執行）

部署完成後，於 **Messaging API** tab 填入：

| 設定項目 | 值 |
|---------|---|
| **Webhook URL** | `https://<cloud-run-url>/webhook` |
| **Use webhook** | ✅ ON |
| **Auto-reply messages** | ❌ OFF |
| **Greeting messages** | ❌ OFF（可視需求保留） |

填入後點擊 **Verify** 確認連線正常（應顯示 Success）。

---

## App Owner 授權設定

> App owner 需完成一次性授權，取得 Google Calendar refresh token，供所有使用者共用。

### 步驟 1：設定 OAuth Consent Screen

1. GCP Console → **APIs & Services** → **OAuth consent screen**
2. User type 選擇 **External**，點擊 **Create**
3. 填寫應用程式資訊：App name、User support email、Developer contact email
4. **Scopes** → 加入 `https://www.googleapis.com/auth/calendar`
5. **Test users** → 加入 app owner 的 Google 帳號
6. 儲存

### 步驟 2：建立 OAuth 2.0 Credentials

1. GCP Console → **APIs & Services** → **Credentials**
2. 點擊 **Create Credentials** → **OAuth client ID**
3. Application type 選擇 **Desktop app**（或 Web app）
4. 取得 `GOOGLE_CLIENT_ID` 與 `GOOGLE_CLIENT_SECRET`

### 步驟 3：執行授權腳本取得 refresh token

```bash
# 從 Secret Manager 取得 credentials，執行授權腳本
GOOGLE_CLIENT_ID=$(gcloud secrets versions access latest --secret=GOOGLE_CLIENT_ID) \
GOOGLE_CLIENT_SECRET=$(gcloud secrets versions access latest --secret=GOOGLE_CLIENT_SECRET) \
uv run python scripts/get_token.py
```

腳本會開啟瀏覽器，完成 Google 授權後輸出 refresh token：

```
============================================================
授權成功！請將以下 refresh token 設定到環境變數：
============================================================

GOOGLE_REFRESH_TOKEN=1//0gXXXXXXXXXXXXXXXXXXXXXXX
```

### 步驟 4：將 refresh token 存入 Secret Manager

```bash
echo -n "YOUR_REFRESH_TOKEN" | gcloud secrets create GOOGLE_REFRESH_TOKEN \
  --data-file=- \
  --project=amateur-intelligence-service

# 授予 Service Account 存取權
gcloud secrets add-iam-policy-binding GOOGLE_REFRESH_TOKEN \
  --member="serviceAccount:line-calendar-bot-sa@amateur-intelligence-service.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

> 若需更新 refresh token（例如過期），使用 `update_secret.sh`：
> ```bash
> ./scripts/update_secret.sh GOOGLE_REFRESH_TOKEN
> ```

---

## 部署到 Cloud Run

### 首次部署

```bash
./scripts/deploy.sh
```

腳本執行流程：

1. 確認 `GOOGLE_REFRESH_TOKEN` secret 已存在
2. 以 `git rev-parse --short HEAD` 作為 image tag
3. `docker build --platform linux/amd64` 建置 image
4. Push 到 Artifact Registry
5. `gcloud run deploy` 部署，從 Secret Manager 掛載所有金鑰
6. 執行 `/health` 健康檢查
7. 輸出 Webhook URL

輸出範例：

```
════════════════════════════════════════════════════════
  部署完成！
════════════════════════════════════════════════════════

  Cloud Run URL:    https://line-calendar-bot-xxxx-de.a.run.app

  LINE Bot Webhook URL（填入 LINE Developers Console）：
  https://line-calendar-bot-xxxx-de.a.run.app/webhook

  Image: asia-east1-docker.pkg.dev/.../line-calendar-bot:abc1234
```

### Cloud Run 設定說明

| 參數 | 值 | 說明 |
|------|---|------|
| `--min-instances` | 0 | 閒置時縮至 0，節省費用 |
| `--max-instances` | 10 | 最多 10 個 instance |
| `--concurrency` | 80 | 每個 instance 最多 80 個並發請求 |
| `--cpu` | 1 | 1 vCPU |
| `--memory` | 512Mi | 512 MB RAM |
| `--timeout` | 30 | 請求超時 30 秒 |
| `--allow-unauthenticated` | — | LINE webhook 需要公開存取 |

---

## 本地開發

### 步驟 1：設定 .env

```bash
cp .env.example .env
# 編輯 .env，填入所有金鑰（包含 GOOGLE_REFRESH_TOKEN）
```

### 步驟 2：安裝相依

```bash
uv sync
```

### 步驟 3：啟動開發伺服器

```bash
./scripts/dev.sh
```

腳本會：
1. 啟動 uvicorn（port 8080，hot reload）
2. 啟動 ngrok tunnel
3. 印出 Webhook URL

---

## 日常維運

### 更新程式碼

```bash
git add . && git commit -m "..."
./scripts/deploy.sh
```

### 更新 Secret

```bash
# 互動式更新（會提示輸入新值）
./scripts/update_secret.sh LINE_CHANNEL_ACCESS_TOKEN

# 直接指定新值
./scripts/update_secret.sh ANTHROPIC_API_KEY sk-ant-xxxx

# 更新後重新部署才會生效
./scripts/deploy.sh
```

### 更新 Refresh Token（token 過期時）

```bash
# 重新授權取得新 token
GOOGLE_CLIENT_ID=$(gcloud secrets versions access latest --secret=GOOGLE_CLIENT_ID) \
GOOGLE_CLIENT_SECRET=$(gcloud secrets versions access latest --secret=GOOGLE_CLIENT_SECRET) \
uv run python scripts/get_token.py

# 更新 Secret Manager
./scripts/update_secret.sh GOOGLE_REFRESH_TOKEN <new_token>
./scripts/deploy.sh
```

### 查看 Cloud Run 日誌

```bash
gcloud run services logs read line-calendar-bot \
  --region=asia-east1 \
  --limit=100
```

### 查看即時日誌

```bash
gcloud run services logs tail line-calendar-bot \
  --region=asia-east1
```

---

## Firestore 資料結構

```
user_states/
  {line_user_id}/
    action:          string   # "select_event_for_update" | "select_event_for_delete"
    candidates:      array    # 候選行程列表
    original_intent: map      # 原始意圖 JSON
    expires_at:      timestamp  ← TTL field（5 分鐘自動刪除）

conversation_history/
  {line_user_id}/
    messages:    array     # [{role, content, timestamp}]
    updated_at:  timestamp ← 超過 30 分鐘自動忽略

reminders/
  {reminder_id}/
    line_user_id:      string
    event_id:          string
    event_summary:     string
    start_time:        timestamp
    reminder_at:       timestamp
    reminder_minutes:  integer
    sent:              boolean
    created_at:        timestamp

user_prefs/
  {line_user_id}/
    default_reminder_minutes: integer | null
    updated_at:               timestamp
```

---

## 常見問題

### webhook 收不到訊息

1. 確認 LINE Developers Console > Webhook URL 已填入且 **Verify** 成功
2. 確認 **Use webhook** 為 ON
3. 確認 Cloud Run 服務正常：`curl https://<url>/health`

### Google Calendar 操作失敗

1. 確認 `GOOGLE_REFRESH_TOKEN` secret 存在且有效
2. 確認 Service Account 有存取 `GOOGLE_REFRESH_TOKEN` secret 的權限
3. 若 token 過期，依[更新 Refresh Token](#更新-refresh-tokentoken-過期時)步驟重新授權

### 部署後 Secret 讀不到

確認 Service Account 已被授予 Secret 的存取權：

```bash
gcloud secrets get-iam-policy <SECRET_NAME>
# 確認包含: serviceAccount:line-calendar-bot-sa@<PROJECT>.iam.gserviceaccount.com
```

若缺少，手動補授：

```bash
gcloud secrets add-iam-policy-binding <SECRET_NAME> \
  --member="serviceAccount:line-calendar-bot-sa@<PROJECT>.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

---

## 已部署環境

| 項目 | 值 |
|------|-----|
| GCP 專案 | `amateur-intelligence-service` |
| 服務 URL | `https://line-calendar-bot-132888979367.asia-east1.run.app` |
| Webhook URL | `https://line-calendar-bot-132888979367.asia-east1.run.app/webhook` |
| 區域 | `asia-east1` |
| Service Account | `line-calendar-bot-sa@amateur-intelligence-service.iam.gserviceaccount.com` |
| Artifact Registry | `asia-east1-docker.pkg.dev/amateur-intelligence-service/line-bot/line-calendar-bot` |
