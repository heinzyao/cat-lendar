from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class ActionType(str, Enum):
    CREATE = "create"
    QUERY = "query"
    UPDATE = "update"
    DELETE = "delete"
    UNKNOWN = "unknown"


class EventDetails(BaseModel):
    summary: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    location: str | None = None
    description: str | None = None
    all_day: bool = False


class TimeRange(BaseModel):
    start: datetime
    end: datetime


class CalendarIntent(BaseModel):
    action: ActionType
    event_details: EventDetails | None = None
    time_range: TimeRange | None = None
    search_keyword: str | None = Field(
        default=None, description="用於查詢/修改/刪除時的關鍵字"
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    clarification_needed: str | None = Field(
        default=None, description="需要使用者澄清時的提示訊息"
    )
