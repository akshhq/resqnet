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

