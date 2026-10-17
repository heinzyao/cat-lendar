"""sim_webhook.py — 對 production webhook 送一則帶合法簽章的模擬 LINE 訊息

用法: uv run python scripts/sim_webhook.py "說明" [假 userId]

- reply token 是假的，log 出現 LINE 400（Invalid reply token）是預期的
- 任何訊息都會寫 users/{userId}，查詢還會寫 conversation_history；結束前一律刪掉
- 只送唯讀指令（說明、查詢、看不懂的話），不要送新增／修改／刪除行程
"""

import base64
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import urllib.request

from google.cloud import firestore


def gcloud(*args: str) -> bytes:
    return subprocess.check_output(["gcloud", *args]).strip()


PROJECT = os.environ.get("GCP_PROJECT_ID") or gcloud("config", "get-value", "project").decode()
REGION = os.environ.get("GCP_REGION", "asia-east1")
COLLECTIONS = ("users", "conversation_history", "user_states", "user_prefs")

text = sys.argv[1]
uid = sys.argv[2] if len(sys.argv) > 2 else f"Usimtest{int(time.time())}"

url = gcloud("run", "services", "describe", "line-calendar-bot", f"--region={REGION}",
             f"--project={PROJECT}", "--format=value(status.url)").decode() + "/webhook"
secret = gcloud("secrets", "versions", "access", "latest",
                "--secret=CATLENDAR_LINE_CHANNEL_SECRET", f"--project={PROJECT}")
body = json.dumps({"destination": "sim", "events": [{
    "type": "message", "mode": "active", "timestamp": int(time.time() * 1000),
    "source": {"type": "user", "userId": uid},
    "replyToken": "0" * 32, "webhookEventId": f"sim-{uid}",
    "deliveryContext": {"isRedelivery": False},
    "message": {"type": "text", "id": "1", "quoteToken": "q", "text": text},
}]}).encode()
sig = base64.b64encode(hmac.new(secret, body, hashlib.sha256).digest()).decode()

db = firestore.Client(project=PROJECT)
try:
    req = urllib.request.Request(url, body, {"Content-Type": "application/json", "X-Line-Signature": sig})
    # webhook 會 await handle_message 跑完才回 200，回應時 Firestore 已寫完，不用等
    print("webhook:", urllib.request.urlopen(req, timeout=60).status)
    for c in COLLECTIONS:
        d = db.collection(c).document(uid).get()
        if d.exists:
            print(f"{c}:", d.to_dict())
finally:
    for c in COLLECTIONS:
        db.collection(c).document(uid).delete()
    print(f"已清除 {uid} 的測試資料")
