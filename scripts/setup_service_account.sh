#!/usr/bin/env bash
# =============================================================================
# setup_service_account.sh — 一次性設定 Google Service Account
#
# 執行步驟：
#   1. 建立 Service Account
#   2. 建立 JSON 金鑰並存入 Secret Manager（GOOGLE_SERVICE_ACCOUNT_JSON）
#   3. 顯示 Service Account email，供手動分享 Google Calendar
#
# 使用方式：
#   GCP_PROJECT_ID=your-project ./scripts/setup_service_account.sh
# =============================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BOLD='\033[1m'; RESET='\033[0m'
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; exit 1; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
info()    { echo -e "${YELLOW}[INFO]${RESET}  $*"; }

PROJECT_ID="${GCP_PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
[[ -z "$PROJECT_ID" ]] && error "請設定 GCP_PROJECT_ID 或執行 gcloud config set project <PROJECT_ID>"

SA_NAME="${SA_NAME:-cat-lendar-calendar}"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
SECRET_NAME="GOOGLE_SERVICE_ACCOUNT_JSON"
KEY_FILE=$(mktemp /tmp/sa-key-XXXXXX.json)
trap 'rm -f "$KEY_FILE"' EXIT

echo ""
echo -e "${BOLD}=== Cat-Lendar Service Account 設定 ===${RESET}"
echo "Project : $PROJECT_ID"
echo "SA Email: $SA_EMAIL"
echo ""

# 確認 Calendar API 已啟用
info "確認 Google Calendar API 已啟用..."
gcloud services enable calendar-json.googleapis.com --project="$PROJECT_ID" --quiet
success "Calendar API 已啟用"

# 建立 Service Account（若已存在則跳過）
SA_CREATED=false
if gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" &>/dev/null; then
  info "Service Account 已存在，略過建立"
else
  gcloud iam service-accounts create "$SA_NAME" \
    --display-name="Cat-Lendar Calendar Bot" \
    --project="$PROJECT_ID"
  success "Service Account 已建立"
  SA_CREATED=true
fi

# 建立新 JSON 金鑰（新建的 SA 需等待 GCP propagation）
info "建立 JSON 金鑰..."
if [[ "$SA_CREATED" == "true" ]]; then
  info "等待 Service Account 生效（最多 30 秒）..."
  for i in $(seq 1 6); do
    sleep 5
    if gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" &>/dev/null; then
      break
    fi
    [[ $i -eq 6 ]] && error "Service Account 未能在 30 秒內生效，請稍後重試"
  done
fi
gcloud iam service-accounts keys create "$KEY_FILE" \
  --iam-account="$SA_EMAIL"
success "JSON 金鑰已建立"

# 寫入 Secret Manager
if gcloud secrets describe "$SECRET_NAME" --project="$PROJECT_ID" &>/dev/null; then
  info "更新既有 Secret：$SECRET_NAME"
  gcloud secrets versions add "$SECRET_NAME" \
    --data-file="$KEY_FILE" \
    --project="$PROJECT_ID"
else
  info "建立新 Secret：$SECRET_NAME"
  gcloud secrets create "$SECRET_NAME" \
    --data-file="$KEY_FILE" \
    --project="$PROJECT_ID"
fi
success "Secret $SECRET_NAME 已更新"

echo ""
echo -e "${BOLD}======================================${RESET}"
echo -e "${BOLD}下一步：將行事曆分享給 Service Account${RESET}"
echo -e "${BOLD}======================================${RESET}"
echo ""
echo "Service Account Email："
echo ""
echo -e "  ${BOLD}${SA_EMAIL}${RESET}"
echo ""
echo "操作步驟："
echo "  1. 開啟 Google Calendar → 目標行事曆 → 設定與共用"
echo "  2. 在「與特定使用者共用」輸入上方 Email"
echo "  3. 權限設定為「變更活動」（Make changes to events）"
echo "  4. 設定 GOOGLE_CALENDAR_ID 為目標行事曆 ID"
echo "  5. 重新部署 Cloud Run：./scripts/deploy.sh"
echo ""
echo -e "${GREEN}完成後 cat-lendar 即可使用，無需定期重新授權。${RESET}"
