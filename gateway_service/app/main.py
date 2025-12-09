import asyncio
import json
import os
import time
from typing import Dict, Set

import aio_pika
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

from .models import EventPayload, WsNotification
from .utils import setup_logging, to_message

logger = setup_logging()

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")
EVENT_EXCHANGE = os.getenv("EVENT_EXCHANGE", "events")
WS_EXCHANGE = os.getenv("WS_EXCHANGE", "ws")
RETRY_DELAY_MS = int(os.getenv("RETRY_DELAY_MS", "5000"))

events_received = Counter("gateway_events_received_total", "Total events accepted by gateway")
events_publish_failed = Counter("gateway_events_publish_failed_total", "Failed publishes to broker")
events_publish_latency = Histogram("gateway_events_publish_seconds", "Publish latency")
ws_messages_sent = Counter("gateway_ws_messages_sent_total", "Messages pushed to WebSocket clients")
ws_active = Gauge("gateway_ws_active_connections", "Active WebSocket connections")


class ConnectionManager:
    def __init__(self) -> None:
        self.active: Dict[int, Set[WebSocket]] = {}

    async def connect(self, user_id: int, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active.setdefault(user_id, set()).add(websocket)
        ws_active.set(sum(len(v) for v in self.active.values()))
        logger.info("WebSocket connected for user %s", user_id)

    def disconnect(self, user_id: int, websocket: WebSocket) -> None:
        clients = self.active.get(user_id, set())
        clients.discard(websocket)
        if not clients:
            self.active.pop(user_id, None)
        ws_active.set(sum(len(v) for v in self.active.values()))
        logger.info("WebSocket disconnected for user %s", user_id)

    async def send_user(self, user_id: int, message: str) -> None:
        clients = list(self.active.get(user_id, []))
        for client in clients:
            try:
                await client.send_text(message)
                ws_messages_sent.inc()
            except Exception:
                self.disconnect(user_id, client)


class RabbitMQManager:
    def __init__(self) -> None:
        self.connection: aio_pika.RobustConnection | None = None
        self.channel: aio_pika.abc.AbstractChannel | None = None
        self.events_exchange: aio_pika.abc.AbstractExchange | None = None
        self.ws_exchange: aio_pika.abc.AbstractExchange | None = None
        self.ws_queue: aio_pika.abc.AbstractQueue | None = None

    async def connect(self) -> None:
        self.connection = await aio_pika.connect_robust(RABBITMQ_URL)
        self.channel = await self.connection.channel(publisher_confirms=True)
        await self.channel.set_qos(prefetch_count=50)
        self.events_exchange = await self.channel.declare_exchange(
            EVENT_EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
        )
        self.ws_exchange = await self.channel.declare_exchange(
            WS_EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
        )
        await self._declare_topology()
        logger.info("Connected to RabbitMQ at %s", RABBITMQ_URL)

    async def _declare_topology(self) -> None:
        assert self.channel is not None
        # Base event queues with retry + DLQ wiring
        main_args = {
            "x-dead-letter-exchange": EVENT_EXCHANGE,
            "x-dead-letter-routing-key": "events.retry",
        }
        retry_args = {
            "x-message-ttl": RETRY_DELAY_MS,
            "x-dead-letter-exchange": EVENT_EXCHANGE,
            "x-dead-letter-routing-key": "events.main",
        }
        events_main = await self.channel.declare_queue("events.main", durable=True, arguments=main_args)
        events_retry = await self.channel.declare_queue("events.retry", durable=True, arguments=retry_args)
        events_dlq = await self.channel.declare_queue("events.dlq", durable=True)
        await events_main.bind(self.events_exchange, routing_key="events.main")
        await events_retry.bind(self.events_exchange, routing_key="events.retry")
        await events_dlq.bind(self.events_exchange, routing_key="events.dlq")

        # WS queue is consumed by gateway for live pushes
        self.ws_queue = await self.channel.declare_queue("ws.notifications", durable=True)
        await self.ws_queue.bind(self.ws_exchange, routing_key="ws.notifications")

    async def publish_event(self, payload: EventPayload) -> None:
        if not self.events_exchange:
            raise RuntimeError("Exchange is not ready")
        body = to_message(payload.dict())
        message = aio_pika.Message(body=body, delivery_mode=aio_pika.DeliveryMode.PERSISTENT)
        await self.events_exchange.publish(message, routing_key="events.main")

    async def consume_ws_notifications(self, manager: ConnectionManager) -> None:
        if not self.ws_queue:
            raise RuntimeError("WS queue is not ready")
        async with self.ws_queue.iterator() as queue_iter:
            async for message in queue_iter:
                async with message.process():
                    try:
                        payload = json.loads(message.body.decode())
                        ws_message = WsNotification(**payload)
                    except Exception as exc:  # noqa: BLE001
                        logger.error("Failed to parse WS payload: %s", exc)
                        continue
                    await manager.send_user(ws_message.user_id, ws_message.message)

    async def close(self) -> None:
        if self.connection:
            await self.connection.close()


app = FastAPI(title="Distributed Notification Platform - Gateway", version="0.1.0")
manager = ConnectionManager()
rabbit = RabbitMQManager()


@app.on_event("startup")
async def startup_event() -> None:
    await rabbit.connect()
    app.state.ws_consumer = asyncio.create_task(rabbit.consume_ws_notifications(manager))


@app.on_event("shutdown")
async def shutdown_event() -> None:
    consumer: asyncio.Task | None = getattr(app.state, "ws_consumer", None)
    if consumer:
        consumer.cancel()
    await rabbit.close()


@app.post("/events")
async def ingest_event(payload: EventPayload) -> JSONResponse:
    events_received.inc()
    started = time.perf_counter()
    try:
        await rabbit.publish_event(payload)
    except Exception as exc:  # noqa: BLE001
        events_publish_failed.inc()
        logger.error("Publish failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to publish event")
    finally:
        events_publish_latency.observe(time.perf_counter() - started)
    return JSONResponse({"status": "queued", "event_id": payload.event_id})


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics")
async def metrics() -> PlainTextResponse:
    return PlainTextResponse(generate_latest().decode(), media_type=CONTENT_TYPE_LATEST)


@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: int) -> None:
    await manager.connect(user_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(user_id, websocket)
