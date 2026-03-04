#!/usr/bin/env bash
# =============================================================================
# setup_gcp.sh — 一次性 GCP 基礎設施設定
# 執行一次即可；後續更新只需執行 deploy.sh
# =============================================================================
set -euo pipefail

# ── 顏色輸出 ─────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${BLUE}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; exit 1; }
step()    { echo -e "\n${BOLD}▶ $*${RESET}"; }

# ── 設定變數（可用環境變數覆蓋）──────────────────────────────────────────────
PROJECT_ID="${GCP_PROJECT_ID:-}"
REGION="${GCP_REGION:-asia-east1}"
SERVICE_NAME="${SERVICE_NAME:-line-calendar-bot}"

# ── 前置檢查 ──────────────────────────────────────────────────────────────────
step "前置檢查"

command -v gcloud >/dev/null 2>&1 || error "請先安裝 gcloud CLI: https://cloud.google.com/sdk/docs/install"
command -v python3 >/dev/null 2>&1 || error "請先安裝 Python 3"

if [[ -z "$PROJECT_ID" ]]; then
  PROJECT_ID=$(gcloud config get-value project 2>/dev/null || true)
  [[ -z "$PROJECT_ID" ]] && error "請設定 GCP_PROJECT_ID 環境變數或執行 gcloud config set project <PROJECT_ID>"
fi
info "GCP Project: ${BOLD}$PROJECT_ID${RESET}"
info "Region:      ${BOLD}$REGION${RESET}"
info "Service:     ${BOLD}$SERVICE_NAME${RESET}"

gcloud config set project "$PROJECT_ID" --quiet

# ── 啟用 API ──────────────────────────────────────────────────────────────────
step "啟用 GCP APIs"

APIS=(
  "run.googleapis.com"
  "firestore.googleapis.com"
  "secretmanager.googleapis.com"
  "calendar-json.googleapis.com"
  "cloudbuild.googleapis.com"
  "artifactregistry.googleapis.com"
)
for api in "${APIS[@]}"; do
  info "啟用 $api..."
  gcloud services enable "$api" --quiet
done
success "所有 API 已啟用"

# ── Artifact Registry ──────────────────────────────────────────────────────────
step "建立 Artifact Registry Repository"

REPO="line-bot"
if gcloud artifacts repositories describe "$REPO" --location="$REGION" --quiet 2>/dev/null; then
  info "Repository $REPO 已存在，跳過"
else
  gcloud artifacts repositories create "$REPO" \
    --repository-format=docker \
    --location="$REGION" \
    --description="LINE Calendar Bot images" \
    --quiet
  success "Repository $REPO 建立完成"
fi

# ── Firestore ──────────────────────────────────────────────────────────────────
step "建立 Firestore 資料庫"

if gcloud firestore databases describe --database="(default)" --quiet 2>/dev/null; then
  info "Firestore database 已存在，跳過"
else
  gcloud firestore databases create \
    --location="$REGION" \
    --type=firestore-native \
    --quiet
  success "Firestore 資料庫建立完成"
fi

# 設定 TTL policy（oauth_states.expires_at 與 user_states.expires_at）
step "設定 Firestore TTL Policy"

for COLLECTION in oauth_states user_states; do
  info "設定 $COLLECTION.expires_at TTL..."
  gcloud firestore fields ttls update expires_at \
    --collection-group="$COLLECTION" \
    --enable-ttl \
    --async \
    --quiet || warn "$COLLECTION TTL 設定失敗（可能需要等待 API 就緒，可稍後重跑）"
done
success "Firestore TTL 設定完成"

# ── Service Account ──────────────────────────────────────────────────────────
step "建立 Cloud Run Service Account"

SA_NAME="line-calendar-bot-sa"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

if gcloud iam service-accounts describe "$SA_EMAIL" --quiet 2>/dev/null; then
  info "Service Account 已存在，跳過"
