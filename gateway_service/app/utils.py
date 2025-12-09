import json
import logging
from typing import Any, Dict


def setup_logging() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [gateway] %(name)s: %(message)s",
    )
    return logging.getLogger("gateway")


def to_message(payload: Dict[str, Any]) -> bytes:
    return json.dumps(payload, default=str).encode()
