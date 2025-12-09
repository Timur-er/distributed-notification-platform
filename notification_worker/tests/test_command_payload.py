from datetime import datetime

from app.consumer import CommandPayload


def test_command_payload_parses_dates() -> None:
    ts = datetime.utcnow()
    payload = CommandPayload(
        notification_id=1,
        user_id=1,
        channel="email",
        message="hello",
        event_id="evt-1",
        command_ts=ts,
        email="a@b.com",
        whatsapp=None,
        ws_id=None,
    )
    assert payload.command_ts == ts
