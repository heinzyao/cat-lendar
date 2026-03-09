"""Cat-lendar：LINE × Google Calendar 智慧日程管理 Bot

架構概覽
--------
使用 FastAPI 作為 ASGI 框架，核心設計為「輕量入口 + 服務分層」：

┌─────────────────────────────────────┐
│  LINE Webhook (routes/webhook.py)   │  ← 接收 LINE 訊息事件
│  Notify Cron  (routes/notify.py)    │  ← 定期觸發提醒推播
└────────────────┬────────────────────┘
                 │
        handlers/message.py           ← 意圖協調器：解析 → 執行 → 回覆
                 │
    ┌────────────┼────────────┐
    │            │            │
 services/    services/    store/
  nlp.py     calendar.py  firestore.py
 (Claude AI)  (GCal API)   (GCP NoSQL)

設計理由：
- FastAPI 選用：原生支援 async/await，與 Google Cloud 客戶端非同步操作契合，
  且 OpenAPI 文件對 LINE Webhook 驗證場景無用，故關閉 docs_url/redoc_url。
- 路由分離：webhook（即時回覆）與 notify（定時排程）職責不同，各自獨立 Router
  方便獨立部署、測試與流量控制。
- /health 端點：供 Cloud Run、GKE 等容器化平台進行健康檢查，也可用於監控告警。
"""

import logging

from fastapi import FastAPI

from app.routes.notify import router as notify_router
from app.routes.webhook import router as webhook_router

# 設定日誌格式：timestamp + 層級 + 模組名稱，方便在 Cloud Logging 中追蹤問題來源
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# docs_url=None：關閉 Swagger UI，避免 LINE Webhook 設定頁面出現不必要的 API 文件端點
# redoc_url=None：同上，關閉 ReDoc 文件頁面
app = FastAPI(title="LINE Calendar Bot", docs_url=None, redoc_url=None)

# 掛載兩條路由：
# - webhook_router：處理 LINE 訊息（POST /webhook），需在 100ms 內回應 200 OK
# - notify_router：定時提醒推播（GET/POST /notify），由 Cloud Scheduler 觸發
app.include_router(webhook_router)
app.include_router(notify_router)


@app.get("/health")
async def health():
    """健康檢查端點。

    設計理由：
    - 回傳 {"status": "ok"} 而非詳細狀態，避免暴露內部資訊
    - 容器平台（Cloud Run）定期呼叫此端點判斷服務是否就緒
    """
    return {"status": "ok"}
