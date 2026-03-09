"""應用程式設定模組：使用 pydantic-settings 從環境變數自動載入所有憑證與參數。

設計理由——使用 pydantic-settings 而非 os.getenv()？
- 自動型別轉換（str → int，str → bool 等）
- 必填欄位（無預設值）若未設定會在啟動時立即報錯，而非在執行時才發現
- 支援 .env 檔案，本機開發無需手動 export 環境變數
- extra="ignore"：忽略 .env 中未定義的欄位，避免因多餘設定導致啟動失敗

各欄位設計說明
--------------
- google_refresh_token：App Owner 的 Google OAuth refresh token
  （Shared Calendar 架構：所有 LINE 用戶共用同一個 Google 帳號的日曆）
- encryption_key：Fernet 對稱加密金鑰，base64 編碼的 32 bytes
  用於加密存入 Firestore 的敏感資料（如 OAuth token）
- notify_secret：Cloud Scheduler 呼叫 /notify 端點時的身份驗證 token
  防止任意人觸發提醒推播
- user_state_ttl_seconds（300 秒）：選擇行程的等待逾時
  5 分鐘內使用者未選擇則自動清除，避免殘留的狀態影響後續操作
- conversation_history_ttl_seconds（1800 秒）：對話記憶有效期
  30 分鐘無操作後清除，避免舊對話影響新指令的解析（避免「錯誤的上下文」）
- max_conversation_turns（10 輪）：最多保留幾輪對話歷史傳給 Claude
  保留更多可提升上下文理解，但也增加 API token 消耗與延遲

Singleton 設計：
settings 在模組載入時建立一次，所有模組 import 同一個物件
"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # LINE Bot 憑證
    line_channel_secret: str        # HMAC-SHA256 簽名驗證用
    line_channel_access_token: str  # Reply API / Push API 授權 token

    # Claude API
    anthropic_api_key: str
    claude_model: str = "claude-sonnet-4-20250514"  # 用於 NLP 意圖解析的模型版本

    # Google OAuth 憑證（Shared Calendar 架構）
    google_client_id: str
    google_client_secret: str
    google_refresh_token: str = ""          # App Owner 的長效 refresh token
    google_calendar_id: str = "primary"     # 目標日曆 ID（"primary" 代表預設日曆）

    # 加密（Fernet 對稱加密）
    encryption_key: str  # base64-encoded 32-byte key，使用 cryptography.fernet.Fernet.generate_key() 生成

    # GCP 設定
    gcp_project_id: str = ""  # Firestore 所在的 GCP 專案 ID（空字串時使用 ADC 預設）

    # 通知設定
    notify_secret: str = ""               # /notify 端點的身份驗證 token
    default_reminder_minutes: int = 15    # 系統預設提醒分鐘數（使用者可覆蓋）

    # 應用程式參數
    timezone: str = "Asia/Taipei"
    user_state_ttl_seconds: int = 300     # 多筆行程選擇的等待逾時（5 分鐘）
    conversation_history_ttl_seconds: int = 1800  # 對話記憶有效期（30 分鐘）
    max_conversation_turns: int = 10      # 傳給 Claude 的最大對話輪次

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


# Singleton：全域共享同一個 Settings 實例
settings = Settings()
