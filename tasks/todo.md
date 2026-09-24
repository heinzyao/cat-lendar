# 遷移 NLP 至 google-genai SDK

`google-generativeai` 已被 Google 標記停止支援（import 時噴 FutureWarning），
且在 Python 3.14 的相依解析會失敗——本專案 venv 已是 3.14，屬於遲早要還的債。
同時把兩個 prompt 裡手寫的 JSON schema 移到協議層的 `response_schema`。

所有 genai 用法都集中在 `app/services/nlp.py`，`parse_intent` / `parse_update_details`
的對外介面不變，`app/handlers/message.py` 不需改動。

## Checklist

- [x] 1. 相依：移除 `google-generativeai`，加入 `google-genai`（2.25.0）
- [x] 2. 模型：抽出 `CalendarIntentPayload`（wire 形狀，不含 `original_message`）
      作為 `CalendarIntent` 的 base，供 `response_schema` 使用
- [x] 3. 確認 Gemini 接受這兩個 schema —— 對真實 API 驗過，`datetime`、巢狀 Optional、
      Enum、float 範圍全部接受
- [x] 4. `nlp.py` 改寫：client singleton、`client.aio` 非同步、safety settings 新形狀、
      multi-turn history 改 `types.Content`、兩處 `response_schema`
- [x] 5. 刪掉兩個 prompt 裡的手寫 JSON schema（共 26 行）
- [x] 6. 改寫測試（`test_nlp_update.py`、`test_conversation_memory.py`）
- [x] 7. 全測通過（69 passed）
- [x] 8. 真實 API 端對端驗證（三條路徑）
- [x] 9. 部署後以簽章模擬 webhook 驗 production（revision `00048-r89`，image tag `965db3f`）

## Review

**介面零變動。** `parse_intent` / `parse_update_details` 的簽章與回傳型別都沒改，
`app/handlers/message.py` 一個字都不用動。這是遷移能做得這麼乾淨的原因——
舊 SDK 的用法本來就全部關在 `nlp.py` 裡。

**`response.parsed` 取代了整條解析鏈。** 舊流程是
`response.text` → 剝 markdown fence → `json.loads()` → `model_validate()`；
現在 API 直接回型別化物件。錯誤處理也跟著簡化成一個 `isinstance` 檢查——
被安全過濾擋下、或回傳不符 schema，都會讓 `parsed` 是 None，走同一條路。

**為什麼要多一個 `CalendarIntentPayload`。** `original_message` 是解析完我們自己補的，
不是模型輸出。直接把 `CalendarIntent` 當 `response_schema` 等於要求 Gemini 把使用者
原話再抄一遍——浪費 token，而且 schema 會說謊。拆成 base（wire）+ 子類（內部）
可以零重複達成，handlers 完全無感。

**測試從 6 個變 5 個。** 原本「JSON 解析失敗」與「Pydantic 驗證失敗」是兩個測試，
在 `response_schema` 之後它們是同一件事（`parsed` 拿不到合法物件），合併為一。
測試的 mock 點也從 `_get_model` 移到 `_get_client`。

**驗證方式的取捨。** schema 能不能被接受，mock 測試永遠測不到——本地
`GenerateContentConfig(response_schema=...)` 只是把 model class 存起來，
真正的轉換與驗證發生在 API 端。所以第 3 項與第 8 項都是打真實 API，
這是整個遷移唯一無法靠單元測試覆蓋的風險。

**其餘模組的過期指涉已一併清除。** `app/handlers/message.py` 等 7 個檔案共 21 處
註解寫「Claude」，是 2026-05-19 Claude→Gemini 遷移留下的，已在後續 commit 清掉。
`AGENT.md` 的「Claude Code」指的是編碼工具而非 NLP 模型，刻意保留。

## Production 驗證結果（revision 00048-r89）

新相依在 Cloud Run 裝得起來也跑得起來：`ImportError` / `ModuleNotFound` /
`AttributeError` 與 genai 相關錯誤各 0 次，新程式碼的失敗路徑
「未回傳合法 payload」觸發 0 次。

送兩則查詢類指令（刻意不送 create/update，避免動到真實日曆），流程都走到
`_handle_query` 的 reply 那一步才因假 replyToken 失敗。能走到那裡即代表
`parse_intent` 產出了合法的 `action=query` intent（confidence ≥ 0.5）、
Google Calendar 查詢也成功，只差把結果送回 LINE。

**驗不到的部分：** 解析出的具體欄位值。本專案不像 diffords 會 log 解析結果，
而 replyToken 是模擬的，看不到回覆內容。這是簽章模擬 webhook 的天花板，
最後一段要從真實 LINE 帳號發訊息才能確認。

### 兩個過程中的發現

1. **模擬事件要補 `quoteToken`。** 少了它會被 linebot v3 的 `WebhookParser` 擋掉
   （log 顯示 `Unknown event type. type=message`），但 HTTP 仍回 200 且只花 0.1 秒——
   只看狀態碼會誤判成功。本專案走 `WebhookParser`，比 diffords 手寫的 Flask
   webhook 嚴格，日後寫探測腳本要記得。
2. **`Calendar operation failed` 這個 log 標籤會誤導。** 實際失敗的是
   `line_messaging.reply_text`，日曆操作本身成功。將來真的日曆故障時
   無法從訊息區分兩者。→ **已於 `08acf71` 修掉**，見下。

## 後續：回覆失敗不再偽裝成日曆錯誤（`08acf71`，revision 00049-q2m）

上面第 2 點追查後發現不只是標籤問題。`_execute_intent` 的 `except Exception`
同時蓋住「日曆操作失敗」與「各 `_handle_*` 內部 reply 失敗」，後者發生時除了
記錯標籤，還會用同一個已失效的 replyToken **再 reply 一次**——那次註定失敗，
而且會把原始錯因蓋掉。

改成先攔 `ApiException`（只記 `LINE reply failed`、不重試），其餘維持原行為。

### Production 驗證（送兩則查詢，各觸發一次回覆失敗）

| 指標 | 修正前（00048-r89） | 修正後（00049-q2m） |
|---|---|---|
| `LINE reply failed` | 0 | 2 |
| `Calendar operation failed` | 2（誤導） | 0 |
| `Invalid reply token` | 4 | 2 |

`Invalid reply token` 從 4 次降到 2 次是關鍵證據：每則訊息原本會嘗試回覆兩次
（正常回覆 + except 裡的錯誤回覆），現在只剩一次，代表那個註定失敗的重試
真的不再發生。traceback 也停在 `_handle_query` 的 reply 那行，不再被二次失敗蓋掉。

兩個單元測試各釘一邊（`ApiException` 不得重試 / 其他例外仍須通知使用者），
並確認過拿掉 `ApiException` 分支後會 FAILED，不是恆真斷言。

## 端對端確認（2026-09-24）

使用者從真實 LINE 帳號發訊息，**回覆正確**。這補上了簽章模擬 webhook
驗不到的最後一段——模擬事件的 replyToken 是假的，看不到回覆內容，
所以解析出的具體欄位值只有真實帳號才驗得到。

至此整個遷移完成：SDK 換血、schema 移到協議層、註解同步、錯誤處理分流，
單元測試（71）、真實 API 呼叫、production log、真實 LINE 回覆四層都驗過。
