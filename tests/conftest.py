import base64
import os

_ = os.environ.setdefault("LINE_CHANNEL_SECRET", "test_channel_secret")
_ = os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test_token")
_ = os.environ.setdefault("GEMINI_API_KEY", "test-key")
_ = os.environ.setdefault("GOOGLE_CALENDAR_ID", "test-calendar@example.com")
_ = os.environ.setdefault("ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
_ = os.environ.setdefault("GCP_PROJECT_ID", "test-project")
