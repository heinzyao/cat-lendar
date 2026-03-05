import logging

from fastapi import FastAPI

from app.routes.notify import router as notify_router
from app.routes.webhook import router as webhook_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

app = FastAPI(title="LINE Calendar Bot", docs_url=None, redoc_url=None)
app.include_router(webhook_router)
app.include_router(notify_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
