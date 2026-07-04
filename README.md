# ResQNet — Response & Rescue Network

> A context-aware, device-independent emergency response system: real-time situational awareness, intelligent escalation, a Postgres-backed user/device/contacts system, and a responder dashboard that's paged in via magic link the instant an emergency starts.

---

## Table of Contents

1. [What It Does](#what-it-does)
2. [Architecture](#architecture)
3. [Project Structure](#project-structure)
4. [How to Run](#how-to-run)
5. [The Four Surfaces](#the-four-surfaces)
6. [Data Model](#data-model)
7. [Emergency Flow, End to End](#emergency-flow-end-to-end)
8. [API Reference](#api-reference)
9. [Google Apps Scripts](#google-apps-scripts)
10. [Environment Variables](#environment-variables)
11. [Security](#security)
12. [Simulator](#simulator)
13. [Design Principles](#design-principles)
14. [Current Status](#current-status)
15. [Known Gaps / Next Steps](#known-gaps--next-steps)
16. [Author](#author)

---

## What It Does

Most SOS systems assume you have calm, unobstructed access to your phone. ResQNet doesn't.

It's built around one premise: **the device triggers the alert, not the app.** Once an emergency is triggered, the system latches into emergency state, classifies movement context in real time, escalates automatically if danger persists, notifies the device owner's emergency contacts, and hands responders a live-tracking link — no login, no delay.

---

## Architecture

```
[ Trial Dashboard ]         [ User Dashboard ]           [ Responder Dashboard ]
  (in-browser sim,            (Firebase auth, devices,      (opened via magic link
   dev/demo tool)              contacts, incidents)          from an emergency email)
        │                            │                              ▲
        │  POST /device/update       │  POST/GET /user/*            │ ?uid=&token=
        ▼                            ▼                              │
              [ FastAPI Backend  (Render) ]  ───────────────────────┘
                    │        │                validates via
                    │        │
    WebSocket        │        └── on emergency start ──► [ Emergency Session
    broadcast         │                                     Token Apps Script ]
        │              │                                          │
        ▼              ▼                                          ├─► emails responders
[ live map on any   [ Neon Postgres ]                              └─► emails this user's
  connected           users / devices /                                emergency contacts
  dashboard ]         user_devices /
                       emergency_contacts /
                       incidents / email_queue
```

### Core data flow (per device tick)

```
Device sends /device/update (1 Hz)
    → context classified   (stationary / walking / running / vehicle)
    → speed anomaly checked   (Δspeed > 5 m/s = elevated)
    → risk calculated   (normal / elevated / critical)
    → escalation checked   (30s → escalated, 90s → critical)
    → Postgres circular-buffer log updated (500-row cap per device)
    → broadcast to all connected WebSocket clients

If emergency just started this tick:
    → emergency Postgres log table opened (pre-seeded with 5 min of history)
    → owning user looked up via user_devices
    → Emergency Session Token Apps Script called (action=trigger)
         → emails fixed responder list + this user's emergency contacts
           a magic link: responder_dashboard?uid=&token=
    → incidents row created, storing the returned token

If the device later resets:
    → emergency log closed, incidents row marked resolved
    → responder dashboard (if a magic-link session is open) auto-calls
      the Apps Script's action=resolve AND the backend's
      /user/incidents/resolve-by-token, killing the link on both sides
```

---

## Project Structure

```
resqnet/
├── index.html                      # Landing page — links to all 3 dashboards + API docs
├── backend/
│   ├── app/
│   │   ├── main.py                 # FastAPI app: device ingestion, WS broadcast, emergency trigger
│   │   ├── user_routes.py          # /user/* — register/login/contacts/devices/preferences/incidents
│   │   ├── user_db.py              # Postgres layer: users, devices, user_devices, emergency_contacts,
│   │   │                           #   user_preferences, incidents, email_queue
│   │   ├── email_queue_routes.py   # /email-queue/* — polled by a Phase-4 Apps Script (not live yet)
│   │   ├── db.py                   # Per-device circular-buffer + emergency log tables
│   │   ├── auth.py                 # Optional API key + WS token validation, rate limit handler
│   │   ├── context.py              # Speed classification, risk, escalation
│   │   ├── schemas.py              # Pydantic request models
│   │   ├── storage.py              # In-memory live state (device_state, history, registered_devices)
│   │   └── websocket.py            # ConnectionManager
│   ├── requirements.txt
│   ├── env.example                 # Copy to .env — documents every backend variable
│   └── .env                        # (gitignored) local secrets
├── apps_script/
│   └── Emergency_Session_Backend.gs     # Responder magic links + emergency-contact alerts
├── frontend/
│   ├── .env                        # Frontend source of truth: Firebase + public backend URLs
│   ├── generate-config.js          # Builds dashboard config.js files from frontend/.env
│   ├── user_dashboard/             # Firebase auth / devices / contacts / incidents
│   │   ├── index.html
│   │   ├── app.js                  # Firebase Auth + talks to FastAPI /user/*
│   │   └── style.css
│   └── responder_dashboard/        # Opened via emergency magic link
│       ├── index.html
│       ├── script.js               # Live map / WS feed / timeline (the "real" dashboard logic)
│       ├── responder-dashboard-frontend.js  # Validates ?uid=&token= against the Apps Script
│       └── style.css
├── Trial_Dashboard/                 # Dev/demo tool — in-browser simulator + live map, unchanged
│   ├── index.html
│   ├── style.css
│   └── script.js
├── simulator/
│   ├── simulator.py                 # Standalone interactive + demo Python simulator
│   ├── seed_simulator_user.py       # Creates a real "Simulator" user + device via the API
│   └── send_updates.py              # Minimal one-shot update script
├── EMAIL_QUEUE_INTEGRATION.md       # HTTP contract for the Phase-4 email-sending Apps Script
├── start.bat / start.sh
└── LICENSE
```

---

## How to Run

### Prerequisites
- Python 3.11+
- `pip install -r backend/requirements.txt`
- A Neon Postgres database (free tier is fine) — required for the User Dashboard, Responder Dashboard, and email queue. The Trial Dashboard works without it.
- Both Apps Scripts deployed as Web Apps (see [Google Apps Scripts](#google-apps-scripts))

### Configure
```bash
cp backend/env.example backend/.env
# then fill in DATABASE_URL, SESSION_TOKEN_WEBAPP_URL, and optionally API_KEY
# also fill in frontend/.env with the Firebase + public URL values
node frontend/generate-config.js
```
The frontend `.env` file is the source of truth for the dashboard URLs and Firebase settings. `frontend/generate-config.js` reads it and writes the generated `config.js` files used by the dashboards.

### One-command startup
```bash
start.bat        # Windows
./start.sh       # macOS / Linux
```
This starts the backend, serves the Trial Dashboard, and opens it in your browser. On startup, the backend also creates every Postgres table it needs (`users`, `devices`, `user_devices`, `emergency_contacts`, `user_preferences`, `incidents`, `email_queue`, plus the per-device log tables) if they don't already exist.

### Manual startup
```bash
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
```bash
node frontend/generate-config.js   # regenerate dashboard config.js files after any .env change
cd Trial_Dashboard && python -m http.server 5500      # Trial Dashboard
cd frontend/user_dashboard && python -m http.server 5501
cd frontend/responder_dashboard && python -m http.server 5502
```

### Seed a demo account
```bash
python simulator/seed_simulator_user.py --url https://resqnet-gti8.onrender.com --key <API_KEY>
# prints a device_id — feed it straight into the simulator:
python simulator/simulator.py --demo --id <device_id> --url https://resqnet-gti8.onrender.com/device/update --key <API_KEY>
```
Log into the User Dashboard with `simulator@resqnet.demo` / `SimulatorDemo123!` to watch that account's device, incidents, and contacts update live.

### Run Integration Tests
To validate the backend API endpoints, user/device registrations, and emergency flow against your database:
1. Ensure the local backend server is running (`uvicorn app.main:app` on port 8000).
2. Run the integration test suite:
   ```bash
   python scratch/test_user_flow.py
   ```
   This script registers a temporary test user, signs up a test device, creates contacts, fires a location update with `emergency=True` to create an incident, and then clears it using `reset=True` to verify the resolution sequence.

---

## The Four Surfaces

### 1. Landing page (`index.html`)
Links out to the Trial Dashboard, User Dashboard, Responder Dashboard, the GitHub repo, and the live API docs (`/docs`). No backend calls of its own.

### 2. Trial Dashboard
The original dev/demo tool — an in-browser device simulator plus live map, entirely self-contained. Unchanged by the User/Responder Dashboard work; still the fastest way to see the core context/risk/escalation logic without touching Postgres or Apps Script at all.

### 3. User Dashboard
Where a real person manages their account:
- **Register & Login** — Handled natively by Firebase Auth (email verification & passwords). The backend just creates a relational record upon signup.
- **Devices** — add a device (generates a `device_id`, provisions its log table), see battery/status/last-seen live.
- **Emergency contacts** — up to 3, email-first, priority-ordered.
- **Incidents** — history of past emergencies for this account.
- **Voice Monitoring** — placeholder card, disabled. Reserves the UI slot for a future device-mic feature; does nothing yet.
- The dashboard now loads directly from the network. The service-worker cache layer was removed so deployed HTML/CSS/JS changes show up immediately instead of being held behind stale cached assets.

### 4. Responder Dashboard
Never logged into directly — it's opened via the link an emergency email contains (`?uid=&token=`). On load, it validates that link against the Emergency Session Token Apps Script, then drives the exact same live map / WebSocket feed / timeline UI the Trial Dashboard uses for manual device IDs. When the device resets, the link is killed automatically on both the Apps Script side and in Postgres.

---

## Data Model

Four core Postgres tables, plus supporting ones:

| Table | Purpose |
|---|---|
| `users` | One row per person. `user_id` = `firstname_lastname_phonenumber` (e.g. `aksh_kumar_9876543210`). |
| `devices` | One row per physical device. **No owner column** — ownership lives in `user_devices`. |
| `user_devices` | Relation table: which user owns which device. |
| `emergency_contacts` | Up to 3 per user, email-first, unique priority (1–3). |
| `user_preferences` | Notification toggles + quiet hours, one row per user. |
| `incidents` | One row per emergency event — links `device_id`, `user_id`, the responder token, and start/end times. |
| `email_queue` | Outbound email jobs for Phase-4 features, polled by a separate (not-yet-built) Apps Script — see `EMAIL_QUEUE_INTEGRATION.md`. |

Plus, per device, `db.py` maintains its own circular-buffer log table (`device_<id>`, capped at 500 rows) and one emergency-log table per incident (`<device_id>_EM<timestamp>`, uncapped, permanent).

---

## Emergency Flow, End to End

1. Device (or simulator) sends `emergency: true` on `/device/update`.
2. Backend opens an emergency log table in Postgres, looks up the owning user via `user_devices`.
3. Backend calls the **Emergency Session Token Apps Script** (`action=trigger`) with the user's name, device ID, coordinates, and their emergency-contact email list.
4. That script generates a 6-character token, emails the fixed responder list a full incident email, emails every emergency contact a shorter personal-alert email — **same link** for both — and returns the token.
5. Backend stores that token on a new `incidents` row.
6. Whoever opens the link lands on the Responder Dashboard, which validates `?uid=&token=` against the Apps Script, then loads the live map for that device.
7. When the device sends `reset: true`, the responder page auto-resolves the session on both the Apps Script (kills the link) and the backend (`/user/incidents/resolve-by-token`, closes the Postgres row).

---

## API Reference

### Device ingestion (Trial Dashboard + simulators + real devices)
| Endpoint | Purpose |
|---|---|
| `POST /device/register` | Register a device ID before it can send updates |
| `POST /device/update` | Position + state update (1 Hz) — triggers emergency notification logic |
| `GET /device/{id}` | Latest state |
| `GET /device/{id}/history` | Last 1,000 entries |
| `GET /device/registered` | List all registered device IDs |
| `WS /ws/live` | Live broadcast feed — `?token=<api_key>` if auth enabled |

### User Dashboard (`/user/*`, all require `X-API-Key` if auth is enabled)
| Endpoint | Purpose |
|---|---|
| `POST /user/register` | Create account — links Firebase UID to Postgres profile |
| `GET /user/{user_id}` | Full profile: user + contacts + devices + preferences |
| `POST/GET /user/{user_id}/contacts` | Add / list emergency contacts |
| `PATCH/DELETE /user/contacts/{id}` | Edit / remove a contact |
| `POST /user/devices/register` | Register a device under a user |
| `GET /user/{user_id}/devices` | List a user's devices |
| `DELETE /user/devices/{device_id}` | Remove a device |
| `GET/PATCH /user/{user_id}/preferences` | Notification preferences |
| `GET /user/{user_id}/incidents` | Incident history |
| `POST /user/incidents/resolve-by-token` | Called by the responder dashboard on auto-resolve — no API key, token is the credential |

### Email queue (`/email-queue/*`, Phase 4 — not live yet)
See `EMAIL_QUEUE_INTEGRATION.md`.

---

## Google Apps Scripts

Both live in `apps_script/` as version-controlled reference source — the actual deployment happens in the Apps Script editor, and the `/exec` URL goes into `backend/.env`.

### `Emergency_Session_Backend.gs`
Called **by the backend**, server-to-server, the instant an emergency starts. Generates a 6-character responder token, emails the fixed `RESPONDER_EMAILS` list *and* every address passed in `contactEmails` (the triggering user's emergency contacts), and exposes `action=validate` / `action=resolve` for the Responder Dashboard.

> **Before this works in production:** update `RESPONDER_EMAILS` in the deployed script to real addresses, and make sure `DASHBOARD_BASE_URL` points at your actual responder dashboard URL.

---

## Environment Variables

See `backend/env.example` for the authoritative, commented list. Summary:

| Variable | Required for | Notes |
|---|---|---|
| `API_KEY` | Optional auth | Blank = auth disabled (dev mode) |
| `CORS_ORIGINS` | All dashboards | Comma-separated, no trailing slash |
| `DATABASE_URL` | User/Responder Dashboards | Neon connection string; blank = Postgres features disabled |
| `SESSION_TOKEN_WEBAPP_URL` | Responder alerts | The Emergency Session Token Apps Script's `/exec` URL |

The OTP Apps Script URL is **not** a backend env var — it's configured client-side in `frontend/user_dashboard/otp-frontend.js` (`RESQNET_WEB_APP_URL`), since the backend is never part of that exchange.

---

## Security

Authentication is **optional and off by default**.

1. Copy `backend/env.example` to `backend/.env`
2. Generate a key: `python -c "import secrets; print(secrets.token_hex(32))"`
3. Set `API_KEY=<key>` and restart — the startup banner confirms it's enabled

Once enabled: all HTTP endpoints require `X-API-Key`, WebSocket connections require `?token=<key>`, and the simulator/dashboards pick it up automatically from `.env` or a `--key` flag / UI field.

The one deliberate exception is `POST /user/incidents/resolve-by-token` — the Responder Dashboard has no login, so the (already Apps-Script-validated) token itself is the credential there, same pattern as the Apps Script endpoints.

Other protections: rate limiting via `slowapi` (60/min on `/device/update`, 30/min on `/device/history`), mandatory device registration before `/device/update` accepts anything, Pydantic field validation on every request body, passwords hashed with PBKDF2-HMAC-SHA256 (200,000 iterations, per-user salt) as a stopgap until Firebase owns login.

---

## Simulator

```bash
python simulator/seed_simulator_user.py --url https://resqnet-gti8.onrender.com --uid #
python simulator/simulator.py --demo --id # --url https://resqnet-gti8.onrender.com/device/update
# OR
python seed_simulator_user.py --url http://127.0.0.1:8000 --uid #
python simulator.py --demo --id # --url http://127.0.0.1:8000/device/update
```

Interactive keys: `p` panic · `r` reset · `0`–`3` mode · `t` sharp turn · `q` quit.

---

## Design Principles

- **Backend is the single source of truth** — every dashboard reflects backend state, never manages its own.
- **Emergency is latched** — cannot be silently cancelled; only an explicit `reset: true` clears it.
- **Ownership lives in a relation table**, not a foreign key on `devices` — keeps the schema honest about the fact that device ownership is a *fact about a relationship*, not a property of the device itself.
- **Two independent systems close an incident** — the Apps Script Sheet and the Postgres `incidents` row don't share a database, so both close paths are called explicitly rather than assumed to stay in sync.
- **Auth is additive, not required** — the system is fully usable with zero config, and becomes secure the moment a key is configured.
- **The dashboard doesn't care where a device comes from** — Trial Dashboard, Python simulator, or real hardware all look identical once they reach the backend.

---

## Current Status

| Area | Status |
|---|---|
| Backend core (context, risk, escalation) | ✅ Stable |
| WebSocket broadcast | ✅ Working |
| Optional API key auth + rate limiting | ✅ Working |
| Trial Dashboard (in-browser sim + map) | ✅ Working, unchanged |
| User Dashboard (register/login/devices/contacts/incidents) | ✅ Wired end-to-end |
| User Dashboard cache/service worker layer | ✅ Removed — live deploys now show current files immediately |
| Responder Dashboard (magic link → live map) | ✅ Wired end-to-end |
| Postgres schema (4 core tables + supporting) | ✅ Implemented |
| OTP registration Apps Script | ✅ Working, versioned in `apps_script/` |
| Emergency session-token Apps Script + contact alerts | ✅ Working, versioned in `apps_script/` |
| Simulator demo account (`seed_simulator_user.py`) | ✅ Working |
| Voice monitoring | 🚧 Placeholder UI only — not wired to any mic |
| Email queue (Phase 4) | 🚧 Schema + polling contract exist, nothing enqueues into it yet |
| Persistent live state (device_state/history) | ❌ Still in-memory, resets on backend restart |
| Automated tests / CI | ✅ E2E Integration test suite complete |

---

## Known Gaps / Next Steps

- Replace remaining `print()` calls with `logging`
- Unit tests for `context.py` (risk scoring / escalation) — highest value, lowest effort, given this logic has already had one real bug
- GitHub Actions CI
- API versioning (`/api/v1/`)
- SMS notifications (Twilio or Fast2SMS) alongside email
- RBAC design before building any Operations/Admin dashboard
- Persistent storage for `device_state`/`device_history` (currently wiped on every backend restart)
- Real device-mic streaming behind the Voice Monitoring placeholder, with explicit per-device consent
- User Dashboard service-worker caching removed so the live site reflects the latest frontend structure without a manual cache clear

---

## Author

**Aksh Kumar**
Undergraduate Computer Science Student

---

## License

MIT

> ResQNet is not intended to replace official emergency services. It is a research prototype.