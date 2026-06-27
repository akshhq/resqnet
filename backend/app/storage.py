from collections import deque

# Latest state of each device
device_state = {}

# Full timeline history per device — capped at 1000 entries per device.
# deque(maxlen=1000) automatically discards the oldest entry when the cap is
# reached, so a long-running session cannot silently exhaust all available RAM.
# device_id -> deque of event dicts
device_history = {}

# Alert state per device
# device_id -> last_alert_timestamp
alert_state = {}

# Escalation tracking
# device_id -> {
#   "start": timestamp,
#   "level": int
# }
escalation_state = {}

HISTORY_MAXLEN = 1000