"""應用程式設定模組：使用 pydantic-settings 從環境變數自動載入所有憑證與參數。

設計理由——使用 pydantic-settings 而非 os.getenv()？
- 自動型別轉換（str → int，str → bool 等）
- 必填欄位（無預設值）若未設定會在啟動時立即報錯，而非在執行時才發現
- 支援 .env 檔案，本機開發無需手動 export 環境變數
- extra="ignore"：忽略 .env 中未定義的欄位，避免因多餘設定導致啟動失敗

各欄位設計說明
--------------
- google_service_account_json：Service Account 金鑰 JSON（完整 JSON 字串）
  （Shared Calendar 架構：所有 LINE 用戶共用同一 Service Account 的日曆存取權）
- encryption_key：Fernet 對稱加密金鑰，base64 編碼的 32 bytes
  用於加密存入 Firestore 的敏感資料
- notify_secret：Cloud Scheduler 呼叫 /notify 端點時的身份驗證 token
  防止任意人觸發提醒推播
- user_state_ttl_seconds（300 秒）：選擇行程的等待逾時
  5 分鐘內使用者未選擇則自動清除，避免殘留的狀態影響後續操作
- conversation_history_ttl_seconds（1800 秒）：對話記憶有效期
  30 分鐘無操作後清除，避免舊對話影響新指令的解析（避免「錯誤的上下文」）
- max_conversation_turns（10 輪）：最多保留幾輪對話歷史傳給 Gemini
  保留更多可提升上下文理解，但也增加 API token 消耗與延遲

Singleton 設計：
settings 在模組載入時建立一次，所有模組 import 同一個物件
"""
from typing import ClassVar, Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # LINE Bot 憑證
    line_channel_secret: str = ""
    line_channel_access_token: str = ""

    # Gemini API
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"  # 用於 NLP 意圖解析的模型版本

    # Google Service Account 憑證（Shared Calendar 架構）
    google_service_account_json: str = ""   # Service Account JSON 金鑰（完整 JSON 字串）
    google_calendar_id: str = ""

    # 加密（Fernet 對稱加密）
    encryption_key: str = ""

    # GCP 設定
    gcp_project_id: str = ""  # Firestore 所在的 GCP 專案 ID（空字串時使用 ADC 預設）

    # 通知設定
    notify_secret: str = ""               # /notify 端點的身份驗證 token
    default_reminder_minutes: int = 15    # 系統預設提醒分鐘數（使用者可覆蓋）

    # 應用程式參數
    timezone: str = "Asia/Taipei"
    user_state_ttl_seconds: int = 300     # 多筆行程選擇的等待逾時（5 分鐘）
    conversation_history_ttl_seconds: int = 1800  # 對話記憶有效期（30 分鐘）
    max_conversation_turns: int = 10      # 傳給 Gemini 的最大對話輪次

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    @model_validator(mode="after")
    def require_env_values(self) -> Self:
        missing = [
            name
            for name, value in (
                ("LINE_CHANNEL_SECRET", self.line_channel_secret),
                ("LINE_CHANNEL_ACCESS_TOKEN", self.line_channel_access_token),
                ("GEMINI_API_KEY", self.gemini_api_key),
                ("GOOGLE_CALENDAR_ID", self.google_calendar_id),
                ("ENCRYPTION_KEY", self.encryption_key),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"Missing required settings: {', '.join(missing)}")
        return self


# Singleton：全域共享同一個 Settings 實例
settings = Settings()
