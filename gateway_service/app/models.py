from datetime import datetime
from typing import Any, Dict, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, validator


class EventPayload(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    event_type: str
    user_id: int
    timestamp: datetime
    data: Dict[str, Any]

    @validator("event_type")
    def validate_event_type(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("event_type must not be empty")
        return value


class WsNotification(BaseModel):
    user_id: int
    channel: str
    message: str
    notification_id: Optional[int] = None
