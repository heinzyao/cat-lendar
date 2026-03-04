from __future__ import annotations

import logging

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

from app.services.auth import handle_oauth_callback
from app.services.line_messaging import push_text
from app.utils import i18n

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/oauth/callback")
async def oauth_callback(
    code: str = Query(...),
    state: str = Query(...),
):
    line_user_id, error = await handle_oauth_callback(code, state)

    if error == "state_expired":
        return HTMLResponse(
            content=_html_page("授權失敗", i18n.AUTH_STATE_EXPIRED),
            status_code=400,
        )

    if error:
        if line_user_id:
            await push_text(line_user_id, i18n.AUTH_FAILED)
        return HTMLResponse(
            content=_html_page("授權失敗", i18n.AUTH_FAILED),
            status_code=400,
        )

    # OAuth callback 時 reply_token 已過期，用 push message 通知
    await push_text(line_user_id, i18n.AUTH_SUCCESS)

    return HTMLResponse(
        content=_html_page("授權成功", "Google 日曆授權成功！你可以關閉此頁面，回到 LINE 使用。"),
    )


def _html_page(title: str, message: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, sans-serif;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            margin: 0;
            background: #f5f5f5;
        }}
        .card {{
            background: white;
            padding: 2rem;
            border-radius: 12px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            text-align: center;
            max-width: 400px;
        }}
        h1 {{ color: #333; font-size: 1.5rem; }}
        p {{ color: #666; line-height: 1.6; }}
    </style>
</head>
<body>
    <div class="card">
        <h1>{title}</h1>
        <p>{message}</p>
    </div>
</body>
</html>"""
