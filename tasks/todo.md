# 換 gemini-3.5-flash-lite＋相對日期 prompt＋時區防線

## Checklist

- [x] 1. `settings.gemini_model` 預設改 `gemini-3.5-flash-lite`（Cloud Run 沒有 `GEMINI_MODEL` 覆寫）；README 同步
- [x] 2. parse_update_details prompt：明天／後天等相對日期從「目前時間」起算。原行程 11/05 的對照實測：舊 prompt 6/12（「改到明天」→ 11/06），新 prompt 12/12
- [x] 3. `nlp._localize_times()`：只校正 Gemini 回傳的物件（parse_intent／parse_update_details），非本地時區保留牆上時刻改標本地、naive 補本地，校正時記 warning（lite 實測 12 次出現 1 次「10:00+00:00」；47 次抽樣從未「正確換算成 UTC」）；兩個 prompt 也明確要求帶本地位移、不要換算 UTC
- [x] 4. 第一版把校正做成 model validator，Opus 指出會連帶改寫程式內部建立的 model（例如由 Calendar API 的 UTC 時間建立）→ 改成只在 nlp 端校正，model 不動
- [x] 5. 測試 `tests/test_gemini_timezone.py`（巢狀 payload、兩條 parse 路徑、內部 model 不被改寫）；99 passed
- [x] 6. Opus review 兩輪（第二輪無阻擋）：prompt 位移改 ISO 格式 `+08:00`；外地時區不支援的取捨寫進 AGENTS.md；校正次數加進 Hermes 每日早報監控
- [x] 7. 部署並驗證（revision 00065，image 030ca04）：模擬「說明」200／0.41 s；「今天有什麼行程」200／2.2 s（換模型前 5.3–6.3 s），Gemini 解析 query conf 1.00；/internal/sync 200／0.48 s；無時區校正 log

## Review

# 回覆變快：「輸入中」動畫＋Gemini 設定評估

webhook 過去兩週 p50 6.3 s、p90 20.5 s；單次 parse_intent 4–13 s。使用者等待時看不到任何回饋。

## Checklist

- [x] 1. `line_messaging.show_loading()`：一對一聊天顯示「輸入中」（25 秒，回覆送出即消失）；失敗／超過 2 秒只記 log
- [x] 2. `handle_message` 在固定指令之後、選擇狀態機與 NLP 之前呼叫；固定指令（說明、通知開關等）不顯示
- [x] 3. 測試 `tests/test_loading_animation.py`；既有 conversation_memory 測試補 `show_loading` mock；92 passed
- [x] 4. 真實 Gemini 比較（15 題解析＋3 題時間精算 × 3 輪）：3.8-flash 現行 50/54、p50 6.1 s／p90 9.3 s；3.8-flash thinking=LOW 51/54、3.4／5.4 s（Gemini 端 504 兩次）；3.5-flash-lite 51/54、1.4／1.7 s（錯的都是「改到後天同一時間」把後天算成相對原行程）；thinking=MINIMAL 不支援。**換不換模型待 Henry 決定**，這次只上動畫
- [x] 5. Opus review（無阻擋問題）；94 passed
- [x] 6. 部署並驗證（revision 00064，image 5b64613）：模擬 webhook 查詢 200、Gemini 解析正常；模擬用的假 user ID 讓 show_loading 收 400 並被接住不擋流程；直接打 LINE API 確認請求格式正確（格式正確的 user ID＋loadingSeconds=25 → 202）

## Review

- Opus 採納：SDK 沒有 HTTP 逾時、`to_thread` 的執行緒取消不掉 → 請求加 `_request_timeout=2`；群組訊息用 user_id 呼叫會讓動畫跑到私訊 → `handle_message(one_on_one=...)` 由 webhook 依 `event.source.type == "user"` 判斷（原 docstring 說「群組會被拒絕」是錯的）；ApiClient 改用 `with` 關閉。
- Opus 部分採納：show_loading 串行吃 25 秒總時限——LINE 正常 0.1–0.3 s、最壞 2 s，保持串行換取簡單。
- Opus 不採納：新測試檔單獨跑會 import 失敗——`tests/conftest.py` 一定會載入並補環境變數。

