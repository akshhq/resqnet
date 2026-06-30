# ResQNet — Response & Rescue Network

> A context-aware, device-independent emergency response prototype with real-time situational awareness, intelligent escalation, integrated dual simulators, and a live monitoring dashboard.

---

## Table of Contents

1. [What It Does](#what-it-does)
2. [Architecture](#architecture)
3. [Features](#features)
4. [Tech Stack](#tech-stack)
5. [Project Structure](#project-structure)
6. [How to Run](#how-to-run)
7. [In-Browser Simulator](#in-browser-simulator)
8. [Python Simulator (Optional)](#python-simulator-optional)
9. [API Reference](#api-reference)
10. [Security](#security)
11. [Design Principles](#design-principles)
12. [Current Status](#current-status)
13. [Planned Improvements](#planned-improvements)
14. [Author](#author)

---

## What It Does

Most SOS systems assume you have calm, unobstructed access to your phone. ResQNet doesn't.

It is designed around a single premise: **the device triggers the alert, not the app**. Once panic is triggered, the system latches into emergency state, classifies movement context in real time, escalates automatically if danger persists, and streams everything live to a monitoring dashboard — all without the user needing to do anything else.

---

## Architecture

```
[ Browser Simulator ]      [ Python Simulator ]
  (built into dashboard)     (simulator.py, optional)
        │                           │
        │   POST /device/update (1 Hz, both)
        ▼                           ▼
            [ FastAPI Backend ]
                    │
                    │  WebSocket broadcast
                    ▼
            [ Live Dashboard ]
   (renders BOTH simulators on one map, side by side)
```

Both simulators can run **at the same time**, driving completely independent devices that appear together on the same map and in the same sidebar — there's no conflict between them.

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

### Dual Simulators (run independently or together)
- **In-browser simulator** — built directly into the dashboard. Click "+ Add Device" to spawn any number of simulated devices, each with its own movement loop, right in the browser. No Python process needed.
- **Python simulator** — `simulator.py` runs standalone in a terminal for interactive keypress control or scripted demos.
- Both use the **same heading-based movement model**: realistic curves, gaussian speed noise, smooth lerp transitions, GPS jitter, and brief pauses (traffic lights, checking phone).
- Devices from either simulator appear together on the same dashboard, identified automatically — the dashboard doesn't care which one created a device.

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

### Live Dashboard — Sidebar + Map Layout
- **Left sidebar**, split top/bottom:
  - **Top half — Device list**: every active device (from either simulator) as a card showing live speed, context, risk pill, battery %, and status dot. Each card has its own Panic / Reset / Mode / Turn controls.
  - **Bottom half — Movement log**: switches automatically to show the log of whichever device is selected. Capped at 200 entries per device.
- **Map fills the remaining screen** — never locked to one device. Auto-pans only when the *currently selected* device leaves the visible bounds, so you're always free to pan/zoom and inspect any device manually.
- **Add Device modal** — name, preset city or custom lat/lng, starting mode.
- Marker colour: 🟢 normal · 🟠 elevated · 🔴 emergency (with blink)
- **Movement trail** shown only during active emergency, colour-coded by risk, cleared on reset
- Battery bar with colour coding (green / amber / red)
- WebSocket reconnection with exponential backoff (1s → 30s)
- Connection status dot (🟢 live / 🟠 reconnecting / 🔴 disconnected)
- Audio alert on emergency trigger and escalation
- **Dark mode** toggle (CartoDB dark tiles + dark panel theme, default ON)
- API key panel — only needed if backend auth is enabled (see [Security](#security))

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11+, FastAPI 0.128, Uvicorn |
| Real-time | WebSockets (native FastAPI) |
| Validation | Pydantic v2 with Field constraints |
| Auth | Optional API key (HTTP header + WS token), rate limiting via slowapi |
| State | In-memory (deque-bounded history, prototype stage) |
| Frontend | HTML5, external CSS, Vanilla JS, Leaflet.js |
| Map tiles | CartoDB Dark (default), OpenStreetMap (light mode) |
| Simulators | In-browser JS engine + standalone Python (`simulator.py`) — both heading-based |

---

## Project Structure

```
resqnet/
├── start.bat                 # Windows one-click startup
├── start.sh                  # macOS/Linux one-click startup
├── .gitignore
├── LICENSE
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py           # FastAPI app, routes, broadcast logic
│   │   ├── auth.py           # Optional API key + WS token validation, rate limit handler
│   │   ├── context.py        # Speed classification, risk, escalation
│   │   ├── schemas.py        # Pydantic request models with field validation
│   │   ├── storage.py        # In-memory state dicts, deque history, registered devices
│   │   └── websocket.py      # ConnectionManager with dead-client cleanup
│   ├── requirements.txt
│   └── .env.example          # Copy to .env to enable auth / configure CORS
├── Trial_Dashboard/
│   ├── index.html            # Sidebar + map layout, modal markup
│   ├── style.css             # All dashboard styling (external file)
│   └── script.js             # In-browser simulator engine, WS client, map, replay
└── simulator/
    ├── simulator.py          # Standalone interactive + demo Python simulator
    └── send_updates.py       # Minimal one-shot update script
```

> Note: this project does **not** use Docker. `start.bat` / `start.sh` run everything directly with Python — no containers needed.

---

## How to Run

### Prerequisites
- Python 3.11+
- `pip install -r backend/requirements.txt`

### One-command startup

```bash
# Windows
start.bat

# macOS / Linux
./start.sh
```

This starts the backend, serves the dashboard, and opens it in your default browser automatically. The dashboard's own simulator is ready immediately — click **+ Add Device** to start simulating.

### Optional flags

```bash
start.bat               # backend + dashboard only (recommended — use "+ Add Device" in browser)
start.bat --with-sim    # ALSO launches the Python simulator (interactive) alongside the browser one
start.bat --demo        # ALSO launches the Python simulator in scripted demo mode
```
(Same flags work with `./start.sh` on macOS/Linux.)

### Manual startup (if you prefer separate terminals)

**Backend:**
```bash
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
> Binds to `0.0.0.0` (not `127.0.0.1`) so both `localhost` and `127.0.0.1` resolve correctly — important on Windows where `localhost` can resolve to the IPv6 loopback `::1`.

**Dashboard:**
```bash
cd Trial_Dashboard
python -m http.server 5500
```
Open `http://localhost:5500`

**Python simulator (optional):**
```bash
cd simulator
python simulator.py
```

---

## In-Browser Simulator

No setup needed — it's part of the dashboard.

1. Click **+ Add Device** (top of the sidebar)
2. Name it, pick a starting city (or enter custom lat/lng), choose a starting mode
3. Click **Add Device** — it appears as a card and starts moving immediately

Each device card has:

| Control | Action |
|---|---|
| 🚨 Panic | Toggle emergency state for this device |
| ✅ Reset | Explicitly clear emergency + escalation state |
| Mode dropdown | Switch between Walk / Still / Run / Vehicle |
| ↩ Turn | Instantly randomise heading (simulate turning a corner) |
| ✕ | Remove this device |

Click anywhere on a card (not on a control) to **select** it — the movement log below switches to that device, and the map pans to it. You can add as many devices as you want; each runs its own independent 1Hz movement loop.

---

## Python Simulator (Optional)

Runs alongside the browser simulator — devices from both appear together on the same dashboard automatically (the dashboard auto-creates a card for any device it sees broadcast from the backend, regardless of source).

```bash
cd simulator
python simulator.py                          # interactive
python simulator.py --id DEVICE_02            # custom device ID
python simulator.py --lat 19.076 --lng 72.877 # start in Mumbai
python simulator.py --demo                    # scripted demo, no keypresses
python simulator.py --key <your_api_key>      # if backend auth is enabled
```

### Interactive controls

| Key | Action |
|---|---|
| `p` | Trigger panic |
| `r` | Reset panic |
| `0` | Mode: stationary |
| `1` | Mode: walking |
| `2` | Mode: running |
| `3` | Mode: vehicle |
| `t` | Sharp turn |
| `q` | Quit |

### Demo mode timeline

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

### `POST /device/register`
Register a device ID before it can send updates.
```json
{ "device_id": "SIM_DEVICE_01" }
```

### `POST /device/update`
Send a position + state update from a registered device.

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

**Validation:** `latitude` −90 to 90 · `longitude` −180 to 180 · `speed` ≥ 0 · `battery` 0–100

**Response:** `{ "status": "broadcasted", "risk": "normal" }`

### `GET /device/{device_id}`
Returns latest state for a device.

### `GET /device/{device_id}/history`
Returns position + event history (last 1,000 entries, capped in memory).

### `GET /device/registered`
Lists all registered device IDs (auth required if enabled).

### `WebSocket /ws/live`
Connect to receive all device broadcasts in real time. If auth is enabled, append `?token=<api_key>`.

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

## Security

Authentication is **optional and off by default** — the system works immediately with zero configuration.

### Enabling auth

1. Copy `backend/.env.example` to `backend/.env`
2. Generate a key: `python -c "import secrets; print(secrets.token_hex(32))"`
3. Set `API_KEY=<generated key>` in `.env`
4. Restart the backend — it prints a startup banner confirming auth is enabled

Once enabled:
- All HTTP endpoints require an `X-API-Key` header → `403` if missing/wrong
- WebSocket connections require `?token=<key>` in the URL → closed with code `4403` if invalid
- The Python simulator auto-loads `backend/.env`, so it picks up the key automatically — or pass `--key` explicitly
- The dashboard shows a 🔑 API Key input — enter the same key and click Apply to reconnect

### Other protections
- Rate limiting via `slowapi`: 60 req/min on `/device/update`, 30 req/min on `/device/history`
- Device registration required — `/device/update` rejects any `device_id` that hasn't called `/device/register` first
- Pydantic field validation rejects out-of-range coordinates, negative speed, invalid battery before they touch state

---

## Design Principles

- **Backend is the single source of truth** — frontend reflects latest state, never manages its own
- **Emergency is latched** — cannot be silently cancelled by attacker or network glitch
- **Reset is explicit** — only a deliberate `reset: true` payload clears emergency state
- **Escalation is time-driven, not polling** — elapsed time from panic start, advanced in one pass
- **History is bounded** — deque with maxlen=1000 prevents silent RAM exhaustion
- **Validation at the boundary** — Pydantic Field constraints reject invalid payloads before they touch state
- **Dead clients are cleaned up** — failed WebSocket sends are caught and the connection removed
- **Auth is additive, not required** — the system is fully usable with zero config, and becomes secure the moment a key is configured
- **The dashboard doesn't care where a device comes from** — browser simulator, Python simulator, or real hardware all look identical once they reach the backend

---

## Current Status

| Area | Status |
|---|---|
| Backend core | ✅ Stable |
| WebSocket broadcast | ✅ Working |
| Risk + escalation logic | ✅ Fixed and verified |
| Optional API key auth | ✅ Working (HTTP + WS) |
| Rate limiting | ✅ Working |
| Device registration | ✅ Working |
| In-browser simulator | ✅ Working — multi-device, sidebar UI |
| Python simulator (interactive + demo) | ✅ Working |
| Both simulators running simultaneously | ✅ Working |
| Dashboard (sidebar + map layout) | ✅ Working |
| External CSS file | ✅ Done |
| Movement trail | ✅ Working (emergency-only, colour-coded) |
| Dark mode | ✅ Working (default) |
| Docker | ❌ Removed — not needed for this project |
| Persistent storage | ❌ Not yet (in-memory only) |
| Notifications (SMS/email) | ❌ Not yet |
| Hardware device | ❌ Not yet |

---

## Planned Improvements

### Backend
- Replace remaining `print()` calls with Python `logging` module (levels, timestamps, file output)
- API versioning — prefix all routes with `/api/v1/`
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

### Testing & CI
- Unit tests for `context.py` (escalation, risk, classification logic)
- Integration tests for the full `/device/update` cycle
- GitHub Actions CI on push
- `ruff` + `black` + pre-commit hook

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