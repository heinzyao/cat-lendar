import base64
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_settings_requires_google_calendar_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GOOGLE_CALENDAR_ID", raising=False)

    with pytest.raises(ValidationError):
        _ = Settings(
            line_channel_secret="test_secret",
            line_channel_access_token="test_token",
            gemini_api_key="test-key",
            encryption_key=base64.b64encode(os.urandom(32)).decode(),
            gcp_project_id="test-project",
        )
