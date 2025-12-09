#!/usr/bin/env bash

set -euo pipefail

curl -X POST http://localhost:8000/events \
  -H "Content-Type: application/json" \
  -d '{
    "event_type": "demo_event",
    "user_id": 1,
    "timestamp": "'"$(date -Iseconds)"'",
    "data": {
      "message": "Hello from demo script",
      "preferred_channel": "websocket"
    }
  }'
