"""行程提醒通知路由：由 Cloud Scheduler 定期觸發，推播到期提醒至使用者。

設計理由——定時推播架構
-----------------------
行程提醒採用「輪詢（polling）」而非「實時計算」的設計：

  Cloud Scheduler → POST /internal/notify（每分鐘）
       ↓
  notification.check_and_send_reminders()
       ├─ 從 Firestore 查詢 reminder_at <= now 且 sent==False 的記錄
       └─ 逐一推播 LINE 通知 → 標記 sent=True

為何選擇輪詢而非精確定時？
- Google Cloud 無法對每筆資料設定獨立的定時器
- Firestore 查詢 reminder_at <= now 很有效率（支援 WHERE 條件的索引）
- 最大誤差為輪詢間隔（1 分鐘），對行程提醒場景可接受

端點安全：
- /internal/notify 只應被 Cloud Scheduler 呼叫，不對一般使用者開放
- 透過 X-Internal-Secret 標頭進行身份驗證（settings.notify_secret）
- 若 notify_secret 未設定（空字串），一律拒絕（403）
  設計理由：寧可功能停用也不要無身份驗證的端點

路徑選擇 /internal/notify 而非 /notify：
- /internal/ 前綴明確標示這是內部端點
- 若未來部署在 GKE 可設定 NetworkPolicy 只允許 Cloud Scheduler IP 段存取
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from app.config import settings
from app.services import notification

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/internal/notify")
async def internal_notify(request: Request):
    """由 Cloud Scheduler 每分鐘呼叫，掃描並推播到期的行程提醒。

    驗證策略：
    - 要求 X-Internal-Secret 標頭與 settings.notify_secret 完全一致
    - notify_secret 若為空字串（未設定），一律拒絕（防止未設定時被任意觸發）
    """
    secret = request.headers.get("X-Internal-Secret", "")
    # 雙重驗證：settings.notify_secret 必須已設定，且與請求標頭一致
    if not settings.notify_secret or secret != settings.notify_secret:
        raise HTTPException(status_code=403, detail="Forbidden")

    sent = await notification.check_and_send_reminders()
    return {"sent": sent}  # 回傳本次推播的通知數量，供 Cloud Scheduler 日誌記錄