# 外部 API 逾時短於 Cloud Run 30 秒上限

過去兩週 9 次請求被 Cloud Run 30 秒上限切斷（504）：/internal/sync 6 次、/webhook 4 次。sync 平常 p50 0.53 s／p99 1.05 s，
卡住就是整整 30 s；webhook 55 次中 p50 6.3 s、6 次 >20 s。原因：googleapiclient 預設逾時 60 s、google-genai 預設不設逾時，
連線卡住時撐到被切斷，使用者連 PARSE_ERROR 都收不到。

## Checklist

- [x] 1. Calendar API 自帶 `httplib2.Http(timeout=8)`（`calendar.HTTP_TIMEOUT_SEC`），維持不重試（POST 重試可能重複建立行程）
- [x] 2. Gemini client 加 `HttpOptions(timeout=20_000)`（`nlp.GEMINI_TIMEOUT_MS`），逾時走既有的 PARSE_ERROR 回覆
- [x] 3. `google-auth-httplib2`、`httplib2` 改列為直接依賴
- [x] 4. 測試：`tests/test_timeouts.py`（逾時值 < deploy.sh 的 `--timeout`、不重試）；86 passed
- [x] 5. 整個請求的總時限：webhook 所有 event 共用 25 秒（`webhook.HANDLE_DEADLINE_SEC`，逾時回 `i18n.TIMEOUT_ERROR`，請使用者先查詢再重試）；/internal/sync 25 秒（`notify.SYNC_DEADLINE_SEC`）逾時自己回 504 並留 log
- [x] 6. Opus review 兩輪（第二輪：無阻擋問題）；89 passed
- [x] 7. 部署並驗證（revision 00063，image 273f69b）：/internal/sync 200／0.54 s；模擬 webhook「說明」200、「今天有什麼行程」Gemini 解析＋Calendar 查詢成功（假 reply token 的 400 屬預期）

## Review

- Opus 第一輪採納：單次逾時加總仍會超過 30 秒 → 加總時限（最關鍵）；httplib2 逾時是 socket 層級、token refresh 會疊加 → 由總時限兜住；「不重試」測試改名並註明 httplib2 底層仍可能重送一次；型別標註改 `google.auth.credentials.Credentials`。
- Opus 第一輪不採納：「逾時例外可能沒被接住」——`handle_message` 對 NLP 與日曆操作都是 `except Exception`，逾時會回 PARSE_ERROR／CALENDAR_ERROR，已確認。
- Opus 第二輪接受的取捨（不改）：期限若落在 reply 之後會中斷跨用戶通知；取消不會停掉 `to_thread` 裡的 Calendar 呼叫，可能留下「有行程、沒提醒」。改之前是被 Cloud Run 在 30 秒直接切斷，結果相同或更糟。
- Opus 第二輪不成立：sync 504 被 Scheduler 立刻重跑而重疊——`sync-job` 未設 retryCount（預設 0）。

# 一次修改／取消多個行程 + 關鍵字搜尋改本地比對

## Checklist

- [x] 1. `EventTarget` + `CalendarIntentPayload.targets`：每個要找的行程一筆（keyword／time_range／event_details／instruction／match_all）
- [x] 2. `_handle_update`／`_handle_delete` 合併為 `_handle_targets`：單筆訊息也包成一個 target，回覆格式不變
- [x] 3. 明確的（唯一或 match_all）並行執行；單筆失敗標 ❌ 不中斷，只有一筆時 raise
- [x] 4. 模糊的列選單，可回「1 3」「1、3」「全部」複選；候選帶 target 以使用對應 instruction
- [x] 5. `query_events` 有 keyword 時不用 API `q`（斷詞搜尋，中文子字串會漏），抓 250 筆本地子字串比對；忽略 `[LINE: ...]`
- [x] 6. 只有關鍵字時搜尋範圍 ±7 天 → 過去 30 天～未來 90 天
- [x] 7. prompt：targets／match_all 規則、search_keyword 只放名稱詞
- [x] 8. 測試 82 passed；Opus 第二次 review 已處理（見下）
- [x] 9. 真實 Gemini 驗 targets schema 與多目標解析（2026-10-21 用 production key 只呼叫 NLP、不碰日曆：多筆取消／多筆修改／「都」match_all／單筆走頂層 keyword 共 6 例全對；parse_update_details「延後 30 分鐘」10:00→10:30 正確。單次 parse_intent 4–13 秒）
- [x] 10. 部署（2026-10-21 已部署 badd250，模擬 webhook＋結構 log 驗過；之後的提醒修正 37646dd 也已上線）

