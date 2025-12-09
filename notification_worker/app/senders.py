import asyncio
import logging
import os
from email.message import EmailMessage

import aiosmtplib

from .error_handler import DeliveryError

logger = logging.getLogger("notification-worker")

FAKE_DELIVERY = os.getenv("FAKE_DELIVERY", "true").lower() == "true"
SMTP_HOST = os.getenv("SMTP_HOST", "mailhog")
SMTP_PORT = int(os.getenv("SMTP_PORT", "1025"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", "notifications@example.com")


async def send_email(recipient: str, message: str) -> None:
    if not recipient:
        raise DeliveryError("Email is missing for recipient")

    if FAKE_DELIVERY:
        await asyncio.sleep(0.1)
        logger.info("[FAKE] Email to %s: %s", recipient, message)
        return

    email = EmailMessage()
    email["From"] = EMAIL_FROM
    email["To"] = recipient
    email["Subject"] = "Notification"
    email.set_content(message)

    try:
        await aiosmtplib.send(
            email,
            hostname=SMTP_HOST,
            port=SMTP_PORT,
            username=SMTP_USERNAME or None,
            password=SMTP_PASSWORD or None,
        )
    except Exception as exc:  # noqa: BLE001
        raise DeliveryError(f"Email send failed: {exc}") from exc


async def send_whatsapp(phone: str, message: str) -> None:
    if not phone:
        raise DeliveryError("WhatsApp number is missing for recipient")

    if FAKE_DELIVERY:
        await asyncio.sleep(0.1)
        logger.info("[FAKE] WhatsApp to %s: %s", phone, message)
        return

    # Placeholder for real provider integration
    raise DeliveryError("Real WhatsApp provider is not configured")
