"""一次性腳本：查詢本週 Google Calendar 並推播給指定 LINE 用戶。"""
import os, json
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from linebot.v3.messaging import ApiClient, Configuration, MessagingApi, PushMessageRequest, TextMessage

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../.env"))

LINE_USER_ID = "Uc7663e0e11db469ce88092dc227c7818"
CALENDAR_ID  = os.getenv("GOOGLE_CALENDAR_ID", "primary")
TZ = timezone(timedelta(hours=8))

# Google Calendar
creds = Credentials.from_service_account_info(
    json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]),
    scopes=["https://www.googleapis.com/auth/calendar.readonly"],
)
service = build("calendar", "v3", credentials=creds)

now = datetime.now(TZ)
week_end = now + timedelta(days=7)
result = service.events().list(
    calendarId=CALENDAR_ID,
    timeMin=now.isoformat(),
    timeMax=week_end.isoformat(),
    singleEvents=True,
    orderBy="startTime",
    maxResults=20,
).execute()
events = result.get("items", [])

if not events:
    msg = "📅 未來 7 天沒有行程。"
else:
    lines = ["📅 未來 7 天的行程：\n"]
    for i, e in enumerate(events, 1):
        start = e.get("start", {})
        dt = start.get("dateTime", start.get("date", ""))
        try:
            t = datetime.fromisoformat(dt).astimezone(TZ).strftime("%m/%d %H:%M")
        except Exception:
            t = dt
        lines.append(f"{i}. {e.get('summary','(無標題)')} — {t}")
    msg = "\n".join(lines)

# LINE Push
api = MessagingApi(ApiClient(Configuration(access_token=os.environ["LINE_CHANNEL_ACCESS_TOKEN"])))
api.push_message(PushMessageRequest(to=LINE_USER_ID, messages=[TextMessage(text=msg)]))
print("推播成功：")
print(msg)
