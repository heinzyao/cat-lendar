"""
App owner 一次性執行腳本，取得 Google OAuth refresh token。

使用方式：
    uv run python scripts/get_token.py

完成後將輸出的 refresh token 設定到環境變數 GOOGLE_REFRESH_TOKEN。
"""

import os
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def main():
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")

    if not client_id or not client_secret:
        print("錯誤：請先設定 GOOGLE_CLIENT_ID 和 GOOGLE_CLIENT_SECRET 環境變數")
        sys.exit(1)

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)
    credentials = flow.run_local_server(port=0, prompt="consent", access_type="offline")

    print("\n" + "=" * 60)
    print("授權成功！請將以下 refresh token 設定到環境變數：")
    print("=" * 60)
    print(f"\nGOOGLE_REFRESH_TOKEN={credentials.refresh_token}\n")


if __name__ == "__main__":
    main()
