from prometheus_client import Counter, Histogram

saga_started = Counter("saga_started_total", "Saga processes started from incoming events")
saga_completed = Counter("saga_completed_total", "Saga successfully completed")
saga_failed = Counter("saga_failed_total", "Saga failed after worker result")
dlq_events = Counter("saga_dlq_total", "Events sent to dead-letter queue")
worker_signals = Counter(
    "saga_worker_signals_total",
    "Signals returned from notification worker",
    ["status"],
)
worker_roundtrip_seconds = Histogram(
    "saga_worker_roundtrip_seconds",
    "Time from saga command publish to worker response",
    buckets=(0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10),
)
