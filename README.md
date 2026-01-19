# ResQNet – Response & Rescue Network

## Overview

**ResQNet (Response & Rescue Network)** is a context-aware emergency response system designed to provide **fast, reliable distress alerts with real-time situational awareness**. Unlike app-only SOS solutions, ResQNet is designed with a *device-first mindset* and continues to function even when a smartphone is unavailable, inaccessible, or compromised.

The system combines:

* A simulated wearable/device trigger
* Intelligent movement & risk analysis
* Time-based alert escalation
* A live monitoring dashboard for trusted observers

ResQNet is built as a **software-first prototype**, making it ideal for academic projects, hackathons, and research demonstrations.

---

## Problem Statement

In real-world emergency situations (assault, abduction risk, medical distress, accidents):

* Phones may be lost, locked, snatched, or out of battery
* Existing SOS systems often send only location, without context
* Emergency contacts lack real-time visibility into what is happening
* False triggers and alert spam reduce trust

These limitations lead to **delayed or ineffective responses** during critical moments.

---

## Objective

ResQNet aims to build a **fail-safe, intelligent emergency response system** that:

* Can be triggered quickly under stress
* Locks emergency state to prevent forced cancellation
* Allows only explicit, controlled reset
* Shares live location and motion context
* Escalates alerts if danger persists
* Provides real-time monitoring via a dashboard

---

## System Architecture

```
[ Device / Simulator ]
        ↓
[ FastAPI Backend ]
        ↓ (WebSocket)
[ Live Web Dashboard ]
```

### Core Flow

```
Panic Trigger → Emergency Locked
        ↓
Context + Speed + Location Analysis
        ↓
Risk & Alert Evaluation
        ↓
Time-based Escalation (30s / 90s)
        ↓
Live Broadcast to Dashboard
```

---

## Key Features

### 1. Emergency Trigger & Lock

* Panic trigger activates emergency mode
* Emergency state is **latched** (cannot be auto-cancelled)
* Reset requires an **explicit reset signal**

### 2. Context-Aware Tracking

* Live GPS location
* Speed-based context classification:

  * Stationary
  * Walking
  * Running
  * Vehicle
* Sudden speed anomalies flagged as high-risk

### 3. Risk Assessment

* Combines emergency intent and anomalies
* Risk levels:

  * Normal
  * Elevated
  * Critical

### 4. Alert System

* Alerts triggered on risk escalation
* Cooldown prevents alert spam
* Console + dashboard alerts (prototype stage)

### 5. Time-Based Escalation

* Emergency persistence escalates severity:

  * After 30 seconds → Escalated
  * After 90 seconds → Critical
* Escalation resets automatically on panic reset

### 6. Real-Time Dashboard

* Live map visualization (Leaflet)
* Marker color reflects state:

  * Green → Normal
  * Red → Emergency
* Timeline of events
* Replay of movement history

### 7. Device Simulator

* Software-based wearable simulation
* Auto movement updates every second
* Modes: walking, running, vehicle
* Keyboard controls:

  * `p` → Panic
  * `r` → Reset
  * `1/2/3` → Movement modes

---

## Tech Stack

### Backend

* Python
* FastAPI
* WebSockets
* In-memory state management (prototype)

### Frontend

* HTML / JavaScript
* Leaflet.js for maps
* WebSocket client

### Simulator

* Python
* Requests
* Multi-threaded input & send loops

---

## How to Run

### 1. Backend

```bash
cd backend
venv\Scripts\activate
python -m uvicorn app.main:app
```

### 2. Dashboard

```bash
cd dashboard
python -m http.server 5500
```

Open: `http://localhost:5500/index.html`

### 3. Simulator

```bash
cd simulator
python simulator.py
```

---

## Demo Flow

1. Start backend, dashboard, simulator
2. Device starts in **normal (green)** state
3. Press `p` → emergency triggers → marker turns **red**
4. Wait 30s → escalation alert
5. Wait 90s → critical escalation
6. Press `r` → emergency reset → marker returns **green**

---

## Design Principles

* **Backend is the single source of truth**
* **Emergency is latched** to prevent malicious cancellation
* **Reset is explicit and authoritative**
* **Escalation is event-driven**, not timer-based
* **Frontend is stateless**, reflects latest backend state only

---

## Current Status

* Fully functional prototype
* Stable cold start
* End-to-end tested with simulator

---

## Future Enhancements

* Persistent storage (SQLite / MongoDB)
* SMS / Email / Push notifications
* Sound & visual alert enhancements
* Multi-device & trusted contacts
* Hardware integration (ESP32 + GPS + button)

---

## Intended Use

* Academic projects
* Hackathons
* System design demonstrations
* Research prototyping

**ResQNet is not intended to replace official emergency services.**

---

## Author

**Aksh Kumar**
Undergraduate Computer Science Student

---

## License

MIT (or specify if different)
