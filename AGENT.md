# AGENT.md — 多代理協作指引

本文件為 AI Agent 在此專案工作時的指引，包含專案地圖、角色分工與協作規範。
支援在 **Claude Code**、**OpenCode**、**Antigravity** 之間交接工作。

---

## 專案快照

| 項目 | 值 |
|------|-----|
| 專案 | Cat-Lendar |
| 語言 | Python 3.12（Docker）／Python 3.14（本地開發） |
| 套件管理 | uv |
| 部署 | Google Cloud Run (`asia-east1`) |
| 最新 Revision | `line-calendar-bot-00010-wqp` |

---

## 關鍵檔案地圖

```
app/
├── config.py              # 所有環境變數（pydantic-settings）
├── main.py                # FastAPI 入口，路由掛載
├── handlers/message.py    # ★ 核心協調器：接收訊息 → NLP → 執行
├── services/
│   ├── nlp.py             # ★ Claude API 意圖解析（parse_intent + parse_update_details 二次解析）
│   ├── auth.py            # ★ Google OAuth + PKCE + token refresh
│   ├── calendar.py        # Google Calendar CRUD
│   ├── local_calendar.py  # Firestore 內建行事曆 CRUD
│   └── line_messaging.py  # LINE reply / push
├── models/
│   ├── intent.py          # CalendarIntent（action, event_details, time_range, original_message…）
│   └── user.py            # UserToken, OAuthState, UserState
├── store/
│   ├── firestore.py       # Firestore CRUD（users / user_prefs / local_events / states）
│   └── encryption.py      # AES-256-GCM 加解密
└── utils/
    ├── datetime_utils.py  # 時區、格式化（Asia/Taipei）
    └── i18n.py            # 繁體中文訊息模板
scripts/
├── deploy.sh              # 建置 + 推送 + 部署到 Cloud Run
├── dev.sh                 # 本地開發（uvicorn + ngrok）
├── setup_gcp.sh           # 一次性 GCP 基礎建設
└── update_secret.sh       # 更新 Secret Manager 密鑰
tests/                     # pytest，51 個測試，asyncio_mode=auto
```

---

## Firestore 結構

```
users/{line_user_id}
  encrypted_access_token, encrypted_refresh_token, token_expiry, scopes

user_prefs/{line_user_id}
  calendar_mode: "google" | "local", updated_at

local_events/{line_user_id}/events/{event_id}
  summary, start_time, end_time, location, description, all_day
  created_at, updated_at

oauth_states/{state_token}          ← TTL 10 分鐘
  line_user_id, expires_at, code_verifier

user_states/{line_user_id}          ← TTL 5 分鐘
  action, candidates, original_intent, expires_at
```

### UserState.action 合法值

| action | 情境 |
|--------|------|
| `choose_calendar_mode` | 新使用者選擇模式 |
| `switch_calendar_choice` | 切換行事曆目標選擇 |
| `confirm_migration` | 確認是否遷移行程 |
| `pending_local_to_google_migration` | OAuth 完成後執行 L→G 遷移 |
| `select_event_for_update` | 多筆事件更新選擇 |
| `select_event_for_delete` | 多筆事件刪除選擇 |

---

## 已知限制與陷阱

| 問題 | 說明 |
|------|------|
| PKCE 必要 | Google 強制要求 PKCE，`auth.py` 已實作 |
| redirect_uri 格式 | `deploy.sh` 從 project number 計算穩定 URL，避免 `*.a.run.app` 格式不符 |
| Python 3.14 警告 | `line-bot-sdk` 使用 Pydantic V1，Docker image 固定使用 3.12 無此問題 |
| OAuth Testing 狀態 | GCP OAuth consent screen 處於 Testing，只有 test users 可授權 |
| Firestore TTL | `oauth_states` 10 分鐘、`user_states` 5 分鐘，需在 GCP 設定 TTL policy |

---

## 代理角色與工具分配

### Claude Code
**適合工作**：完整功能開發、跨多檔案重構、測試撰寫
**進入條件**：閱讀 `handlers/message.py`（核心流程）與 `store/firestore.py`（資料結構）
**完成條件**：`uv run python -m pytest tests/ -q` 全部通過

### OpenCode
**適合工作**：單一模組修改、Bug 修復、程式碼審查
**進入條件**：閱讀目標模組及其直接依賴
**完成條件**：修改最小化，測試通過

### Antigravity
**適合工作**：探索性分析、文件撰寫、架構建議
**進入條件**：閱讀本 AGENT.md 與 README.md 取得全貌
**完成條件**：產出可直接使用的文件或清楚的實作建議

### 共通規則
- 測試不得連線外部服務（Firestore / Claude / LINE）—— 一律 mock
- 外部依賴使用 `unittest.mock.AsyncMock`
- 環境變數使用 `os.environ.setdefault` 注入

---

## 協作交接規範

當一個 agent 需要將工作交給另一個 agent 時，在 commit message 或 PR description 說明：

```
交接事項：
- 已完成：<已做的事>
- 待完成：<下一步要做的事>
- 注意：<需要特別留意的邊界條件或陷阱>
```

---

## 修改流程

```
1. 閱讀相關原始碼（勿假設）
2. 最小化修改範圍
3. 執行 uv run python -m pytest tests/ -q → 全部通過
4. git commit（Conventional Commits 格式）
5. 部署：source ~/Project/.env && bash scripts/deploy.sh
6. 更新 AGENT.md 的「最新 Revision」
```

## Commit 格式

```
<type>: <description>

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
```

| type | 用途 |
|------|------|
| `feat` | 新功能 |
| `fix` | Bug 修復 |
| `test` | 測試新增/修改 |
| `docs` | 文件更新 |
| `refactor` | 重構（不改行為） |
| `chore` | 維護性工作 |

---

## 環境變數

所有密鑰存放於 **GCP Secret Manager**，本地開發從 `~/Project/.env` 讀取。
**不要在任何文件中記錄密鑰明文。**

| 變數 | 來源 |
|------|------|
| `LINE_CHANNEL_SECRET` | Secret Manager |
| `LINE_CHANNEL_ACCESS_TOKEN` | Secret Manager |
| `ANTHROPIC_API_KEY` | Secret Manager / `~/Project/.env` |
| `GOOGLE_CLIENT_ID` | Secret Manager / `~/Project/.env` |
| `GOOGLE_CLIENT_SECRET` | Secret Manager / `~/Project/.env` |
| `ENCRYPTION_KEY` | Secret Manager |
| `GCP_PROJECT_ID` | `deploy.sh` 自動讀取 gcloud config |

---

## 已完成功能

- [x] Google Calendar 整合（OAuth + PKCE）
- [x] Firestore 內建行事曆（local mode）
- [x] 行事曆雙模式切換與資料遷移
- [x] 多筆事件選擇流程（update / delete）
- [x] NLP 二次解析（parse_update_details）：相對時間更新保持持續時間

---

## 擴充方向（待辦）

- [ ] 支援 recurring events（每週定期行程）
- [ ] 多 Google 帳號切換
- [ ] 提醒功能（透過 LINE push message）
- [ ] 群組 bot 支援
- [ ] 使用量統計（每日 Claude API 呼叫次數）
