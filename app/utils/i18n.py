"""繁體中文訊息模板"""

# OAuth
AUTH_REQUIRED = "你尚未授權 Google 日曆，請點擊下方按鈕進行授權："
AUTH_BUTTON_LABEL = "授權 Google 日曆"
AUTH_SUCCESS = "Google 日曆授權成功！現在可以開始使用了 ✅\n試試看傳送「明天下午兩點開會」"
AUTH_FAILED = "授權失敗，請稍後再試。"
AUTH_STATE_EXPIRED = "授權連結已過期，請重新操作。"

# Calendar operations
EVENT_CREATED = "已建立行程：\n📌 {summary}\n🕐 {time}"
EVENT_CREATED_WITH_LOCATION = "已建立行程：\n📌 {summary}\n🕐 {time}\n📍 {location}"
EVENT_DELETED = "已刪除行程：{summary}"
EVENT_UPDATED = "已更新行程：\n📌 {summary}\n🕐 {time}"
NO_EVENTS_FOUND = "在指定時間範圍內沒有找到行程。"

EVENTS_LIST_HEADER = "📅 查詢結果：\n"
EVENT_LIST_ITEM = "{index}. {summary}\n   🕐 {time}\n"

# Disambiguation
MULTIPLE_EVENTS_FOUND = "找到多筆相符的行程，請輸入編號選擇：\n"
SELECT_PROMPT = "\n請回覆數字（如：1）"

# Errors
CLARIFICATION_NEEDED = "🤔 {message}"
PARSE_ERROR = "抱歉，我無法理解你的指令。\n你可以試試：\n• 明天下午 3 點開會\n• 查詢本週行程\n• 刪除明天的會議"
CALENDAR_ERROR = "操作行事曆時發生錯誤，請稍後再試。"
TOKEN_EXPIRED = "你的 Google 授權已失效，請重新授權。"
GENERAL_ERROR = "系統發生錯誤，請稍後再試。"

# Calendar mode selection
CHOOSE_CALENDAR_MODE = (
    "👋 歡迎使用 Cat-Lendar！\n\n"
    "請選擇行事曆模式：\n"
    "1️⃣  Google Calendar（需授權 Google 帳號）\n"
    "2️⃣  內建行事曆（無需第三方帳號，資料存於 Firestore）\n\n"
    "請回覆 1 或 2："
)
CALENDAR_MODE_SET_GOOGLE = "已設定使用 Google Calendar，請點擊下方按鈕完成授權："
CALENDAR_MODE_SET_LOCAL = "✅ 已設定使用內建行事曆，可以開始新增行程了！"
SWITCH_CALENDAR_PROMPT = (
    "目前模式：{current_mode}\n\n"
    "請選擇新的行事曆模式：\n"
    "1️⃣  Google Calendar\n"
    "2️⃣  內建行事曆\n\n"
    "請回覆 1 或 2："
)
MIGRATION_PROMPT = (
    "是否將舊行程遷移到新行事曆？\n"
    "1️⃣  是，遷移舊行程\n"
    "2️⃣  否，不遷移\n\n"
    "請回覆 1 或 2："
)
MIGRATION_SUCCESS = "✅ 已完成遷移，共轉移 {count} 筆行程。"
MIGRATION_SKIPPED = "✅ 已切換到{mode}，舊行程保留不變。"

# Help
HELP_MESSAGE = (
    "📅 Cat-Lendar 使用說明\n\n"
    "➕ 新增行程\n"
    "・明天下午 2 點開會\n"
    "・週五早上 10 點到 12 點團隊會議，地點台北車站\n"
    "・下週一整天請假\n\n"
    "🔍 查詢行程\n"
    "・今天有什麼行程？\n"
    "・這週的行程\n"
    "・明天有沒有會議？\n\n"
    "✏️ 修改行程\n"
    "・把明天的開會改到後天下午 3 點\n"
    "・把週五的會議延後 1 小時\n"
    "・把下午 2 點的行程改名為客戶簡報\n"
    "・（改完後）再提前 30 分鐘\n"
    "・（改完後）地點改到信義區\n\n"
    "🗑️ 刪除行程\n"
    "・取消明天的會議\n"
    "・刪除週三下午的開會\n\n"
    "⚙️ 其他指令\n"
    "・切換行事曆：在 Google Calendar / 內建行事曆之間切換\n"
    "・解除授權：取消 Google 日曆連結\n"
    "・說明 / help：顯示此說明"
)
