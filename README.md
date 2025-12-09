# distributed-notification-platform
# Distributed Event-Driven Notification Platform 🚀

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/downloads/)

Розподілена подійно-керована платформа нотифікацій. Приймає події через REST, обробляє їх у RabbitMQ, керує бізнес-процесом через Saga Orchestrator, доставляє повідомлення Email/WhatsApp/WebSocket та експонує метрики Prometheus з дашбордами Grafana.

- [Анотація](./docs/annotation.md)
- [Проблематика](./docs/problems.md)
- [Архітектура з sequence diagram](./docs/architecture.md)
- [Вимоги](./docs/requirements.md)

## Сервіси
- **gateway_service/** — FastAPI Gateway (`POST /events`, WebSocket, метрики).
- **saga_orchestrator/** — Saga orchestrator (RabbitMQ consumer, Postgres, метрики).
- **notification_worker/** — worker доставки (Email/WhatsApp/WebSocket, retry, метрики).
- **infrastructure/** — Docker Compose, RabbitMQ, Postgres, Prometheus, Grafana.

## Швидкий старт
Потрібні Docker + Docker Compose.

```bash
cd infrastructure
docker-compose up --build
```

Доступи:
- Gateway REST: http://localhost:8000/docs
- Gateway WebSocket: ws://localhost:8000/ws/{user_id}
- RabbitMQ UI: http://localhost:15672 (guest/guest)
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000 (admin/admin)
- Postgres: localhost:5432 (`postgres`/`postgres`, db `notifications`)

## Надсилання тестової події
```bash
curl -X POST http://localhost:8000/events \
  -H "Content-Type: application/json" \
  -d '{
    "event_type": "order_created",
    "user_id": 1,
    "timestamp": "2024-06-01T12:00:00Z",
    "data": {
      "message": "Order #42 confirmed",
      "preferred_channel": "websocket"
    }
  }'
```

Після цього Saga створить запис у БД, worker відправить нотифікацію, а статус оновиться через `saga.events`. Для WebSocket підпишіться на `ws://localhost:8000/ws/1`.

## Локальний запуск окремих сервісів
- Gateway: `cd gateway_service && uvicorn app.main:app --reload`
- Worker: `cd notification_worker && python -m app.consumer`
- Orchestrator: `cd saga_orchestrator && python -m app.orchestrator`

## Тести
Базові unit-тести в підпапках `tests`. Запуск: `pytest`.

## Ліцензія
[MIT](./LICENSE)
