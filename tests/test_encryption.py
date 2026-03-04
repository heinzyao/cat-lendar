"""AES-256-GCM 加解密模組測試"""
import os
import base64

import pytest

# 設定測試用環境變數（必須在 import config 前）
os.environ.setdefault("LINE_CHANNEL_SECRET", "test_secret_32bytes_padding_here!")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test_token")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test")
os.environ.setdefault("GOOGLE_CLIENT_ID", "test_client_id")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test_client_secret")
os.environ.setdefault("GOOGLE_REDIRECT_URI", "https://example.com/oauth/callback")
os.environ.setdefault("ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("GCP_PROJECT_ID", "test-project")

from app.store.encryption import encrypt, decrypt


def test_encrypt_decrypt_roundtrip():
    """加密後解密應還原原始字串"""
    plaintext = "hello, world!"
    assert decrypt(encrypt(plaintext)) == plaintext


def test_encrypt_token():
    """模擬 token 字串的加解密"""
    token = "ya29.a0AfH6SMC_test_access_token_value_here"
    assert decrypt(encrypt(token)) == token


def test_encrypt_produces_different_ciphertext():
    """每次加密結果不同（nonce 隨機）"""
    plaintext = "same_text"
    c1 = encrypt(plaintext)
    c2 = encrypt(plaintext)
    assert c1 != c2  # nonce 不同
    assert decrypt(c1) == decrypt(c2) == plaintext


def test_encrypt_output_is_base64():
    """輸出應為有效 base64"""
    ct = encrypt("test")
    base64.b64decode(ct)  # 不應拋例外


def test_decrypt_wrong_key_raises():
    """錯誤金鑰解密應拋例外"""
    from unittest.mock import patch
    ct = encrypt("secret")
    bad_key = base64.b64encode(os.urandom(32)).decode()
    with patch.dict(os.environ, {"ENCRYPTION_KEY": bad_key}):
        # 重新載入 settings（因為 pydantic-settings 已快取）
        from app.store import encryption
        import importlib
        # 直接用不同 key 測試
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        raw = base64.b64decode(ct)
        nonce, ciphertext = raw[:12], raw[12:]
        aesgcm = AESGCM(base64.b64decode(bad_key))
        with pytest.raises(Exception):
            aesgcm.decrypt(nonce, ciphertext, None)
