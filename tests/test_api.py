"""FastAPI 路由整合測試（不連線外部服務）"""
import os
import base64
import hashlib
import hmac
import json

import pytest
from httpx import AsyncClient, ASGITransport

os.environ.setdefault("LINE_CHANNEL_SECRET", "test_channel_secret")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test_token")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test")
os.environ.setdefault("GOOGLE_CLIENT_ID", "test_client_id")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test_client_secret")
os.environ.setdefault("GOOGLE_REFRESH_TOKEN", "test_refresh_token")
os.environ.setdefault("ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("GCP_PROJECT_ID", "test-project")

from app.main import app


def _line_signature(body: bytes, secret: str) -> str:
    return base64.b64encode(
        hmac.new(secret.encode(), body, hashlib.sha256).digest()
    ).decode()


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_webhook_invalid_signature(client):
    body = json.dumps({"destination": "U123", "events": []}).encode()
    resp = await client.post(
        "/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "x-line-signature": "invalid_signature",
        },
    )
    assert resp.status_code == 400
    assert "Invalid signature" in resp.text


async def test_webhook_valid_signature_empty_events(client):
    """有效 signature + 空 events → 200（LINE 驗活）"""
    body = json.dumps({"destination": "U123", "events": []}).encode()
    sig = _line_signature(body, "test_channel_secret")
    resp = await client.post(
        "/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "x-line-signature": sig,
        },
    )
    assert resp.status_code == 200
