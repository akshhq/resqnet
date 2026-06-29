from collections import deque

# Latest state per device
device_state = {}

# Full timeline history per device — capped at 1000 entries.
# deque(maxlen=1000) automatically discards oldest on overflow.
# device_id -> deque of event dicts
device_history = {}

# Alert cooldown state
# device_id -> last_alert_timestamp
alert_state = {}

# Escalation tracking
# device_id -> { "start": timestamp, "level": int }
escalation_state = {}

HISTORY_MAXLEN = 1000

# ---------------------------------------------------------------------------
# 5.4 — Registered device list
# ---------------------------------------------------------------------------
# Set of device IDs that have been registered via POST /device/register.
# Only registered IDs are accepted at POST /device/update.
# Persisted in memory for the lifetime of the process (prototype stage).
registered_devices: set[str] = set()