## Review

- `parse_update_details` 不再帶 user_id：觸發訊息已計過頻率，批次內每筆再計會把 10 筆以上的「都延後」自己擋掉。
- Opus review 採納：關鍵字範圍不往回搜（改今天起 90 天，免得「都取消」刪到已結束的）；同一 target 套多筆時二次解析失敗不 fallback（否則「延後一小時」全變同一時間）；跨 target 以 event id 去重；並行上限 4；純空白關鍵字會全中 → strip；多 target 時單筆失敗不 raise。
- Opus review 不採納：httplib2 共用（`_get_service` 每次 build 新的）；每筆一則跨用戶通知（與多筆新增既有行為一致）；大量刪除二次確認（使用者明說「全部／都」才會 match_all，待實際使用再評估）。
- 已知限制：關鍵字搜尋只抓一頁 250 筆；set_reminder 多筆選擇仍沿用 select_event_for_update（既有行為，未動）。

---

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
`AGENT.md`（現為 `AGENTS.md`）的「Claude Code」指的是編碼工具而非 NLP 模型，刻意保留。

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

---

# 修正 secret 比對與 register_user 背景執行（2026-10-16）

- [x] `/internal/*` 的 secret 比對改用 `hmac.compare_digest`（原本 `!=` 有 timing side channel）
- [x] `register_user` 從 `asyncio.create_task` 改為 await（加 3 秒上限與 try/except）：
      背景 task 沒有參照會被 GC，Cloud Run 回應後又會節流 CPU，「說明」這類短路徑可能跑不完，
      漏登記的用戶就收不到跨用戶通知
- [x] 測試：register_user 拋例外仍要回覆（74 passed）
- [x] 部署（revision 00057）：錯誤或缺少 secret 回 403、Scheduler 回 200；真實 LINE 訊息回覆正常，
      webhook 延遲 6.3–6.5 秒（舊版約 3.5–9 秒），也沒有 `register_user failed` log

## Review（agy，claude-opus-5-5-high）

- 採用：Firestore 慢時 await 會拖住回覆 → 加 `asyncio.wait_for(..., timeout=3)`
- 採用：新測試前多兩行空行 → 已刪
- 不採用：log 裡的 LINE user ID — 跟其他地方的 log 慣例一致，要改就一次改全部
- 不採用：非 ASCII 的 secret 會永遠比對失敗 — secret 是 ASCII，就算不是也是拒絕請求，不會誤放行
- 不採用：測試 mock 可能 await 不到 — 實際跑過有通過

# clarification_needed 截斷移到 model 層（2026-10-17）

- [x] agy review 指出：低信心路徑 `message.py` 直接回 `clarification_needed`，沒經過 80 字截斷
- [x] 截斷改在 `CalendarIntent` 的 `field_validator`，`_with_assumption_note` 不再自己截
- [x] 測試：低信心路徑的截斷（75 passed）
- [x] 部署（revision 00058，health check 200）

## Review（agy，gemini-4-argon-high）

- 採用：AGENTS.md 還寫舊的 `_with_assumption_note` 截斷 → 已更新
- 不採用：validator 移到 `CalendarIntentPayload` — Payload 只在 `nlp.py` 當 response_schema 用，解析後立刻轉成 `CalendarIntent`，沒有別的讀取端
