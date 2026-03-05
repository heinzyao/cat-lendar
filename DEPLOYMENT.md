# Cat-Lendar — 部署與設定指南

## 目錄

1. [系統需求](#系統需求)
2. [架構概覽](#架構概覽)
3. [GCP 基礎設施設定（首次）](#gcp-基礎設施設定首次)
4. [LINE Bot 設定](#line-bot-設定)
5. [Google OAuth 設定（App Owner 授權）](#google-oauth-設定app-owner-授權)
6. [部署到 Cloud Run](#部署到-cloud-run)
7. [本地開發](#本地開發)
8. [日常維運](#日常維運)
9. [Firestore 資料結構](#firestore-資料結構)
10. [常見問題](#常見問題)

---

## 系統需求

| 工具 | 最低版本 |
|------|---------|
| gcloud CLI | 任意新版 |
| Docker | 任意新版 |
| Python | 3.12+ |
| uv | 任意新版 |

---

## 架構概覽

**共享行事曆模式**：所有 LINE 用戶共用 app owner 的單一 Google Calendar。App owner 預先完成一次 OAuth 授權並取得 refresh token，所有操作皆透過此憑證存取 Google Calendar API。

```
LINE User（任何人）
   │  傳送訊息
   ▼
Cloud Run (FastAPI)
   └── POST /webhook       ← LINE Messaging API
         │
         ├── Claude API              (自然語言解析)
         ├── Google Calendar API     (共享行程 CRUD)
         └── Cloud Firestore         (對話狀態、提醒)
                │
                └── Secret Manager  (API 金鑰、refresh token)
```

### Firestore Collections

| Collection | 用途 | TTL |
|-----------|------|-----|
| `user_states/{line_user_id}` | 多筆選擇暫存狀態（5 分鐘） | `expires_at` |
| `conversation_history/{line_user_id}` | 對話記憶（30 分鐘） | `updated_at` |
| `reminders/{reminder_id}` | 到期提醒佇列 | 無 |
| `user_prefs/{line_user_id}` | 預設提醒設定 | 無 |

---

## GCP 基礎設施設定（首次）

> 整個專案只需執行一次。

### 步驟 1：登入 gcloud

```bash
gcloud auth login
gcloud config set project amateur-intelligence-service
```

### 步驟 2：啟用必要 API

```bash
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  firestore.googleapis.com \
  secretmanager.googleapis.com \
  calendar-json.googleapis.com
```

### 步驟 3：建立 Artifact Registry Repository

```bash
gcloud artifacts repositories create line-bot \
  --repository-format=docker \
  --location=asia-east1
```

### 步驟 4：建立 Service Account

```bash
gcloud iam service-accounts create line-calendar-bot-sa \
  --display-name="LINE Calendar Bot SA"
```

### 步驟 5：授予 IAM 權限

```bash
PROJECT_ID="amateur-intelligence-service"
SA_EMAIL="line-calendar-bot-sa@${PROJECT_ID}.iam.gserviceaccount.com"

# Firestore
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/datastore.user"

# Secret Manager（讀取）
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/secretmanager.secretAccessor"

# Cloud Run 日誌
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/logging.logWriter"
```

### 步驟 6：建立 Secret Manager Secrets

```bash
# 互動式建立各 secret
for SECRET in \
  LINE_CHANNEL_SECRET \
  LINE_CHANNEL_ACCESS_TOKEN \
  ANTHROPIC_API_KEY \
  GOOGLE_CLIENT_ID \
  GOOGLE_CLIENT_SECRET \
  GOOGLE_REFRESH_TOKEN \
  ENCRYPTION_KEY; do
  gcloud secrets create "$SECRET" --replication-policy=automatic
done

# 填入各 secret 的值
echo -n "your_value" | gcloud secrets versions add SECRET_NAME --data-file=-
```

> **ENCRYPTION_KEY** 產生方式：
> ```bash
> python3 -c "import os, base64; print(base64.b64encode(os.urandom(32)).decode())"
> ```

> **GOOGLE_REFRESH_TOKEN** 取得方式：見下方「Google OAuth 設定」。

### 步驟 7：建立 Firestore Database

```bash
gcloud firestore databases create --location=asia-east1
```

---

## LINE Bot 設定

### 步驟 1：建立 LINE Channel

1. 前往 [LINE Developers Console](https://developers.line.biz/)
2. 建立 Provider（如尚未建立）
3. 建立 Messaging API Channel
4. 取得：
   - **Channel Secret** → `LINE_CHANNEL_SECRET`
   - **Channel access token** → `LINE_CHANNEL_ACCESS_TOKEN`

### 步驟 2：設定 Webhook

部署完成後，將 Webhook URL 填入 LINE Developers Console：

| 設定 | 值 |
|------|----|
| **Webhook URL** | `https://line-calendar-bot-132888979367.asia-east1.run.app/webhook` |
| **Use webhook** | ✅ ON |
| **Auto-reply messages** | ❌ OFF |
| **Greeting messages** | ❌ OFF（可視需求保留） |

填入後點擊 **Verify** 確認連線正常（應顯示 Success）。

### 步驟 3：LINE Bot 功能建議設定

在 **Messaging API** tab：

| 功能 | 建議設定 | 原因 |
|------|---------|------|
| Allow bot to join group chats | OFF | 目前僅支援 1 對 1 |
| Allow users to open the profile page | ON | 使用者可查看 Bot 資訊 |

---

## Google OAuth 設定（App Owner 授權）

App owner 只需執行一次，取得 refresh token 後存入 Secret Manager，之後所有用戶的操作皆透過此憑證存取共享行事曆。

### 步驟 1：設定 OAuth Consent Screen

1. GCP Console → **APIs & Services** → **OAuth consent screen**
2. User type 選擇 **External**，點擊 **Create**
3. 填寫應用程式資訊

   | 欄位 | 填入值 |
   |------|-------|
   | App name | Cat-Lendar（或任意名稱） |
   | User support email | 你的 email |
   | Developer contact email | 你的 email |

4. **Scopes** 頁面 → 點擊 **Add or Remove Scopes**：
   - 勾選 `https://www.googleapis.com/auth/calendar`

### 步驟 2：建立 OAuth 2.0 Credentials（Desktop 類型）

1. GCP Console → **APIs & Services** → **Credentials**
2. 點擊 **Create Credentials** → **OAuth client ID**
3. Application type 選擇 **Desktop app**
4. 取得 **Client ID** 與 **Client Secret**，存入 Secret Manager

> 注意：Desktop 類型不需要設定 Redirect URI，適合一次性 CLI 授權。

### 步驟 3：執行 Setup Script 取得 Refresh Token

```bash
# 設定環境變數
export GOOGLE_CLIENT_ID=your_client_id
export GOOGLE_CLIENT_SECRET=your_client_secret

# 執行授權腳本（會開啟瀏覽器）
uv run python scripts/get_token.py
```

腳本會在完成後輸出：
```
GOOGLE_REFRESH_TOKEN=1//0xxxxxxxxxxxxxxxx...
```

### 步驟 4：將 Refresh Token 存入 Secret Manager

```bash
echo -n "your_refresh_token" | \
  gcloud secrets versions add GOOGLE_REFRESH_TOKEN --data-file=-
```

---

## 部署到 Cloud Run

### 首次部署

```bash
./scripts/deploy.sh
```

腳本執行流程：

1. 以 `git rev-parse --short HEAD` 作為 image tag
2. `docker build --platform linux/amd64` 建置 image
3. Push 到 Artifact Registry
4. `gcloud run deploy` 部署，從 Secret Manager 掛載所有金鑰
5. 執行 `/health` 健康檢查
6. 輸出 Webhook URL

輸出範例：

```
════════════════════════════════════════════════════════
  部署完成！
════════════════════════════════════════════════════════

  Cloud Run URL:    https://line-calendar-bot-132888979367.asia-east1.run.app

  LINE Bot Webhook URL（填入 LINE Developers Console）：
  https://line-calendar-bot-132888979367.asia-east1.run.app/webhook

  Image: asia-east1-docker.pkg.dev/.../line-calendar-bot:abc1234
```

### Cloud Run 設定說明

| 參數 | 值 | 說明 |
|------|---|------|
| `--min-instances` | 0 | 閒置時縮至 0，節省費用 |
| `--max-instances` | 10 | 最多 10 個 instance |
| `--concurrency` | 80 | 每個 instance 同時處理 80 個請求 |
| `--timeout` | 30 | 最長 30 秒回應時間 |

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

### 步驟 3：啟動開發環境

```bash
./scripts/dev.sh
```

腳本會：
1. 啟動 uvicorn（port 8080，hot reload）
2. 啟動 ngrok tunnel
3. 印出 Webhook URL

```
════════════════════════════════════════════════════════
  本地開發環境已就緒
════════════════════════════════════════════════════════

  LINE Bot Webhook URL：
  https://xxxx.ngrok-free.app/webhook

  ngrok 管理介面: http://localhost:4040
  按 Ctrl+C 停止所有服務
```

### ngrok 首次設定

```bash
# 至 https://dashboard.ngrok.com/ 取得 authtoken
ngrok config add-authtoken <YOUR_AUTHTOKEN>
```

---

## 日常維運

### 更新程式碼

```bash
git add . && git commit -m "..."
./scripts/deploy.sh
```

### 更新 Secret

```bash
# 互動式更新
./scripts/update_secret.sh

# 直接指定新值
./scripts/update_secret.sh ANTHROPIC_API_KEY sk-ant-xxxx

# 更新後重新部署才會生效
./scripts/deploy.sh
```

### 更新 Refresh Token（token 過期時）

```bash
# 重新執行授權腳本
export GOOGLE_CLIENT_ID=your_client_id
export GOOGLE_CLIENT_SECRET=your_client_secret
uv run python scripts/get_token.py

# 將新 token 存入 Secret Manager
echo -n "new_refresh_token" | \
  gcloud secrets versions add GOOGLE_REFRESH_TOKEN --data-file=-

# 重新部署
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
    expires_at:      timestamp  ← TTL（5 分鐘）

conversation_history/
  {line_user_id}/
    messages:   array      # [{role, content, timestamp}]
    updated_at: timestamp  ← TTL（30 分鐘）

reminders/
  {reminder_id}/
    line_user_id:      string
    event_id:          string
    event_summary:     string
    start_time:        timestamp
    reminder_at:       timestamp
    reminder_minutes:  int
    sent:              bool
    created_at:        timestamp

user_prefs/
  {line_user_id}/
    default_reminder_minutes: int | null
    updated_at:               timestamp
```

---

## 常見問題

### webhook 收不到訊息

1. 確認 LINE Developers Console > Webhook URL 已填入且 **Verify** 成功
2. 確認 **Use webhook** 為 ON
3. 確認 Cloud Run 服務正常：`curl https://<url>/health`

### Google Calendar 操作失敗

1. 確認 `GOOGLE_REFRESH_TOKEN` secret 正確無誤
2. 確認 refresh token 尚未過期（Google refresh token 通常不會過期，除非主動撤銷或長時間未使用）
3. 查看 Cloud Run 日誌確認錯誤訊息

### refresh token 過期或失效

重新執行 `scripts/get_token.py` 取得新的 refresh token，更新 Secret Manager 後重新部署。

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
