import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional

import aio_pika
from pydantic import BaseModel
from prometheus_client import Counter, Histogram, start_http_server

from .error_handler import DeliveryError
from .senders import send_email, send_whatsapp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [worker] %(name)s: %(message)s",
)
logger = logging.getLogger("notification-worker")


class CommandPayload(BaseModel):
    notification_id: int
    user_id: int
    channel: str
    message: str
    event_id: str
    command_ts: datetime
    email: Optional[str]
    whatsapp: Optional[str]
    ws_id: Optional[str]


commands_total = Counter("worker_commands_total", "Commands consumed", ["channel"])
delivery_success = Counter("worker_delivery_success_total", "Successful deliveries", ["channel"])
delivery_failed = Counter("worker_delivery_failed_total", "Failed deliveries", ["channel"])
delivery_latency = Histogram(
    "worker_delivery_seconds", "Notification send duration", buckets=(0.05, 0.1, 0.2, 0.5, 1, 2, 5)
)


class NotificationWorker:
    def __init__(self) -> None:
        self.broker_url = os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")
        self.retry_limit = int(os.getenv("WORKER_RETRY_LIMIT", "3"))
        self.retry_delay_ms = int(os.getenv("WORKER_RETRY_DELAY_MS", "5000"))

        self.connection: aio_pika.RobustConnection | None = None
        self.channel: aio_pika.abc.AbstractChannel | None = None
        self.queue: aio_pika.abc.AbstractQueue | None = None
        self.saga_exchange: aio_pika.abc.AbstractExchange | None = None
        self.ws_exchange: aio_pika.abc.AbstractExchange | None = None

    async def setup(self) -> None:
        self.connection = await aio_pika.connect_robust(self.broker_url)
        self.channel = await self.connection.channel()
        await self.channel.set_qos(prefetch_count=10)

        self.saga_exchange = await self.channel.declare_exchange(
            "saga", aio_pika.ExchangeType.TOPIC, durable=True
        )
        self.ws_exchange = await self.channel.declare_exchange("ws", aio_pika.ExchangeType.TOPIC, durable=True)

        await self._declare_topology()
        logger.info("Notification worker connected to broker")

    async def _declare_topology(self) -> None:
        assert self.channel is not None
        commands_args = {
            "x-dead-letter-exchange": "saga",
            "x-dead-letter-routing-key": "saga.commands.retry",
        }
        retry_args = {
            "x-message-ttl": self.retry_delay_ms,
            "x-dead-letter-exchange": "saga",
            "x-dead-letter-routing-key": "saga.commands",
        }
        self.queue = await self.channel.declare_queue("saga.commands", durable=True, arguments=commands_args)
        commands_retry = await self.channel.declare_queue("saga.commands.retry", durable=True, arguments=retry_args)
        saga_events = await self.channel.declare_queue("saga.events", durable=True)
        await self.queue.bind(self.saga_exchange, routing_key="saga.commands")
        await commands_retry.bind(self.saga_exchange, routing_key="saga.commands.retry")
        await saga_events.bind(self.saga_exchange, routing_key="saga.events")

    async def run(self) -> None:
        if not self.queue:
            raise RuntimeError("Queue not ready")
        async with self.queue.iterator() as queue_iter:
            async for message in queue_iter:
                async with message.process():
                    await self._handle_message(message)

    async def _handle_message(self, message: aio_pika.abc.AbstractIncomingMessage) -> None:
        attempts = int(message.headers.get("x-attempts", 0))
        try:
            payload = json.loads(message.body.decode())
            command = CommandPayload(**payload)
        except Exception as exc:  # noqa: BLE001
            logger.error("Invalid message %s: %s", message.body, exc)
            return

        commands_total.labels(channel=command.channel).inc()
        started = datetime.utcnow()
        try:
            await self._dispatch(command)
        except Exception as exc:  # noqa: BLE001
            await self._handle_failure(message, command, attempts, exc)
            return

        delivery_success.labels(channel=command.channel).inc()
        delivery_latency.observe((datetime.utcnow() - started).total_seconds())
        await self._publish_result(command, status="sent", error=None)

    async def _dispatch(self, command: CommandPayload) -> None:
        if command.channel == "email":
            await send_email(command.email or "", command.message)
        elif command.channel == "whatsapp":
            await send_whatsapp(command.whatsapp or "", command.message)
        elif command.channel == "websocket":
            await self._publish_websocket(command)
        else:
            raise DeliveryError(f"Unsupported channel {command.channel}")

    async def _publish_websocket(self, command: CommandPayload) -> None:
        assert self.ws_exchange is not None
        payload = {"user_id": command.user_id, "channel": "websocket", "message": command.message}
        message = aio_pika.Message(body=json.dumps(payload).encode(), delivery_mode=aio_pika.DeliveryMode.PERSISTENT)
        await self.ws_exchange.publish(message, routing_key="ws.notifications")

    async def _handle_failure(
        self,
        message: aio_pika.abc.AbstractIncomingMessage,
        command: CommandPayload,
        attempts: int,
        exc: Exception,
    ) -> None:
        delivery_failed.labels(channel=command.channel).inc()
        logger.error("Delivery failed for notification %s: %s", command.notification_id, exc)
        if attempts + 1 >= self.retry_limit:
            await self._publish_result(command, status="failed", error=str(exc))
        else:
            await self._schedule_retry(message, attempts + 1)

    async def _schedule_retry(self, message: aio_pika.abc.AbstractIncomingMessage, attempts: int) -> None:
        assert self.saga_exchange is not None
        headers = dict(message.headers or {})
        headers["x-attempts"] = attempts
        retry_msg = aio_pika.Message(
            body=message.body,
            headers=headers,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        )
        await self.saga_exchange.publish(retry_msg, routing_key="saga.commands.retry")

    async def _publish_result(self, command: CommandPayload, status: str, error: Optional[str]) -> None:
        assert self.saga_exchange is not None
        payload = {
            "notification_id": command.notification_id,
            "status": status,
            "error": error,
            "channel": command.channel,
            "command_ts": command.command_ts.isoformat(),
        }
        message = aio_pika.Message(body=json.dumps(payload).encode(), delivery_mode=aio_pika.DeliveryMode.PERSISTENT)
        await self.saga_exchange.publish(message, routing_key="saga.events")


async def main() -> None:
    worker = NotificationWorker()
    await worker.setup()
    metrics_port = int(os.getenv("METRICS_PORT", "9102"))
    start_http_server(metrics_port)
    logger.info("Worker metrics exposed on :%s/metrics", metrics_port)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