else
  gcloud iam service-accounts create "$SA_NAME" \
    --display-name="LINE Calendar Bot Service Account" \
    --quiet
  success "Service Account 建立完成: $SA_EMAIL"
fi

# 最小權限原則 — 只給必要角色
ROLES=(
  "roles/datastore.user"                # Firestore 讀寫
  "roles/secretmanager.secretAccessor"  # 讀取 Secret
)
for role in "${ROLES[@]}"; do
  info "授予 $role..."
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$SA_EMAIL" \
    --role="$role" \
    --condition=None \
    --quiet
done
success "IAM 設定完成"

# ── Secret Manager ──────────────────────────────────────────────────────────
step "建立 Secret Manager 密鑰"

# 產生 AES-256 加密金鑰
GENERATED_KEY=$(python3 -c "import os, base64; print(base64.b64encode(os.urandom(32)).decode())")
info "已產生 ENCRYPTION_KEY（請妥善保存）: ${BOLD}$GENERATED_KEY${RESET}"

# 密鑰名稱清單（對應 .env.example）
declare -A SECRETS=(
  ["LINE_CHANNEL_SECRET"]=""
  ["LINE_CHANNEL_ACCESS_TOKEN"]=""
  ["ANTHROPIC_API_KEY"]=""
  ["GOOGLE_CLIENT_ID"]=""
  ["GOOGLE_CLIENT_SECRET"]=""
  ["ENCRYPTION_KEY"]="$GENERATED_KEY"
)

for SECRET_NAME in "${!SECRETS[@]}"; do
  PREFILLED="${SECRETS[$SECRET_NAME]}"

  if gcloud secrets describe "$SECRET_NAME" --quiet 2>/dev/null; then
    info "Secret $SECRET_NAME 已存在，跳過（如需更新請手動執行 update_secret.sh）"
    continue
  fi

  if [[ -n "$PREFILLED" ]]; then
    VALUE="$PREFILLED"
  else
    echo -n "  請輸入 ${BOLD}$SECRET_NAME${RESET} 的值: "
    read -r VALUE
    [[ -z "$VALUE" ]] && warn "$SECRET_NAME 跳過（可稍後用 gcloud secrets create 補建）" && continue
  fi

  echo -n "$VALUE" | gcloud secrets create "$SECRET_NAME" \
    --data-file=- \
    --replication-policy=automatic \
    --quiet
  success "Secret $SECRET_NAME 建立完成"
done

# 授予 Service Account 存取所有密鑰
for SECRET_NAME in "${!SECRETS[@]}"; do
  gcloud secrets add-iam-policy-binding "$SECRET_NAME" \
    --member="serviceAccount:$SA_EMAIL" \
    --role="roles/secretmanager.secretAccessor" \
    --quiet 2>/dev/null || true
done
success "Secret 存取權限設定完成"

# ── 完成提示 ──────────────────────────────────────────────────────────────────
echo
echo -e "${GREEN}${BOLD}════════════════════════════════════════════${RESET}"
echo -e "${GREEN}${BOLD}  GCP 基礎設施設定完成！${RESET}"
echo -e "${GREEN}${BOLD}════════════════════════════════════════════${RESET}"
echo
echo -e "  Service Account: ${BOLD}$SA_EMAIL${RESET}"
echo -e "  Artifact Registry: ${BOLD}$REGION-docker.pkg.dev/$PROJECT_ID/$REPO${RESET}"
echo
echo -e "  ${YELLOW}下一步：${RESET}"
echo -e "  1. 在 GCP Console 設定 OAuth consent screen（External）"
echo -e "  2. 建立 OAuth 2.0 credentials，下載後填入 GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET"
echo -e "  3. 執行 ${BOLD}./scripts/deploy.sh${RESET} 部署服務"
echo -e "  4. 將 Cloud Run URL 填回 GOOGLE_REDIRECT_URI secret"
echo
