"""
App owner 執行腳本：取得 Google OAuth refresh token 並自動更新 Secret Manager。

使用方式：
    uv run python scripts/get_token.py

執行後會開啟瀏覽器進行 Google 授權，完成後自動將 refresh token
寫入 Secret Manager，無需手動複製貼上或重新部署。
"""

import os
import subprocess
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def _get_project_id() -> str:
    project_id = os.environ.get("GCP_PROJECT_ID", "")
    if project_id:
        return project_id
    try:
        return subprocess.check_output(
            ["gcloud", "config", "get-value", "project"], text=True
        ).strip()
    except Exception:
        return ""


def _read_secret(project_id: str, name: str) -> str:
    from google.cloud import secretmanager
    client = secretmanager.SecretManagerServiceClient()
    resp = client.access_secret_version(
        request={"name": f"projects/{project_id}/secrets/{name}/versions/latest"}
    )
    return resp.payload.data.decode("utf-8")


def _update_secret(project_id: str, token: str) -> bool:
    try:
        result = subprocess.run(
            ["gcloud", "secrets", "versions", "add", "GOOGLE_REFRESH_TOKEN",
             "--data-file=-", f"--project={project_id}"],
            input=token.encode("utf-8"),
            capture_output=True,
        )
        return result.returncode == 0
    except Exception as e:
        print(f"⚠️  Secret Manager 更新失敗：{e}")
        return False


def main():
    project_id = _get_project_id()

    client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")

    if not client_id or not client_secret:
        if not project_id:
            print("錯誤：請先設定 GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET 或 GCP_PROJECT_ID 環境變數")
            sys.exit(1)
        try:
            client_id = _read_secret(project_id, "GOOGLE_CLIENT_ID")
            client_secret = _read_secret(project_id, "GOOGLE_CLIENT_SECRET")
            print("✅ 已從 Secret Manager 讀取憑證")
        except Exception as e:
            print(f"錯誤：無法讀取 Secret Manager 憑證：{e}")
            sys.exit(1)

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)
    credentials = flow.run_local_server(port=0, prompt="consent", access_type="offline")

    print("\n" + "=" * 60)
    print("授權成功！")
    print("=" * 60)

    if project_id and credentials.refresh_token:
        print(f"\n正在更新 Secret Manager（{project_id}）...")
        if _update_secret(project_id, credentials.refresh_token):
            print("✅ GOOGLE_REFRESH_TOKEN 已更新至 Secret Manager")
            print("   新 token 下次 Cloud Run 收到請求時即生效（無需重新部署）")
        else:
            print(f"\n請手動執行：")
            print(f"  ./scripts/update_secret.sh GOOGLE_REFRESH_TOKEN")
    else:
        print(f"\n請手動設定 GOOGLE_REFRESH_TOKEN 並執行：")
        print(f"  ./scripts/update_secret.sh GOOGLE_REFRESH_TOKEN")


if __name__ == "__main__":
    main()
