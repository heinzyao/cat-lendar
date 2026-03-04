#!/usr/bin/env bash
# =============================================================================
# deploy.sh — 建置 Docker image 並部署到 Cloud Run
# 每次程式碼更新後執行此腳本即可
# =============================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${BLUE}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; exit 1; }
step()    { echo -e "\n${BOLD}▶ $*${RESET}"; }

# ── 設定變數 ──────────────────────────────────────────────────────────────────
PROJECT_ID="${GCP_PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${GCP_REGION:-asia-east1}"
SERVICE_NAME="${SERVICE_NAME:-line-calendar-bot}"
REPO="line-bot"
IMAGE_BASE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/${SERVICE_NAME}"
SA_EMAIL="${SERVICE_NAME}-sa@${PROJECT_ID}.iam.gserviceaccount.com"

[[ -z "$PROJECT_ID" ]] && error "請設定 GCP_PROJECT_ID 或執行 gcloud config set project"

# 用 git short hash 當 image tag，fallback 到 timestamp
if git rev-parse --git-dir >/dev/null 2>&1; then
  TAG=$(git rev-parse --short HEAD)
else
  TAG=$(date +%Y%m%d%H%M%S)
fi
IMAGE="${IMAGE_BASE}:${TAG}"
IMAGE_LATEST="${IMAGE_BASE}:latest"

info "Project:  $PROJECT_ID"
info "Region:   $REGION"
info "Service:  $SERVICE_NAME"
info "Image:    $IMAGE"

# ── 前置檢查 ──────────────────────────────────────────────────────────────────
step "前置檢查"

command -v gcloud >/dev/null 2>&1 || error "請先安裝 gcloud CLI"
command -v docker >/dev/null 2>&1 || error "請先安裝 Docker"

gcloud config set project "$PROJECT_ID" --quiet

# ── Docker 認證 ───────────────────────────────────────────────────────────────
step "設定 Artifact Registry 認證"
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet
success "Docker 認證完成"

# ── 建置 Image ────────────────────────────────────────────────────────────────
step "建置 Docker Image"
docker build \
  --platform linux/amd64 \
  --tag "$IMAGE" \
  --tag "$IMAGE_LATEST" \
  --file Dockerfile \
  .
success "Image 建置完成: $IMAGE"

# ── 推送 Image ────────────────────────────────────────────────────────────────
step "推送 Image 到 Artifact Registry"
docker push "$IMAGE"
docker push "$IMAGE_LATEST"
success "Image 推送完成"

# ── 取得 GOOGLE_REDIRECT_URI ─────────────────────────────────────────────────
# 先嘗試取得已存在的服務 URL，作為 redirect URI
EXISTING_URL=$(gcloud run services describe "$SERVICE_NAME" \
  --region="$REGION" \
  --format="value(status.url)" \
  --quiet 2>/dev/null || true)

if [[ -n "$EXISTING_URL" ]]; then
  REDIRECT_URI="${EXISTING_URL}/oauth/callback"
  info "使用現有服務 URL: $REDIRECT_URI"
else
  warn "首次部署：GOOGLE_REDIRECT_URI 將在部署後更新"
  REDIRECT_URI="https://placeholder.example.com/oauth/callback"
fi

# ── 部署到 Cloud Run ──────────────────────────────────────────────────────────
step "部署到 Cloud Run"

# 所有從 Secret Manager 掛載的環境變數
# 格式: ENV_VAR=secret-name:version
SECRET_MAPPINGS=(
  "LINE_CHANNEL_SECRET=LINE_CHANNEL_SECRET:latest"
  "LINE_CHANNEL_ACCESS_TOKEN=LINE_CHANNEL_ACCESS_TOKEN:latest"
  "ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest"
  "GOOGLE_CLIENT_ID=GOOGLE_CLIENT_ID:latest"
  "GOOGLE_CLIENT_SECRET=GOOGLE_CLIENT_SECRET:latest"
  "ENCRYPTION_KEY=ENCRYPTION_KEY:latest"
)
SET_SECRETS=$(IFS=','; echo "${SECRET_MAPPINGS[*]}")

gcloud run deploy "$SERVICE_NAME" \
  --image="$IMAGE" \
  --region="$REGION" \
  --platform=managed \
  --service-account="$SA_EMAIL" \
  --set-secrets="$SET_SECRETS" \
  --set-env-vars="GCP_PROJECT_ID=${PROJECT_ID},GOOGLE_REDIRECT_URI=${REDIRECT_URI}" \
  --allow-unauthenticated \
  --min-instances=0 \
  --max-instances=10 \
  --concurrency=80 \
  --cpu=1 \
  --memory=512Mi \
  --timeout=30 \
  --quiet

success "部署完成"

# ── 取得 Cloud Run URL ───────────────────────────────────────────────────────
step "取得服務 URL"
SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" \
  --region="$REGION" \
  --format="value(status.url)" \
  --quiet)

WEBHOOK_URL="${SERVICE_URL}/webhook"
REDIRECT_URI_FINAL="${SERVICE_URL}/oauth/callback"

# 若是首次部署，自動更新 GOOGLE_REDIRECT_URI secret
if [[ "$REDIRECT_URI" == *"placeholder"* ]]; then
  step "更新 GOOGLE_REDIRECT_URI Secret"
  echo -n "$REDIRECT_URI_FINAL" | gcloud secrets versions add GOOGLE_REDIRECT_URI \
    --data-file=- \
    --quiet 2>/dev/null || \
  echo -n "$REDIRECT_URI_FINAL" | gcloud secrets create GOOGLE_REDIRECT_URI \
    --data-file=- \
    --replication-policy=automatic \
    --quiet

  # 重新部署以套用更新後的 GOOGLE_REDIRECT_URI
  info "重新部署以套用 GOOGLE_REDIRECT_URI..."
  gcloud run deploy "$SERVICE_NAME" \
    --image="$IMAGE" \
    --region="$REGION" \
    --update-secrets="GOOGLE_REDIRECT_URI=GOOGLE_REDIRECT_URI:latest" \
    --quiet
  success "GOOGLE_REDIRECT_URI 已更新"
fi

# ── 健康檢查 ─────────────────────────────────────────────────────────────────
step "健康檢查"
sleep 3
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "${SERVICE_URL}/health" || echo "000")
if [[ "$HTTP_STATUS" == "200" ]]; then
  success "Health check 通過 (HTTP $HTTP_STATUS)"
else
  warn "Health check 回傳 HTTP $HTTP_STATUS，請確認服務狀態"
fi

# ── 完成摘要 ─────────────────────────────────────────────────────────────────
echo
echo -e "${GREEN}${BOLD}════════════════════════════════════════════════════════${RESET}"
echo -e "${GREEN}${BOLD}  部署完成！${RESET}"
echo -e "${GREEN}${BOLD}════════════════════════════════════════════════════════${RESET}"
echo
echo -e "  Cloud Run URL:    ${BOLD}$SERVICE_URL${RESET}"
echo
echo -e "  ${YELLOW}LINE Bot Webhook URL（填入 LINE Developers Console）：${RESET}"
echo -e "  ${BOLD}$WEBHOOK_URL${RESET}"
echo
echo -e "  ${YELLOW}Google OAuth Redirect URI（填入 GCP OAuth 2.0 Credentials）：${RESET}"
echo -e "  ${BOLD}$REDIRECT_URI_FINAL${RESET}"
echo
echo -e "  Image: ${BOLD}$IMAGE${RESET}"
echo
