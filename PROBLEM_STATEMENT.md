# ResQNet — Problem Statement
### Response & Rescue Network

---

## Domain

**Personal Safety & Emergency Response Systems**
**Type:** Context-Aware IoT / Software Prototype

---

## Background

Every year, millions of people across the world find themselves in life-threatening situations — assaults, accidents, medical episodes, or abductions — where seconds determine survival. The most common emergency tool available is a smartphone. Yet the smartphone itself is frequently the first casualty in an emergency: it gets snatched, runs out of battery, gets locked by an attacker, or simply isn't reachable when panic sets in.

Existing SOS applications assume the user has calm, unobstructed access to their phone. They also send nothing more than a static location pin — stripping away all the contextual information (is the person moving? accelerating? stationary and unresponsive?) that a first responder or trusted contact desperately needs to act effectively.

---

## The Core Problem

Current personal emergency systems fail at the moment they are needed most, in **four compounding ways**:

### 1. Device Dependency Without Fallback
All mainstream SOS tools are smartphone-bound. When a phone is seized, damaged, dead, or simply out of reach, the person in distress has no alternative channel to signal for help.

### 2. Context Blindness
Existing systems transmit only a location coordinate. They provide no insight into what is actually happening — whether the victim is running, being transported in a vehicle, unconscious and stationary, or in a rapidly evolving situation. Emergency contacts receive a pin on a map with no ability to assess severity.

### 3. No Intelligent Escalation
Once an SOS is sent, most systems go silent. There is no mechanism to distinguish a quickly-resolved situation from one that is worsening over time. A person who remains in distress for 90 seconds receives the same alert weight as one who triggered SOS by mistake and resolved it in seconds.

### 4. Vulnerability to Coercion and False Resets
Conventional SOS systems allow the user to cancel an alert at any time. This is exploited in coercion scenarios — an attacker can force the victim to cancel the alert. There is no way for emergency contacts to know whether a cancellation was voluntary or coerced.

---

## Impact

These gaps produce a dangerous outcome: **emergency contacts are either uninformed, misinformed, or too late to respond.** The person in distress is effectively isolated at the exact moment they need the most support.

In a country like India, where personal safety infrastructure is uneven and response times vary drastically by region, this problem is especially acute for individuals who are alone, in transit, or in low-connectivity areas.

---

## What's Missing

There is no lightweight, software-demonstrable system that:

- Works **independently of constant smartphone interaction**
- Provides **real-time motion and location context**, not just a static pin
- **Locks emergency state** against coerced cancellation
- Escalates alerts **intelligently over time** if danger persists
- Gives trusted contacts a **live situational dashboard**, not just a notification

---

## Solution Scope — ResQNet

ResQNet addresses this gap by building a **context-aware, latch-based emergency response prototype** that:

- Accepts a panic trigger from a simulated wearable device (no smartphone interaction required)
- Analyzes real-time speed and movement to classify situational risk
- Locks the emergency state so it cannot be silently dismissed
- Escalates severity automatically at **30s** and **90s** thresholds
- Streams live location, motion context, and event history to a monitoring dashboard over WebSocket

The system is implemented as a software prototype using **Python (FastAPI)**, **WebSockets**, and a **Leaflet.js dashboard** — demonstrating the full architecture without requiring physical hardware.

---

## Success Criteria

ResQNet is considered successful if it can demonstrate, end-to-end:

| # | Criterion |
|---|-----------|
| 1 | Panic trigger → immediate emergency lock |
| 2 | Live context (speed, movement mode, GPS) visible on dashboard within 1 second |
| 3 | Time-based escalation firing accurately at 30s and 90s |
| 4 | Explicit-only reset restoring normal state |
| 5 | No false auto-cancellations under any simulated scenario |

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| Backend | Python, FastAPI, WebSockets |
| Frontend | HTML, JavaScript, Leaflet.js |
| Simulator | Python (multi-threaded) |
| State | In-memory (prototype stage) |

---

> **Disclaimer:** ResQNet is not intended to replace official emergency services. It is a research prototype demonstrating an architectural approach to smarter, device-independent personal safety systems.

---

*Author: Aksh Kumar — Undergraduate Computer Science Student*
