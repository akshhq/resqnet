# ResQNet
## Response & Rescue Network

> *"The phone is the first thing that gets taken."*

---

## The Situation

Picture this.

Someone is being followed. They reach for their phone to call for help — but it's grabbed out of their hand. Or the battery is dead. Or they're in a state of panic and can't navigate a lock screen. Or they're forced at gunpoint to cancel the alert they just sent.

In every one of these scenarios, every existing SOS app fails completely.

This is not an edge case. It is the most common pattern in real assault and abduction situations. The tool people rely on most is the one most easily taken away.

---

## What's Actually Broken

Existing emergency systems — Google Emergency SOS, Apple Crash Detection, third-party SOS apps — were all built on the same flawed assumption:

**The user has calm, unobstructed access to their smartphone.**

They don't. And when they don't, four things break simultaneously:

---

### ① The Trigger Fails
Every SOS system today is smartphone-bound. No phone = no alert. There is no secondary trigger. No wearable fallback. No way to signal distress without a functioning device in hand.

---

### ② The Alert Is Useless
Even when the alert does fire, it sends a single GPS coordinate to a contact. That contact sees a pin on a map.

They have no idea:
- Is the person moving or stationary?
- Are they running away or being driven somewhere?
- Have they been unconscious for 30 seconds or 30 minutes?
- Is the situation getting worse?

A pin on a map is not situational awareness. It is noise.

---

### ③ Nothing Escalates
Once an SOS is sent, the system goes quiet. A person who triggered an alert 90 seconds ago and is now in critical danger looks exactly the same to their contact as someone who triggered it 5 seconds ago and is already safe. There is no automatic urgency increase. Responders have no way to prioritise.

---

### ④ The Alert Can Be Forced Off
This is the most dangerous failure. Every mainstream SOS system allows the user to cancel the alert at any time. In a coercion scenario, an attacker can simply force the victim to cancel. The contact receives silence and assumes everything is fine. It isn't.

---

## The Human Cost

These four failures compound. A responder who receives a static pin, with no context, no escalation, and no confidence the alert is still active — hesitates. That hesitation is measured in minutes. In real emergencies, minutes are the difference.

---

## What Needs to Exist

A system that works **when everything else has failed**. One that:

- Can be triggered **without touching a smartphone**
- Tells responders **what is actually happening**, not just where
- **Cannot be silently cancelled** — by the attacker or by accident
- **Gets louder automatically** if the danger doesn't stop
- Gives trusted contacts a **live operational picture**, not a notification

---

## How ResQNet Solves It

ResQNet is built around one principle: **the device is the trigger, the backend is the truth, and the dashboard is the eyes.**

---

### The Device
A wearable or clip-on device — currently simulated in software — carries a single physical panic button. One press. No phone needed. The signal goes directly to the backend over WiFi or cellular.

The device cannot be silenced by taking the phone. The emergency state lives on the server, not the handset.

---

### The Backend
When panic triggers, the backend **latches** the emergency state. It cannot be cleared by a network drop, an accidental tap, or a coerced cancel. Only an explicit, authorised reset signal clears it.

Every second, the backend receives a position update and does four things:

| Step | What happens |
|---|---|
| **Classify** | Speed → context: stationary / walking / running / vehicle |
| **Detect** | Sudden speed jump > 5 m/s → anomaly flag |
| **Assess** | Emergency + anomaly → risk level (normal / elevated / critical) |
| **Escalate** | 30s elapsed → escalated · 90s elapsed → critical |

All of this is broadcast in real time to every connected dashboard.

---

### The Dashboard
Trusted contacts don't get a notification. They get a live operations view:

- **Map** with a blinking red marker at the device's exact position
- **Movement trail** — the path taken in the 5 minutes before the panic trigger, plus live updates
- **Context label** — running, in a vehicle, stationary and unresponsive
- **Escalation state** — how long the emergency has been active and how serious it has become
- **Battery level** — so they know if contact is about to be lost
- **Timeline** — every event, timestamped, in sequence

The responder sees the full picture. They know whether to call the police, drive to a location, or wait for an update. They are not guessing.

---

## The Architecture

```
 [ Browser Simulator ]      [ Python Simulator ]
   (built into dashboard)     (optional, standalone)
          │                          │
          │   1 update / second, both
          ▼                          ▼
            [ FastAPI Backend ]
              ┌─────────────┐
              │  Classify   │  speed → context
              │  Detect     │  anomaly check
              │  Assess     │  risk level
              │  Escalate   │  30s / 90s thresholds
              └─────────────┘
                    │
                    │  WebSocket broadcast
                    ▼
            [ Live Dashboard ]
   (sidebar device list + log + live map —
    both simulators render side by side)
            │
            ▼
   [ Responder App — planned ]
   [ Command Center — planned ]
```

---

## What This Is Not

ResQNet is a **software-first prototype**. It demonstrates the complete architecture — trigger, classification, escalation, broadcast, dashboard — without requiring physical hardware.

Two independent simulators produce realistic human movement (heading-based, speed-smoothed, with GPS noise): one built directly into the dashboard for instant multi-device testing with zero setup, and one as a standalone Python script for scripted demos and interactive keypress control. Both can run **at the same time**, and the dashboard treats every device identically regardless of which simulator — or eventually, which real hardware — produced it.

It is not a finished product. It is proof that the architecture works, and a foundation to build on.

---

## What Gets Built Next

The prototype establishes the core. The roadmap builds the layers around it:

| Phase | What gets added / Status |
|---|---|
| **Notifications** | Email alerts to emergency contacts completed; SMS/WhatsApp planned |
| **User accounts** | Implemented (Firebase native Auth + Neon Postgres profile store) |
| **Responder dashboard** | Implemented (Time-limited magic links and live-tracking map) |
| **Command center** | Planned (Organizations monitoring hundreds of devices) |
| **Hardware** | ESP32 + GPS module + physical panic button + LiPo battery |
| **Auth + security** | Completed (optional API keys, WS token, rate limiting, device register check) |

---

## In One Sentence

ResQNet replaces the broken assumption that a person in danger has calm access to their phone — with a system that works precisely when they don't.

---

*ResQNet is a research prototype. It is not intended to replace official emergency services.*

**Author:** Aksh Kumar — Undergraduate Computer Science Student