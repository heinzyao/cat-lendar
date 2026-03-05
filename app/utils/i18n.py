"""繁體中文訊息模板"""

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
GENERAL_ERROR = "系統發生錯誤，請稍後再試。"

# Cross-user notifications
NOTIFY_EVENT_CREATED = "📅 {name} 新增了行程\n📌 {summary}\n🕐 {time}"
NOTIFY_EVENT_UPDATED = "✏️ {name} 修改了行程\n📌 {summary}\n🕐 {time}"
NOTIFY_EVENT_DELETED = "🗑️ {name} 刪除了行程：{summary}"

# Reminders
REMINDER_SET = "⏰ 已設定提醒：行程開始前 {minutes} 分鐘通知"
REMINDER_UPDATED = "⏰ 已更新提醒設定：開始前 {minutes} 分鐘"
REMINDER_DELETED = "🔕 已取消行程提醒"
REMINDER_NOTIFICATION = "⏰ 提醒：{summary} 將在 {minutes} 分鐘後開始\n🕐 {time}"
DEFAULT_REMINDER_SET = "✅ 已設定預設提醒：每個行程開始前 {minutes} 分鐘提醒"
DEFAULT_REMINDER_CLEARED = "✅ 已關閉預設提醒"
REMINDER_EVENT_NOT_FOUND = "找不到符合的行程，請確認行程名稱或時間。"

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
    "⏰ 行程提醒\n"
    "・明天下午 2 點開會，提前 15 分鐘提醒\n"
    "・幫明天的開會設定 30 分鐘前提醒\n"
    "・設定預設提醒 30 分鐘前（所有新行程自動套用）\n"
    "・關閉預設提醒\n\n"
    "⚙️ 其他指令\n"
    "・說明 / help：顯示此說明"
)
