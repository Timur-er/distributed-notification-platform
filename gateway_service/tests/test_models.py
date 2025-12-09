from datetime import datetime

import pytest

from app.models import EventPayload


def test_event_payload_generates_id() -> None:
    payload = EventPayload(
        event_type="ping",
        user_id=1,
        timestamp=datetime.utcnow(),
        data={"message": "hello"},
    )
    assert payload.event_id


def test_event_type_validation() -> None:
    with pytest.raises(ValueError):
        EventPayload(
            event_type=" ",
            user_id=1,
            timestamp=datetime.utcnow(),
            data={},
        )
