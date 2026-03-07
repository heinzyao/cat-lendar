"""繁體中文訊息模板"""

# Calendar operations
EVENT_CREATED = "幫你記到日曆上囉！📝\n📌 {summary}\n🕐 {time}"
EVENT_CREATED_WITH_LOCATION = "幫你記到日曆上囉！📝\n📌 {summary}\n🕐 {time}\n📍 {location}"
EVENT_DELETED = "沒問題，已經把『{summary}』從日曆刪除囉！🗑️"
EVENT_UPDATED = "好喔，幫你把行程更新成這樣：\n📌 {summary}\n🕐 {time}"
NO_EVENTS_FOUND = "我翻了一下日曆，這段時間好像沒有行程耶。🤔"

EVENTS_LIST_HEADER = "幫你查了一下日曆，有這些安排 📅：\n"
EVENT_LIST_ITEM = "{index}. {summary}\n   🕐 {time}\n"

# Disambiguation
MULTIPLE_EVENTS_FOUND = "哎呀，日曆上剛好有幾個很像的行程，你指的是哪一個呢？（請回覆編號）\n"
SELECT_PROMPT = "\n請回覆數字（如：1）"

# Errors
CLARIFICATION_NEEDED = "嗯... 我有點不太懂 🤔\n{message}"
PARSE_ERROR = "不好意思，我聽不太懂這個指令 😅\n你可以隨性一點說，例如：\n• 幫我記下明天下午三點要開會\n• 這週有什麼安排嗎？\n• 幫我取消明天的晚餐"
CALENDAR_ERROR = "操作行事曆時發生錯誤，請稍後再試。"
GENERAL_ERROR = "系統發生錯誤，請稍後再試。"

# Cross-user notifications
NOTIFY_EVENT_CREATED = "📅 {name} 在日曆上新增了活動呦：\n📌 {summary}\n🕐 {time}"
NOTIFY_EVENT_UPDATED = "✏️ {name} 修改了一下行程：\n📌 {summary}\n🕐 {time}"
NOTIFY_EVENT_DELETED = "🗑️ {name} 取消了這個行程：\n📌 {summary}"

# Notification preferences
NOTIFY_ENABLED = "✅ 已開啟行事曆異動通知，其他人新增、修改、刪除行程時會通知你喔！"
NOTIFY_DISABLED = "🔕 已關閉行事曆異動通知，之後不會再推播其他人的行程變動了。"
# Reminders
REMINDER_SET = "⏰ 沒問題！會在 {minutes} 分鐘前提醒你喔。"
REMINDER_UPDATED = "⏰ 已更新提醒設定：開始前 {minutes} 分鐘"
REMINDER_DELETED = "🔕 已取消行程提醒"
REMINDER_NOTIFICATION = "⏰ 提醒：{summary} 將在 {minutes} 分鐘後開始\n🕐 {time}"
DEFAULT_REMINDER_SET = "✅ 已設定預設提醒：每個行程開始前 {minutes} 分鐘提醒"
DEFAULT_REMINDER_CLEARED = "✅ 已關閉預設提醒"
REMINDER_EVENT_NOT_FOUND = "找不到符合的行程，請確認行程名稱或時間。"

# Help
HELP_MESSAGE = (
    "哈囉！我是 Cat-Lendar，是你在 LINE 上的日曆小幫手 🗓️\n"
    "其實這個日曆是主人的專屬日曆，\n"
    "如果群組裡的大家也找我記事情，行程都會一起記在主人的日曆上喔！\n\n"
    "你可以像跟朋友聊天一樣告訴我：\n\n"
    "➕ 記下行程\n"
    "・『幫我記明天下午 2 點開會』\n"
    "・『下週一整天我要請假』\n\n"
    "🔍 偷看日曆\n"
    "・『今天有什麼行程？』\n"
    "・『明天要開會嗎？』\n\n"
    "✏️ 臨時變卦\n"
    "・『把明天的開會改到後天下午 3 點』\n"
    "・『把下午 2 點的行程改名為客戶簡報』\n\n"
    "🗑️ 取消計畫\n"
    "・『幫我取消明天的會議』\n\n"
    "⏰ 貼心提醒\n"
    "・『幫明天的開會設定 30 分鐘前提醒』\n"
    "・『設定預設提醒 30 分鐘前』（以後每個新行程都會自動設好）\n"
    "・『關閉預設提醒』\n\n"
    "\n⚙️ 通知設定\n"
    "・『關閉通知』（不再接收其他人的行程變動推播）\n"
    "・『開啟通知』（恢復接收）\n\n"
    "如果有什麼不懂的，隨時打『說明』或『help』叫我出來呦！"
)
