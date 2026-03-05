from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # LINE Bot
    line_channel_secret: str
    line_channel_access_token: str

    # Claude API
    anthropic_api_key: str
    claude_model: str = "claude-sonnet-4-20250514"

    # Google OAuth
    google_client_id: str
    google_client_secret: str
    google_redirect_uri: str

    # Encryption
    encryption_key: str  # base64-encoded 32-byte key

    # GCP
    gcp_project_id: str = ""

    # App
    timezone: str = "Asia/Taipei"
    oauth_state_ttl_seconds: int = 600  # 10 minutes
    user_state_ttl_seconds: int = 300  # 5 minutes
    conversation_history_ttl_seconds: int = 1800  # 30 minutes
    max_conversation_turns: int = 10

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
