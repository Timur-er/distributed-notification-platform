import pytest

from app.orchestrator import SagaOrchestrator


def test_prefers_requested_channel_when_available() -> None:
    orch = SagaOrchestrator()
    user = {"email": "a@b.com", "whatsapp": "+1", "ws_id": "user-1"}
    channel = orch._select_channel(user, {"preferred_channel": "email"})
    assert channel == "email"


def test_fallback_to_available_channel() -> None:
    orch = SagaOrchestrator()
    user = {"email": None, "whatsapp": "+1", "ws_id": "ws"}
    channel = orch._select_channel(user, {})
    assert channel in {"websocket", "whatsapp"}


def test_raises_when_no_channels() -> None:
    orch = SagaOrchestrator()
    user = {"email": None, "whatsapp": None, "ws_id": None}
    with pytest.raises(ValueError):
        orch._select_channel(user, {})
