import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Any, Dict
from uuid import uuid4

import aio_pika
import asyncpg
from pydantic import BaseModel
from prometheus_client import start_http_server

from .metrics import (
    dlq_events,
    saga_completed,
    saga_failed,
    saga_started,
    worker_roundtrip_seconds,
    worker_signals,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [orchestrator] %(name)s: %(message)s",
)
logger = logging.getLogger("saga-orchestrator")


class IncomingEvent(BaseModel):
    event_id: str
    event_type: str
    user_id: int
    timestamp: datetime
    data: Dict[str, Any]


class SagaCommand(BaseModel):
    notification_id: int
    user_id: int
    channel: str
    message: str
    event_id: str
    command_ts: datetime
    email: str | None = None
    whatsapp: str | None = None
    ws_id: str | None = None


class SagaOrchestrator:
    def __init__(self) -> None:
        self.broker_url = os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")
        self.db_url = os.getenv(
            "DATABASE_URL", "postgresql://postgres:postgres@postgres:5432/notifications"
        )
        self.retry_limit = int(os.getenv("RETRY_LIMIT", "3"))
        self.retry_delay_ms = int(os.getenv("RETRY_DELAY_MS", "5000"))

        self.connection: aio_pika.RobustConnection | None = None
        self.channel: aio_pika.abc.AbstractChannel | None = None
        self.events_exchange: aio_pika.abc.AbstractExchange | None = None
        self.saga_exchange: aio_pika.abc.AbstractExchange | None = None
        self.ws_exchange: aio_pika.abc.AbstractExchange | None = None
        self.events_queue: aio_pika.abc.AbstractQueue | None = None
        self.saga_events_queue: aio_pika.abc.AbstractQueue | None = None
        self.pool: asyncpg.Pool | None = None

    async def setup(self) -> None:
        self.connection = await aio_pika.connect_robust(self.broker_url)
        self.channel = await self.connection.channel()
        await self.channel.set_qos(prefetch_count=20)

        self.events_exchange = await self.channel.declare_exchange(
            "events", aio_pika.ExchangeType.TOPIC, durable=True
        )
        self.saga_exchange = await self.channel.declare_exchange(
            "saga", aio_pika.ExchangeType.TOPIC, durable=True
        )
        self.ws_exchange = await self.channel.declare_exchange("ws", aio_pika.ExchangeType.TOPIC, durable=True)

        await self._declare_topology()
        self.pool = await asyncpg.create_pool(self.db_url, min_size=1, max_size=5)
        await self._ensure_tables()
        logger.info("Saga orchestrator connected to broker and database")

    async def _declare_topology(self) -> None:
        assert self.channel is not None
        main_args = {
            "x-dead-letter-exchange": "events",
            "x-dead-letter-routing-key": "events.retry",
        }
        retry_args = {
            "x-message-ttl": self.retry_delay_ms,
            "x-dead-letter-exchange": "events",
            "x-dead-letter-routing-key": "events.main",
        }
        self.events_queue = await self.channel.declare_queue("events.main", durable=True, arguments=main_args)
        events_retry = await self.channel.declare_queue("events.retry", durable=True, arguments=retry_args)
        events_dlq = await self.channel.declare_queue("events.dlq", durable=True)
        await self.events_queue.bind(self.events_exchange, routing_key="events.main")
        await events_retry.bind(self.events_exchange, routing_key="events.retry")
        await events_dlq.bind(self.events_exchange, routing_key="events.dlq")

        commands_args = {
            "x-dead-letter-exchange": "saga",
            "x-dead-letter-routing-key": "saga.commands.retry",
        }
        retry_args = {
            "x-message-ttl": self.retry_delay_ms,
            "x-dead-letter-exchange": "saga",
            "x-dead-letter-routing-key": "saga.commands",
        }
        saga_commands = await self.channel.declare_queue("saga.commands", durable=True, arguments=commands_args)
        saga_commands_retry = await self.channel.declare_queue(
            "saga.commands.retry", durable=True, arguments=retry_args
        )
        saga_events = await self.channel.declare_queue("saga.events", durable=True)
        saga_compensations = await self.channel.declare_queue("saga.compensations", durable=True)
        await saga_commands.bind(self.saga_exchange, routing_key="saga.commands")
        await saga_commands_retry.bind(self.saga_exchange, routing_key="saga.commands.retry")
        await saga_events.bind(self.saga_exchange, routing_key="saga.events")
        await saga_compensations.bind(self.saga_exchange, routing_key="saga.compensations")
        self.saga_events_queue = saga_events

        ws_notifications = await self.channel.declare_queue("ws.notifications", durable=True)
        await ws_notifications.bind(self.ws_exchange, routing_key="ws.notifications")

    async def _ensure_tables(self) -> None:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    email TEXT,
                    whatsapp TEXT,
                    ws_id TEXT
                );
                CREATE TABLE IF NOT EXISTS notifications (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id),
                    channel TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error_message TEXT,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                );
                """
            )

    async def run(self) -> None:
        if not self.events_queue or not self.saga_events_queue:
            raise RuntimeError("Topology is not ready")
        await asyncio.gather(self.consume_events(), self.consume_results())

    async def consume_events(self) -> None:
        assert self.events_queue is not None
        async with self.events_queue.iterator() as queue_iter:
            async for message in queue_iter:
                async with message.process():
                    attempts = int(message.headers.get("x-attempts", 0))
                    try:
                        payload = json.loads(message.body.decode())
                        event = IncomingEvent(**payload)
                        await self.handle_event(event)
                    except Exception as exc:  # noqa: BLE001
                        logger.error("Failed to handle event %s: %s", message.body, exc)
                        await self._handle_failure(message, attempts, exc)

    async def consume_results(self) -> None:
        assert self.saga_events_queue is not None
        async with self.saga_events_queue.iterator() as queue_iter:
            async for message in queue_iter:
                async with message.process():
                    try:
                        payload = json.loads(message.body.decode())
                        await self._handle_worker_result(payload)
                    except Exception as exc:  # noqa: BLE001
                        logger.error("Failed to process worker result %s: %s", message.body, exc)

    async def handle_event(self, event: IncomingEvent) -> None:
        saga_started.inc()
        logger.info("Handling event %s for user %s", event.event_id, event.user_id)

        user = await self._get_user(event.user_id)
        if not user:
            raise ValueError(f"User {event.user_id} not found")

        channel = self._select_channel(user, event.data)
        notification_id = await self._create_notification(user["id"], channel)
        command = SagaCommand(
            notification_id=notification_id,
            user_id=user["id"],
            channel=channel,
            message=self._build_message(event),
            event_id=event.event_id,
            command_ts=datetime.utcnow(),
            email=user.get("email"),
            whatsapp=user.get("whatsapp"),
            ws_id=user.get("ws_id"),
        )
        await self._publish_command(command)

    async def _handle_failure(
        self, message: aio_pika.abc.AbstractIncomingMessage, attempts: int, exc: Exception
    ) -> None:
        if attempts + 1 >= self.retry_limit:
            await self._send_dlq(message, attempts + 1, exc)
        else:
            await self._schedule_retry(message, attempts + 1, exc)

    async def _schedule_retry(
        self, message: aio_pika.abc.AbstractIncomingMessage, attempts: int, exc: Exception
    ) -> None:
        assert self.events_exchange is not None
        logger.warning("Retrying message after failure (%s), attempt %s", exc, attempts)
        headers = dict(message.headers or {})
        headers["x-attempts"] = attempts
        retry_msg = aio_pika.Message(
            body=message.body,
            headers=headers,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        )
        await self.events_exchange.publish(retry_msg, routing_key="events.retry")

    async def _send_dlq(
        self, message: aio_pika.abc.AbstractIncomingMessage, attempts: int, exc: Exception
    ) -> None:
        assert self.events_exchange is not None
        logger.error("Sending message to DLQ after %s attempts: %s", attempts, exc)
        headers = dict(message.headers or {})
        headers.update({"x-attempts": attempts, "x-error": str(exc)})
        dlq_message = aio_pika.Message(
            body=message.body,
            headers=headers,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        )
        await self.events_exchange.publish(dlq_message, routing_key="events.dlq")
        dlq_events.inc()

    async def _publish_command(self, command: SagaCommand) -> None:
        assert self.saga_exchange is not None
        body = json.dumps(command.dict(), default=str).encode()
        message = aio_pika.Message(body=body, delivery_mode=aio_pika.DeliveryMode.PERSISTENT)
        await self.saga_exchange.publish(message, routing_key="saga.commands")
        logger.info(
            "Saga command dispatched (notification_id=%s, channel=%s)", command.notification_id, command.channel
        )

    async def _handle_worker_result(self, payload: Dict[str, Any]) -> None:
        notification_id = payload.get("notification_id")
        status = payload.get("status")
        error = payload.get("error")
        command_ts = payload.get("command_ts")

        if not notification_id:
            logger.warning("Received worker result without notification_id: %s", payload)
            return

        if status == "sent":
            saga_completed.inc()
            await self._update_notification(notification_id, "sent", None)
        else:
            saga_failed.inc()
            await self._update_notification(notification_id, "failed", error or "Unknown error")

        if command_ts:
            try:
                start_ts = datetime.fromisoformat(command_ts)
                duration = (datetime.utcnow() - start_ts).total_seconds()
                worker_roundtrip_seconds.observe(max(duration, 0))
            except Exception:
                pass

        worker_signals.labels(status=status or "unknown").inc()

    async def _get_user(self, user_id: int) -> Dict[str, Any] | None:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, name, email, whatsapp, ws_id FROM users WHERE id=$1 LIMIT 1", user_id
            )
            return dict(row) if row else None

    async def _create_notification(self, user_id: int, channel: str) -> int:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            record = await conn.fetchrow(
                "INSERT INTO notifications(user_id, channel, status) VALUES($1, $2, $3) RETURNING id",
                user_id,
                channel,
                "pending",
            )
            return int(record["id"])

    async def _update_notification(self, notification_id: int, status: str, error: str | None) -> None:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE notifications SET status=$1, error_message=$2 WHERE id=$3",
                status,
                error,
                notification_id,
            )

    def _build_message(self, event: IncomingEvent) -> str:
        preview = event.data.get("message") if isinstance(event.data, dict) else None
        if preview:
            return preview
        return f"{event.event_type} for user {event.user_id} at {event.timestamp.isoformat()}"

    def _select_channel(self, user: Dict[str, Any], event_data: Dict[str, Any]) -> str:
        preferred = event_data.get("preferred_channel")
        preferred = preferred.lower() if isinstance(preferred, str) else None

        def available(choice: str) -> bool:
            return bool(
                (choice == "email" and user.get("email"))
                or (choice == "whatsapp" and user.get("whatsapp"))
                or (choice == "websocket" and user.get("ws_id"))
            )

        if preferred and available(preferred):
            return preferred

        for channel in ("websocket", "email", "whatsapp"):
            if available(channel):
                return channel

        raise ValueError("No delivery channels available for user")


async def main() -> None:
    orchestrator = SagaOrchestrator()
    await orchestrator.setup()
    metrics_port = int(os.getenv("METRICS_PORT", "9101"))
    start_http_server(metrics_port)
    logger.info("Metrics available on :%s/metrics", metrics_port)
    await orchestrator.run()


if __name__ == "__main__":
    asyncio.run(main())
