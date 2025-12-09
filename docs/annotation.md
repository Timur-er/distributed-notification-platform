# Анотація

Distributed Event-Driven Notification Platform — навчальний проєкт, що демонструє побудову мікросервісної системи доставки нотифікацій на базі RabbitMQ та шаблону Saga. Платформа приймає події від зовнішніх сервісів через REST Gateway, оркеструє бізнес-процес у окремому Saga-сервісі, зберігає стани у Postgres та асинхронно доставляє повідомлення через Email, WhatsApp або WebSocket. Для надійності використано retry-черги, dead-letter queue та метрики Prometheus з дашбордами Grafana. Увесь стек запускається через Docker Compose.
