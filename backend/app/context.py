def classify_context(speed: float) -> str:
    if speed < 0.3:
        return "stationary"
    elif speed < 1.5:
        return "walking"
    elif speed < 3.5:
        return "running"
    else:
        return "vehicle"


def detect_speed_anomaly(prev_speed: float, curr_speed: float) -> bool:
    # Sudden jump greater than 5 m/s
    return abs(curr_speed - prev_speed) > 5.0


def calculate_risk(emergency: bool, anomaly: bool) -> str:
    if emergency:
        return "critical"
    elif anomaly:
        return "elevated"
    else:
        return "normal"

ALERT_COOLDOWN = 30  # seconds


def should_alert(device_id: str, risk: str, timestamp: int, alert_state: dict) -> bool:
    if risk not in ("elevated", "critical"):
        return False

    last = alert_state.get(device_id)

    if last is None:
        return True

    if timestamp - last >= ALERT_COOLDOWN:
        return True

    return False

ESCALATION_STEPS = [
    (30, "escalated"),     # after 30s
    (90, "critical")      # after 90s
]


def check_escalation(
    device_id: str,
    emergency: bool,
    timestamp: int,
    escalation_state: dict
):
    if not emergency:
        escalation_state.pop(device_id, None)
        return None

    state = escalation_state.get(device_id)

    if state is None:
        escalation_state[device_id] = {
            "start": timestamp,
            "level": 0
        }
        return None

    elapsed = timestamp - state["start"]

    for i, (threshold, label) in enumerate(ESCALATION_STEPS):
        if elapsed >= threshold and state["level"] < i + 1:
            state["level"] = i + 1
            return label

    return None
