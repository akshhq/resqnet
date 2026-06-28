# ResQNet — Response & Rescue Network

> A context-aware, device-independent emergency response prototype with real-time situational awareness, intelligent escalation, and a live monitoring dashboard.

---

## Table of Contents

1. [What It Does](#what-it-does)
2. [Architecture](#architecture)
3. [Features](#features)
4. [Tech Stack](#tech-stack)
5. [Project Structure](#project-structure)
6. [How to Run](#how-to-run)
7. [Simulator Controls](#simulator-controls)
8. [API Reference](#api-reference)
9. [Dashboard](#dashboard)
10. [Design Principles](#design-principles)
11. [Current Status](#current-status)
12. [Planned Improvements](#planned-improvements)
13. [Author](#author)

---

## What It Does

Most SOS systems assume you have calm, unobstructed access to your phone. ResQNet doesn't.

It is designed around a single premise: **the device triggers the alert, not the app**. Once panic is triggered, the system latches into emergency state, classifies movement context in real time, escalates automatically if danger persists, and streams everything live to a monitoring dashboard — all without the user needing to do anything else.

---

## Architecture

```
[ Device / Simulator ]
        │
        │  POST /device/update  (1 Hz)
        ▼
[ FastAPI Backend ]
        │
        │  WebSocket broadcast
        ▼
[ Live Dashboard ]     [ Responder Dashboard (planned) ]
```

### Core data flow

```
Panic Trigger
    → Emergency state latched (cannot auto-cancel)
    → Context classified  (stationary / walking / running / vehicle)
    → Speed anomaly checked  (Δspeed > 5 m/s = elevated)
    → Risk calculated  (normal / elevated / critical)
    → Escalation checked  (30s → escalated, 90s → critical)
    → Broadcast to all connected WebSocket clients
    → Alert cooldown prevents spam  (30s window)
```

---

## Features

### Emergency Trigger & Latch
- Panic button activates emergency mode instantly
- Emergency state is **latched** — it cannot be cancelled automatically or by a coerced tap
- Reset requires an explicit `reset: true` signal from the device
- Reset flag is included in every broadcast so all dashboards react simultaneously

### Realistic Device Simulation
- **Heading-based movement model** — device moves in realistic curves, not random scatter
- Speed profiles per mode with gaussian noise and smooth lerp transitions
- GPS noise (~1 m jitter) layered on top of real movement, matching consumer hardware
- Brief pauses injected for walking mode (traffic lights, checking phone)
- Battery drains at 0.05%/s, warns at 20%, stops at 0

### Context Classification
Speed (m/s) is mapped to movement context each tick:

| Context | Speed Range |
|---|---|
| Stationary | < 0.3 m/s |
| Walking | 0.3 – 1.5 m/s |
| Running | 1.5 – 3.5 m/s |
| Vehicle | > 3.5 m/s |

### Risk Assessment
- `normal` — no emergency, no anomaly
- `elevated` — sudden speed jump > 5 m/s (speed anomaly)
- `critical` — emergency is active

### Time-Based Escalation
- After **30 seconds** of continuous emergency → `escalated`
- After **90 seconds** → `critical`
- All pending escalation levels are advanced in one pass (no silent skips if updates are infrequent)
- Escalation state is fully cleared on reset

### Alert System
- Alerts fire when risk is `elevated` or `critical`
- 30-second cooldown prevents repeat spam
- Alert flag carried in every broadcast payload

### Live Dashboard
- Real-time map with device marker (green / orange / red by risk)
- Marker blinks during active emergency
- **Movement trail** shown only during active emergency:
  - Draws the last 5 minutes of pre-emergency path on panic trigger (if available)
  - Extends live during emergency, coloured by risk level
  - Cleared automatically on reset
- Battery bar with colour coding (green / amber / red)
- Event timeline (last 100 entries, with ISO timestamp on hover)
- WebSocket reconnection with exponential backoff (1s → 30s)
- Connection status dot (🟢 live / 🟠 reconnecting / 🔴 disconnected)
- Audio alert on emergency trigger and escalation
- **Dark mode** toggle (CartoDB dark tiles + dark panel theme)
- Trail legend showing path colour meanings
- Replay button: enter a device ID to replay its emergency history

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11+, FastAPI 0.128, Uvicorn |
| Real-time | WebSockets (native FastAPI) |
| Validation | Pydantic v2 with Field constraints |
| State | In-memory (deque-bounded history, prototype stage) |
| Frontend | HTML5, Vanilla JS, Leaflet.js |
| Map tiles | OpenStreetMap (light), CartoDB Dark (dark mode) |
| Simulator | Python, threading, heading-based movement model |

---

## Project Structure

```
resqnet/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py          # FastAPI app, routes, broadcast logic
│   │   ├── context.py       # Speed classification, risk, escalation
│   │   ├── schemas.py       # Pydantic request model with field validation
│   │   ├── storage.py       # In-memory state dicts and deque history
│   │   └── websocket.py     # ConnectionManager with dead-client cleanup
│   └── requirements.txt
├── dashboard/
│   ├── index.html           # Layout, panels, dark mode styles
│   └── script.js            # WebSocket client, map, trail, replay
└── simulator/
    ├── simulator.py         # Full interactive + demo simulator
    └── send_updates.py      # Minimal one-shot update script
```

---

## How to Run

### Prerequisites
- Python 3.11+
- A virtual environment (recommended)

### 1. Backend

```bash
cd backend
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate

pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

Backend runs at `http://127.0.0.1:8000`

#### Environment variables (optional)

Create `backend/.env` to override defaults:

```
CORS_ORIGINS=http://localhost:5500,http://127.0.0.1:5500
```

### 2. Dashboard

```bash
cd dashboard
python -m http.server 5500
```

Open `http://localhost:5500/index.html`

### 3. Simulator

```bash
cd simulator
python simulator.py
```

#### Options

```bash
python simulator.py --id DEVICE_02                        # custom device ID
python simulator.py --url http://192.168.1.10:8000/device/update  # remote backend
python simulator.py --lat 19.076 --lng 72.877             # start in Mumbai
python simulator.py --demo                                # run scripted demo automatically
```

---

## Simulator Controls

Interactive mode (default):

| Key | Action |
|---|---|
| `p` | Trigger panic (emergency ON) |
| `r` | Reset panic (emergency OFF) |
| `0` | Mode: stationary |
| `1` | Mode: walking |
| `2` | Mode: running |
| `3` | Mode: vehicle |
| `t` | Sharp turn (randomise heading instantly) |
| `q` | Quit |

Demo mode (`--demo`):

Runs a fully scripted scenario — no keypresses needed. Useful for presentations.

```
  0s   walking — normal state
 10s   panic triggered
 15s   mode switches to running
 30s   backend fires "escalated" automatically
 90s   backend fires "critical" automatically
110s   reset sent — returns to normal
120s   simulator exits
```

---

## API Reference

### `POST /device/update`

Send a position + state update from the device.

**Request body:**
```json
{
  "device_id": "SIM_DEVICE_01",
  "timestamp": 1700000000,
  "latitude": 28.6139,
  "longitude": 77.2090,
  "speed": 1.2,
  "battery": 85,
  "emergency": false,
  "reset": false
}
```

**Validation:**
- `latitude`: −90 to 90
- `longitude`: −180 to 180
- `speed`: ≥ 0
- `battery`: 0 to 100

**Response:**
```json
{ "status": "broadcasted", "risk": "normal" }
```

### `GET /device/{device_id}`
Returns latest state for a device.

### `GET /device/{device_id}/history`
Returns full position + event history (last 1,000 entries, capped in memory).

### `WebSocket /ws/live`
Connect to receive all device broadcasts in real time.

**Broadcast payload:**
```json
{
  "device_id": "SIM_DEVICE_01",
  "latitude": 28.6139,
  "longitude": 77.2090,
  "speed": 1.2,
  "context": "walking",
  "battery": 85,
  "emergency": false,
  "risk": "normal",
  "timestamp": 1700000000,
  "alert": false,
  "escalation": null,
  "reset": false
}
```

---

## Dashboard

| Element | Description |
|---|---|
| Map | Leaflet map with real-time device marker |
| Marker colour | 🟢 Normal · 🟠 Elevated · 🔴 Emergency/Critical |
| Movement trail | Shown only during active emergency (blue → orange → red by risk). Includes 5-min pre-emergency path. Cleared on reset. |
| Status box | Device ID, context, risk, emergency state, escalation label, reset flag |
| Battery bar | Visual % bar, colour-coded: green > 50%, amber 20–50%, red < 20% |
| Timeline | Last 100 events with local time display and ISO timestamp on hover |
| Replay | Enter a device ID and click ▶ Play to replay the emergency path from history |
| Connection dot | Top-right: 🟢 live / 🟠 reconnecting / 🔴 disconnected |
| Dark mode | 🌙 button switches map tiles and all panels to dark theme |
| Audio alert | Short beep on emergency trigger and escalation events |

---

## Design Principles

- **Backend is the single source of truth** — frontend reflects latest state, never manages its own
- **Emergency is latched** — cannot be silently cancelled by attacker or network glitch
- **Reset is explicit** — only a deliberate `reset: true` payload clears emergency state
- **Escalation is time-driven, not polling** — elapsed time from panic start, advanced in one pass
- **History is bounded** — deque with maxlen=1000 prevents silent RAM exhaustion
- **Validation at the boundary** — Pydantic Field constraints reject invalid payloads before they touch state
- **Dead clients are cleaned up** — failed WebSocket sends are caught and the connection removed

---

## Current Status

| Area | Status |
|---|---|
| Backend core | ✅ Stable |
| WebSocket broadcast | ✅ Working |
| Risk + escalation logic | ✅ Fixed and verified |
| Simulator (interactive) | ✅ Working |
| Simulator (demo mode) | ✅ Working |
| Dashboard (live view) | ✅ Working |
| Movement trail | ✅ Working (emergency-only, 5-min pre-window) |
| Dark mode | ✅ Working |
| Replay | ✅ Working |
| Persistent storage | ❌ Not yet (in-memory only) |
| Authentication | ❌ Not yet |
| Notifications (SMS/email) | ❌ Not yet |
| Hardware device | ❌ Not yet |

---

## Planned Improvements

### Security
- API key authentication for all device endpoints
- JWT-based authentication for dashboard WebSocket connections
- Rate limiting on `/device/update` (prevent spam / DoS)
- Registered device list — only known device IDs can send updates

### Backend
- Replace `print()` with Python `logging` module (levels, timestamps, file output)
- API versioning — prefix all routes with `/api/v1/`
- Remove or secure the `/test/broadcast` debug endpoint
- Persistent storage (SQLite → PostgreSQL) so history survives restarts
- Configurable escalation thresholds via `.env`

### Notifications
- Email alerts via SMTP / SendGrid on emergency trigger
- SMS alerts via Twilio for trusted contacts
- WhatsApp alerts via Twilio WhatsApp API (high reach in India)
- Push notifications via Firebase Cloud Messaging (FCM)

### Dashboard 1 — User Account Dashboard
Personal dashboard for the device owner:
- Phone OTP authentication
- Manage registered devices (name, battery, last seen, connection status)
- Edit emergency contacts with priority order and per-contact notification method
- Notification preferences and quiet hours
- Full incident history with timeline replay
- Progressive Web App (PWA) for home screen install and offline contacts

### Dashboard 2 — Emergency Responder Dashboard
For trusted contacts receiving the alert:
- One-time session token sent via SMS on emergency trigger (no password needed in a crisis)
- Live map with movement trail and speed context
- "I'm on my way" acknowledgement button
- Live audio stream from device microphone (with user consent)
- Multi-responder view (see who else is watching)
- Session auto-expires after reset + 30 minutes

### Dashboard 3 — Operations Command Center
For organisations (universities, NGOs) monitoring many devices:
- Real-time map of all registered devices, colour-coded by status
- Active incidents panel with responder assignment
- Geofence zones with auto-routing of incidents to zone responders
- Regional statistics and average response time
- Incident log with CSV export

### Dashboard 4 — Org Admin Panel
For organisation administrators:
- User and device management
- Responder roster and shift management
- Alert delivery audit log (did the SMS actually deliver?)
- Customisable escalation thresholds per organisation
- Monthly incident reports

### Infrastructure
- Docker + `docker-compose` for single-command local setup
- GitHub Actions CI (run pytest on every push)
- `.gitignore` and `LICENSE` file

### Hardware (Future)
- **ESP32** microcontroller with **NEO-M8N GPS module**
- Physical panic button on GPIO with debounce
- **MQTT** communication to backend (more reliable than HTTP on flaky connections)
- LiPo battery with TP4056 charger, INMP441 I2S microphone
- OTA (over-the-air) firmware updates
- Target form factor: < 40g, wrist-wear or clip-on

---

## Author

**Aksh Kumar**
Undergraduate Computer Science Student

---

## License

MIT

> ResQNet is not intended to replace official emergency services. It is a research prototype.