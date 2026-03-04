#!/usr/bin/env bash
# =============================================================================
# dev.sh — 本地開發環境
# 啟動 uvicorn + ngrok，自動將 webhook URL 印出供設定 LINE Bot
# 需求: uv、ngrok（brew install ngrok）、.env 檔案
# =============================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${BLUE}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; exit 1; }

PORT="${PORT:-8080}"
ENV_FILE="${ENV_FILE:-.env}"

# ── 前置檢查 ──────────────────────────────────────────────────────────────────
command -v uv >/dev/null 2>&1     || error "請先安裝 uv: https://docs.astral.sh/uv/"
command -v ngrok >/dev/null 2>&1  || error "請先安裝 ngrok: brew install ngrok"
command -v curl >/dev/null 2>&1   || error "請先安裝 curl"

[[ -f "$ENV_FILE" ]] || error "找不到 $ENV_FILE，請複製 .env.example 並填入設定"

cleanup() {
  info "清理背景程序..."
  [[ -n "${UVICORN_PID:-}" ]] && kill "$UVICORN_PID" 2>/dev/null || true
  [[ -n "${NGROK_PID:-}" ]]   && kill "$NGROK_PID"   2>/dev/null || true
}
trap cleanup EXIT INT TERM

# ── 啟動 uvicorn ──────────────────────────────────────────────────────────────
info "啟動 uvicorn (port $PORT)..."
uv run uvicorn app.main:app --reload --port "$PORT" --env-file "$ENV_FILE" &
UVICORN_PID=$!

# 等待 uvicorn 就緒
for i in $(seq 1 15); do
  if curl -sf "http://localhost:${PORT}/health" >/dev/null 2>&1; then
    success "uvicorn 已就緒"
    break
  fi
  sleep 1
  [[ $i -eq 15 ]] && error "uvicorn 啟動逾時，請檢查 .env 設定"
done

# ── 啟動 ngrok ────────────────────────────────────────────────────────────────
info "啟動 ngrok..."
ngrok http "$PORT" --log=stdout > /tmp/ngrok_dev.log 2>&1 &
NGROK_PID=$!
sleep 3

# 從 ngrok API 取得 public URL
NGROK_URL=""
for i in $(seq 1 10); do
  NGROK_URL=$(curl -sf http://localhost:4040/api/tunnels \
    | python3 -c "
import sys, json
data = json.load(sys.stdin)
tunnels = data.get('tunnels', [])
for t in tunnels:
    if t.get('proto') == 'https':
        print(t['public_url'])
        break
" 2>/dev/null || true)
  [[ -n "$NGROK_URL" ]] && break
  sleep 1
done

[[ -z "$NGROK_URL" ]] && error "無法取得 ngrok URL，請確認 ngrok 已登入 (ngrok config add-authtoken <token>)"

WEBHOOK_URL="${NGROK_URL}/webhook"
REDIRECT_URI="${NGROK_URL}/oauth/callback"

echo
echo -e "${GREEN}${BOLD}════════════════════════════════════════════════════════${RESET}"
echo -e "${GREEN}${BOLD}  本地開發環境已就緒${RESET}"
echo -e "${GREEN}${BOLD}════════════════════════════════════════════════════════${RESET}"
echo
echo -e "  ${YELLOW}LINE Bot Webhook URL：${RESET}"
echo -e "  ${BOLD}$WEBHOOK_URL${RESET}"
echo
echo -e "  ${YELLOW}Google OAuth Redirect URI：${RESET}"
echo -e "  ${BOLD}$REDIRECT_URI${RESET}"
echo
echo -e "  ngrok 管理介面: ${BOLD}http://localhost:4040${RESET}"
echo -e "  按 ${BOLD}Ctrl+C${RESET} 停止所有服務"
echo

# ── 等待並監控 ────────────────────────────────────────────────────────────────
# 若任一程序結束，發出警告
while true; do
  if ! kill -0 "$UVICORN_PID" 2>/dev/null; then
    error "uvicorn 意外終止"
  fi
  if ! kill -0 "$NGROK_PID" 2>/dev/null; then
    error "ngrok 意外終止"
  fi
  sleep 5
done
