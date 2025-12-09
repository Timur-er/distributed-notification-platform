# Вимоги

- REST endpoint `POST /events` з валідацією `event_type`, `user_id`, `timestamp`, `data`.
- Черги/топіки RabbitMQ: `events.main`, `events.retry`, `events.dlq`, `saga.commands`, `saga.commands.retry`, `saga.events`, `saga.compensations`, `ws.notifications`.
- Saga Orchestrator:
  - створює запис у `notifications`,
  - визначає канал доставки за даними користувача та `preferred_channel`,
  - шле команду worker-у, обробляє відповідь, оновлює статус,
  - на помилках виконує retry або відправляє у DLQ.
- Notification Worker: читає `saga.commands`, відправляє Email/WhatsApp/WebSocket, повертає результат у `saga.events`.
- Надійність: повторні спроби (TTL retry), dead-letter queue, логування всіх збоїв.
- Моніторинг: Prometheus `/metrics` на всіх сервісах, Grafana дашборди (success vs failed, throughput, DLQ, WS).
- Запуск у Docker Compose, масштабується кількома інстансами worker/consumer.
