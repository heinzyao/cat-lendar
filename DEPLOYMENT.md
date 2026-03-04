# LINE Calendar Bot — 部署與設定指南

## 目錄

1. [系統需求](#系統需求)
2. [架構概覽](#架構概覽)
3. [GCP 基礎設施設定（首次）](#gcp-基礎設施設定首次)
4. [LINE Bot 設定](#line-bot-設定)
5. [Google OAuth 設定](#google-oauth-設定)
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
   ├── POST /webhook       ← LINE Messaging API
   └── GET  /oauth/callback ← Google OAuth 2.0
         │
         ├── Claude API    (自然語言解析)
         ├── Google Calendar API  (行程 CRUD)
         └── Cloud Firestore      (token 儲存、對話狀態)
                │
                └── Secret Manager (加密金鑰、API 金鑰)
```

### Firestore Collections

| Collection | 用途 | TTL |
|-----------|------|-----|
| `users/{line_user_id}` | 加密的 Google OAuth token | 無 |
| `oauth_states/{state}` | CSRF state（10 分鐘） | `expires_at` |
| `user_states/{line_user_id}` | 模糊匹配暫存狀態（5 分鐘） | `expires_at` |

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

> **注意**：`GOOGLE_REDIRECT_URI` 會在 `deploy.sh` 首次執行後自動填入，不需手動設定。

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

### 步驟 4：LINE Bot 功能建議設定

在 **Messaging API** tab：

| 功能 | 建議設定 | 原因 |
|------|---------|------|
| Allow bot to join group chats | OFF | 目前僅支援 1 對 1 |
| Allow users to open the profile page | ON | 使用者可查看 Bot 資訊 |

---

## Google OAuth 設定

> 必須在部署之前完成 OAuth consent screen 設定，否則使用者無法授權。

### 步驟 1：設定 OAuth Consent Screen

1. GCP Console → **APIs & Services** → **OAuth consent screen**
2. User type 選擇 **External**，點擊 **Create**
3. 填寫應用程式資訊：

   | 欄位 | 填入值 |
   |------|-------|
   | App name | 日曆助手（或任意名稱） |
   | User support email | 你的 email |
   | Developer contact email | 你的 email |

4. **Scopes** 頁面 → 點擊 **Add or Remove Scopes**：
   - 搜尋 `calendar`
   - 勾選 `https://www.googleapis.com/auth/calendar`
   - 點擊 **Update**

5. **Test users** 頁面（測試期間）：
   - 點擊 **Add Users**
   - 加入所有需要測試的 Google 帳號
   - 正式上線前此步驟為必要，否則其他使用者無法授權

6. 儲存並返回

> **注意**：應用程式正式上線前，需回到 OAuth consent screen 點擊 **Publish App** 並提交 Google 審核。個人使用或內部測試可維持 Testing 狀態，但限 100 個 test users。

### 步驟 2：建立 OAuth 2.0 Credentials

1. GCP Console → **APIs & Services** → **Credentials**
2. 點擊 **Create Credentials** → **OAuth client ID**
3. Application type 選擇 **Web application**
4. Name：例如「LINE Calendar Bot」
5. **Authorized redirect URIs**：
   - 本地開發：加入 ngrok 提供的 URL，例如 `https://xxxx.ngrok-free.app/oauth/callback`
   - 正式部署：加入 Cloud Run URL，例如 `https://line-calendar-bot-xxxx-de.a.run.app/oauth/callback`
   - ⚠️ 兩個 URI 都可以加入，方便開發
6. 點擊 **Create**，取得：
   - **Client ID** → `GOOGLE_CLIENT_ID`
   - **Client Secret** → `GOOGLE_CLIENT_SECRET`

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
5. 自動偵測並填入 `GOOGLE_REDIRECT_URI`
6. 執行 `/health` 健康檢查
7. 輸出 Webhook URL 與 OAuth Redirect URI

輸出範例：

```
════════════════════════════════════════════════════════
  部署完成！
════════════════════════════════════════════════════════

  Cloud Run URL:    https://line-calendar-bot-xxxx-de.a.run.app

  LINE Bot Webhook URL（填入 LINE Developers Console）：
  https://line-calendar-bot-xxxx-de.a.run.app/webhook

  Google OAuth Redirect URI（填入 GCP OAuth 2.0 Credentials）：
  https://line-calendar-bot-xxxx-de.a.run.app/oauth/callback
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
# 編輯 .env，填入所有金鑰
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
3. 印出 Webhook URL 與 OAuth Redirect URI

```
════════════════════════════════════════════════════════
  本地開發環境已就緒
════════════════════════════════════════════════════════

  LINE Bot Webhook URL：
  https://xxxx.ngrok-free.app/webhook

  Google OAuth Redirect URI：
  https://xxxx.ngrok-free.app/oauth/callback

  ngrok 管理介面: http://localhost:4040
  按 Ctrl+C 停止所有服務
```

> ngrok 免費方案每次啟動 URL 都會改變，需重新填入 LINE Developers Console。若要固定 URL，可使用 ngrok 付費方案或 [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)。

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
# 互動式更新（會提示輸入新值）
./scripts/update_secret.sh LINE_CHANNEL_ACCESS_TOKEN

# 直接指定新值
./scripts/update_secret.sh ANTHROPIC_API_KEY sk-ant-xxxx

# 更新後重新部署才會生效
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

### 撤銷某位使用者的授權（Firestore）

```bash
# 刪除 Firestore 中的 user token
gcloud firestore documents delete \
  "projects/<PROJECT_ID>/databases/(default)/documents/users/<LINE_USER_ID>"
```

---

## Firestore 資料結構

```
users/
  {line_user_id}/
    encrypted_access_token:  string   # AES-256-GCM 加密
    encrypted_refresh_token: string   # AES-256-GCM 加密
    token_expiry:            timestamp
    scopes:                  string[]
    created_at:              timestamp
    updated_at:              timestamp

oauth_states/
  {state_token}/
    line_user_id:  string
    expires_at:    timestamp  ← TTL field（10 分鐘自動刪除）

user_states/
  {line_user_id}/
    action:          string   # "select_event_for_update" | "select_event_for_delete"
    candidates:      array    # 候選行程列表
    original_intent: map      # 原始意圖 JSON
    expires_at:      timestamp  ← TTL field（5 分鐘自動刪除）
```

---

## 常見問題

### webhook 收不到訊息

1. 確認 LINE Developers Console > Webhook URL 已填入且 **Verify** 成功
2. 確認 **Use webhook** 為 ON
3. 確認 Cloud Run 服務正常：`curl https://<url>/health`

### Google 授權頁面在 LINE 內建瀏覽器無法完成

這是 Google 的安全限制，系統已透過在授權 URL 加入 `openExternalBrowser=1` 參數強制開啟外部瀏覽器。若仍有問題，請確認 LINE App 版本已更新。

### token refresh 失敗 / 需要重新授權

使用者傳送「解除授權」後重新點選授權按鈕即可。
若批次發生（如 refresh token 過期），可至 Firestore console 刪除對應的 `users/{line_user_id}` 文件。

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

### OAuth consent screen 審核

測試期間（Testing 狀態）只有加入 **Test users** 的 Google 帳號可以授權。
若要開放所有使用者，需在 OAuth consent screen 點擊 **Publish App** 並提交 Google 審核，審核重點：
- 應用程式確實需要 Calendar 存取權限
- 隱私政策頁面（需自行架設）
- 首頁 URL

### 授權失敗：invalid_grant: Missing code verifier

**現象**：OAuth callback 顯示「授權失敗，請稍後再試」，Cloud Run 日誌出現：
```
oauthlib.oauth2.rfc6749.errors.InvalidGrantError: (invalid_grant) Missing code verifier.
```

**原因**：Google 自 2025 年起對 Web Application 類型的 OAuth client 強制要求 PKCE（Proof Key for Code Exchange）。v1.0 程式碼未實作 PKCE。

**已修復（v1.1）**：已在 `create_auth_url` 生成 `code_verifier` / `code_challenge`，存入 Firestore，並在 `fetch_token` 時帶入 `code_verifier`。升級至最新部署版本即可。

---

### Error 400: redirect_uri_mismatch

**現象**：點選 LINE 授權連結後，Google 顯示 `redirect_uri_mismatch` 錯誤。

**原因**：`deploy.sh` 使用 `gcloud run services describe --format=value(status.url)` 自動偵測 redirect URI，但有時回傳的 URL 格式（如 `xxxx-de.a.run.app`）與 GCP OAuth 2.0 Credentials 中登記的穩定 URL（`132888979367.asia-east1.run.app`）不符。

**修復**：手動更新 Cloud Run 環境變數與 GCP Credentials 一致：

```bash
# 確認穩定 URL（從 gcloud run deploy 輸出中取得）
STABLE_URL="https://line-calendar-bot-132888979367.asia-east1.run.app"

# 更新 Cloud Run 環境變數
gcloud run services update line-calendar-bot \
  --region=asia-east1 \
  --update-env-vars="GOOGLE_REDIRECT_URI=${STABLE_URL}/oauth/callback"
```

同時確認 **GCP Console → APIs & Services → Credentials → OAuth 2.0 Client ID** 的 Authorized redirect URIs 包含：

```
https://line-calendar-bot-132888979367.asia-east1.run.app/oauth/callback
```

---

## 已部署環境

| 項目 | 值 |
|------|-----|
| GCP 專案 | `amateur-intelligence-service` |
| 服務 URL | `https://line-calendar-bot-132888979367.asia-east1.run.app` |
| Webhook URL | `https://line-calendar-bot-132888979367.asia-east1.run.app/webhook` |
| OAuth Callback | `https://line-calendar-bot-132888979367.asia-east1.run.app/oauth/callback` |
| 區域 | `asia-east1` |
| Service Account | `line-calendar-bot-sa@amateur-intelligence-service.iam.gserviceaccount.com` |
| Artifact Registry | `asia-east1-docker.pkg.dev/amateur-intelligence-service/line-bot/line-calendar-bot` |
| 最新 Revision | `line-calendar-bot-00006-tb6` |
