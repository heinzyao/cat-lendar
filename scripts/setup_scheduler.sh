#!/usr/bin/env bash
# 設定 Cloud Scheduler 每分鐘觸發提醒通知
set -euo pipefail

PROJECT_ID="${GCP_PROJECT_ID:-$(gcloud config get-value project)}"
REGION="${REGION:-asia-east1}"
SERVICE_NAME="${SERVICE_NAME:-line-calendar-bot}"

# 取得 Cloud Run 服務 URL
SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" \
  --region="$REGION" \
  --project="$PROJECT_ID" \
  --format="value(status.url)")

if [[ -z "$SERVICE_URL" ]]; then
  echo "❌ 找不到 Cloud Run 服務 URL，請確認服務已部署"
  exit 1
fi

echo "📡 Cloud Run URL: $SERVICE_URL"

# 建立或更新 NOTIFY_SECRET
echo "🔐 請輸入 NOTIFY_SECRET（直接 Enter 跳過若已存在）："
read -r SECRET_VALUE
if [[ -n "$SECRET_VALUE" ]]; then
  echo -n "$SECRET_VALUE" | gcloud secrets create notify-secret \
    --project="$PROJECT_ID" \
    --data-file=- 2>/dev/null || \
  echo -n "$SECRET_VALUE" | gcloud secrets versions add notify-secret \
    --project="$PROJECT_ID" \
    --data-file=-
  echo "✅ NOTIFY_SECRET 已更新"
fi

# 取得 secret 值（用於 Scheduler header）
NOTIFY_SECRET=$(gcloud secrets versions access latest \
  --secret=notify-secret \
  --project="$PROJECT_ID")

# 建立 Cloud Scheduler job（若已存在則更新）
JOB_NAME="notify-job"
if gcloud scheduler jobs describe "$JOB_NAME" \
  --location="$REGION" \
  --project="$PROJECT_ID" &>/dev/null; then
  echo "🔄 更新 Scheduler job..."
  gcloud scheduler jobs update http "$JOB_NAME" \
    --location="$REGION" \
    --project="$PROJECT_ID" \
    --schedule="* * * * *" \
    --uri="${SERVICE_URL}/internal/notify" \
    --http-method=POST \
    --headers="X-Internal-Secret=${NOTIFY_SECRET}" \
    --time-zone="Asia/Taipei"
else
  echo "➕ 建立 Scheduler job..."
  gcloud scheduler jobs create http "$JOB_NAME" \
    --location="$REGION" \
    --project="$PROJECT_ID" \
    --schedule="* * * * *" \
    --uri="${SERVICE_URL}/internal/notify" \
    --http-method=POST \
    --headers="X-Internal-Secret=${NOTIFY_SECRET}" \
    --time-zone="Asia/Taipei"
fi

echo "✅ Cloud Scheduler 設定完成！"
echo ""
echo "📋 更新 Cloud Run 環境變數："
echo "  gcloud run services update $SERVICE_NAME \\"
echo "    --region=$REGION \\"
echo "    --update-env-vars NOTIFY_SECRET=\$(gcloud secrets versions access latest --secret=notify-secret)"
