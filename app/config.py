"""應用程式設定：pydantic-settings 從環境變數／.env 載入；必填值缺了啟動即失敗。"""
from typing import ClassVar, Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # LINE Bot 憑證
    line_channel_secret: str = ""
    line_channel_access_token: str = ""

    # Gemini API
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"  # 用於 NLP 意圖解析的模型版本

    # Google Service Account 憑證（Shared Calendar 架構）
    google_service_account_json: str = ""   # Service Account JSON 金鑰（完整 JSON 字串）
    google_calendar_id: str = ""

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
            )
            if not value
        ]
        if missing:
            raise ValueError(f"Missing required settings: {', '.join(missing)}")
        return self


# Singleton：全域共享同一個 Settings 實例
settings = Settings()
