# ResQNet

**ResQNet** (Response & Rescue Network) is a context-aware emergency alert system designed to provide fast, reliable, and independent emergency signaling using a combination of wearable-device triggers, motion context, and real-time tracking.

Unlike app-only SOS solutions that fail when a phone is lost, locked, or out of battery, ResQNet is built on a **fail-safe architecture** that can operate independently while still integrating with a smartphone when available.

---

## 🧠 Problem Statement

In emergency situations (assaults, accidents, medical distress, abduction risks), existing safety solutions face critical limitations:

* Dependence on smartphones (phone snatched, battery dead, no time to unlock)
* False triggers with no context
* Lack of real-time situational awareness for emergency contacts
* No clear indication of *what is happening* (walking, running, in a vehicle, stationary)

As a result, emergency contacts receive alerts without enough information to respond effectively.

---

## 🎯 Objective

ResQNet aims to build an **intelligent emergency response system** that:

* Can be triggered quickly under stress
* Works even when the user’s phone is unavailable
* Shares live location and motion context
* Provides continuous, real-time updates to trusted contacts
* Minimizes false alerts while maximizing reliability

---

## 🔑 Core Concept

ResQNet is centered around three layers:

```
[ Wearable / Device ]  →  [ ResQNet Backend ]  →  [ Web / App Dashboard ]
```

### Core Flow

```
Distress Trigger → Emergency Mode ON →
Live Location + Speed + Context Sent →
Backend Processing →
Emergency Contacts View Live Status
```

---

## 🚨 Key Features (Planned)

### 1. Manual Emergency Trigger

* Multi-press button activation (e.g., 5–10 rapid presses)
* Designed to prevent accidental activation
* Usable under high stress

### 2. Context-Aware Tracking

* Live GPS location updates
* Speed-based context detection:

  * Stationary
  * Walking
  * Running
  * Vehicle movement
* Sudden speed or motion spikes flagged as high-risk context

### 3. Phone-Linked but Not Phone-Dependent

* Bluetooth connection to smartphone when available
* Automatic fallback to independent connectivity (cellular/GPS)
* Emergency mode does not rely on phone availability

### 4. Real-Time Monitoring Dashboard

* Live map view of user location
* Movement status indicators
* Emergency state visibility
* Timeline of events

### 5. Expandable Architecture (Future)

* Audio and/or video capture during emergencies
* Fall detection
* Battery and device health monitoring
* Secure evidence logging

---

## 🧪 Development Approach

ResQNet follows a **software-first, hardware-later** strategy:

1. Simulate the wearable device using software
2. Build and validate backend logic
3. Develop live monitoring dashboard
4. Introduce physical hardware incrementally

This approach allows rapid iteration and validation before committing to hardware complexity.

---

## 🛠️ Tech Stack (Tentative)

### Backend

* Python (FastAPI / Flask)
* WebSockets for live updates
* SQLite / MongoDB (early-stage)

### Frontend

* Web dashboard (HTML/CSS/JS or React)
* Map integration (Leaflet / Google Maps)

### Device (Later Stage)

* ESP32 microcontroller
* GPS module
* Physical panic button
* Accelerometer / motion sensors

---

## 📌 Current Status

* Project conceptualized
* System architecture defined
* Development starting with:

  * Device simulator
  * Backend API
  * Emergency logic

---

## 🧭 Roadmap (High-Level)

1. Device Simulator (virtual wearable)
2. Backend emergency processing
3. Context classification logic
4. Live web dashboard
5. Hardware trigger integration
6. Advanced sensing & optimization

---

## ⚠️ Disclaimer

ResQNet is a **research and prototype project**. It is not intended to replace official emergency services. All emergency alerts are directed only to trusted contacts defined by the user.

---

## 👤 Author

Developed by **Aksh Kumar**
Undergraduate Computer Science Student

---

## 📄 License

This project is currently under development. Licensing details will be added as the project matures.
