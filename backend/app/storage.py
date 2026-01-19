# Latest state of each device
device_state = {}

# Full timeline history per device
# device_id -> list of events
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
