// ─────────────────────────────────────────────────────────────────────────────
// ResQNet — Responder Dashboard
// Wired to real backend endpoints. Stubs marked clearly with [STUB].
// ─────────────────────────────────────────────────────────────────────────────

const BACKEND = "https://resqnet-gti8.onrender.com";
const WS_URL  = window.RESQNET_WS_URL || "wss://resqnet-gti8.onrender.com/ws/live";

// ── Auth ──────────────────────────────────────────────────────────────────────
function _getApiKey() {
  return window.RESQNET_API_KEY || sessionStorage.getItem("resqnet_api_key") || "";
}
function _authHeaders() {
  const k = _getApiKey();
  return k ? { "X-API-Key": k, "Content-Type": "application/json" }
           : { "Content-Type": "application/json" };
}

// ─────────────────────────────────────────────────────────────────────────────
// State
// ─────────────────────────────────────────────────────────────────────────────
let deviceId       = null;   // the device we're watching
let emergencyStart = null;   // unix ts when emergency first detected
let incidentStart  = null;   // unix ts when we started watching (page load)
let lastPayload    = null;   // most recent broadcast for this device
let lastUpdateTs   = null;   // wall time of last received update
let onMyWayActive  = false;
let alertCount     = 0;
let escalationCount= 0;
let totalDistance  = 0;      // metres
let prevLat        = null;
let prevLng        = null;
let staleTimer     = null;   // timeout to mark connection stale
let durationTimer  = null;

// ── Session expiry ──────────────────────────────────────────────────────
// [REAL BACKEND NEEDED]: in production, the responder link itself is a
// signed, time-limited token (?incident=INC-xxx&token=xxx) that the SERVER
// rejects once expired. This client-side timer approximates that behaviour
// for now — it stops rendering live data 30 minutes after reset, but it is
// NOT a security boundary. Anyone with the raw device_id can still query
// the backend directly until server-side token expiry exists.
const SESSION_GRACE_AFTER_RESET_MS = 30 * 60 * 1000; // 30 minutes
let sessionExpiresAt = null;   // epoch ms, set once reset is observed
let sessionTimer     = null;
let sessionExpired    = false;

// Timeline entries array — newest first
const timelineEntries = [];

// ─────────────────────────────────────────────────────────────────────────────
// Map setup
// ─────────────────────────────────────────────────────────────────────────────
const map = L.map("map", { zoomControl: false }).setView([28.6139, 77.2090], 15);
L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
  attribution: "© OpenStreetMap © CARTO",
  maxZoom: 19,
}).addTo(map);
L.control.zoom({ position: "topleft" }).addTo(map);

let deviceMarker = null;
let trailPolyline = null;
const trailCoords = [];      // [lat, lng] history for the polyline

function makeMarkerIcon(emergency) {
  const div = document.createElement("div");
  div.className = emergency ? "emergency-pulse" : "normal-dot";
  return L.divIcon({ html: div, iconSize: [16, 16], iconAnchor: [8, 8], className: "" });
}

function updateMap(lat, lng, emergency) {
  const latlng = [lat, lng];

  if (!deviceMarker) {
    deviceMarker = L.marker(latlng, { icon: makeMarkerIcon(emergency) }).addTo(map);
  } else {
    deviceMarker.setLatLng(latlng);
    deviceMarker.setIcon(makeMarkerIcon(emergency));
  }

  // Trail — colour shifts with risk
  trailCoords.push(latlng);
  if (trailCoords.length > 500) trailCoords.shift();

  const trailColor = emergency ? "#ef4444" : "#3b82f6";
  if (!trailPolyline) {
    trailPolyline = L.polyline(trailCoords, {
      color: trailColor, weight: 2.5, opacity: 0.7,
    }).addTo(map);
  } else {
    trailPolyline.setLatLngs(trailCoords);
    trailPolyline.setStyle({ color: trailColor });
  }
}

function recenterMap() {
  if (lastPayload) map.setView([lastPayload.latitude, lastPayload.longitude], 16);
}

function openGoogleMaps() {
  if (!lastPayload) return;
  const { latitude: lat, longitude: lng } = lastPayload;
  window.open(`https://www.google.com/maps?q=${lat},${lng}`, "_blank");
}

// ─────────────────────────────────────────────────────────────────────────────
// Distance helper (Haversine, returns metres)
// ─────────────────────────────────────────────────────────────────────────────
function haversine(lat1, lng1, lat2, lng2) {
  const R = 6371000;
  const dLat = (lat2 - lat1) * Math.PI / 180;
  const dLng = (lng2 - lng1) * Math.PI / 180;
  const a = Math.sin(dLat / 2) ** 2 +
            Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
            Math.sin(dLng / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

// ─────────────────────────────────────────────────────────────────────────────
// Duration timer
// ─────────────────────────────────────────────────────────────────────────────
function formatDuration(secs) {
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = secs % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

function startDurationTimer() {
  clearInterval(durationTimer);
  durationTimer = setInterval(() => {
    if (!incidentStart) return;
    const elapsed = Math.floor(Date.now() / 1000 - incidentStart);
    document.getElementById("header-duration").textContent = formatDuration(elapsed);
    document.getElementById("st-duration").textContent     = formatDuration(elapsed);

    if (emergencyStart) {
      const emElapsed = Math.floor(Date.now() / 1000 - emergencyStart);
      document.getElementById("banner-sub").textContent =
        `Emergency active · ${formatDuration(emElapsed)}`;
    }
  }, 1000);
}

// ─────────────────────────────────────────────────────────────────────────────
// Session expiry — 30 minutes after the incident is reset
// ─────────────────────────────────────────────────────────────────────────────
function armSessionExpiry() {
  if (sessionExpiresAt) return;   // already armed, don't restart the clock
  sessionExpiresAt = Date.now() + SESSION_GRACE_AFTER_RESET_MS;

  const badge = document.getElementById("session-meta");
  badge.classList.add("session-warning");

  clearInterval(sessionTimer);
  sessionTimer = setInterval(updateSessionCountdown, 1000);
  updateSessionCountdown();
}

function updateSessionCountdown() {
  if (!sessionExpiresAt) return;
  const remainingMs = sessionExpiresAt - Date.now();

  if (remainingMs <= 0) {
    expireSession();
    return;
  }

  const mins = Math.floor(remainingMs / 60000);
  const secs = Math.floor((remainingMs % 60000) / 1000);
  document.getElementById("session-expiry").textContent =
    `${mins}:${String(secs).padStart(2, "0")}`;
}

function expireSession() {
  if (sessionExpired) return;
  sessionExpired = true;

  clearInterval(sessionTimer);
  clearInterval(durationTimer);
  clearTimeout(staleTimer);

  document.getElementById("session-expiry").textContent = "Expired";

  // Close the live connection — no more updates should render
  if (ws) { ws.onclose = null; ws.close(); }

  document.getElementById("session-expired-overlay").hidden = false;
  addTimelineEntry("🔒 Session expired — 30 min after resolution", "amber");
}

// ─────────────────────────────────────────────────────────────────────────────
// Update all UI from a payload broadcast
// ─────────────────────────────────────────────────────────────────────────────
function applyPayload(data) {
  if (sessionExpired) return;   // session closed — ignore any further data

  lastPayload = data;
  lastUpdateTs = Date.now();

  // ── Distance accumulation ──
  if (prevLat !== null) {
    const d = haversine(prevLat, prevLng, data.latitude, data.longitude);
    totalDistance += d;
    const distStr = totalDistance >= 1000
      ? `${(totalDistance / 1000).toFixed(2)} km`
      : `${Math.round(totalDistance)} m`;
    document.getElementById("st-distance").textContent = distStr;
  }
  prevLat = data.latitude;
  prevLng = data.longitude;

  // ── Map ──
  updateMap(data.latitude, data.longitude, data.emergency);

  // ── Device info panel ──
  const bat = Math.round(data.battery);
  document.getElementById("inf-battery").textContent  = `${bat}%`;
  document.getElementById("inf-speed").textContent    = `${data.speed.toFixed(2)} m/s`;
  document.getElementById("inf-context").textContent  = capitalize(data.context);
  document.getElementById("inf-lat").textContent      = data.latitude.toFixed(6);
  document.getElementById("inf-lng").textContent      = data.longitude.toFixed(6);
  document.getElementById("inf-lastseen").textContent = new Date().toLocaleTimeString();

  const riskEl = document.getElementById("inf-risk");
  riskEl.textContent = capitalize(data.risk);
  riskEl.className = `iv risk-${data.risk}`;

  // Battery bar
  const barEl = document.getElementById("battery-bar");
  barEl.style.width = `${bat}%`;
  barEl.style.background = bat > 50 ? "var(--green)" : bat > 20 ? "var(--amber)" : "var(--red)";

  // ── Banner ──
  updateBanner(data);

  // ── Alert ring ──
  updateAlertRing(data.risk);

  // ── Map info pill ──
  document.getElementById("map-speed-label").textContent  = `${data.speed.toFixed(1)} m/s`;
  document.getElementById("map-context-label").textContent = capitalize(data.context);

  // ── Banner stats ──
  document.getElementById("banner-risk").textContent    = capitalize(data.risk);
  document.getElementById("banner-context").textContent = capitalize(data.context);
  document.getElementById("banner-speed").textContent   = `${data.speed.toFixed(1)} m/s`;

  // ── Counters ──
  if (data.alert) {
    alertCount++;
    document.getElementById("st-alerts").textContent = alertCount;
  }
  if (data.escalation) {
    escalationCount++;
    document.getElementById("st-escalations").textContent = escalationCount;
  }

  // ── Connection chain — device + cloud are OK since we just got a message ──
  setChainNode("cn-device", "ok");
  setChainNode("cn-cloud",  "ok");
  setChainNode("cn-dashboard", "ok");
  document.getElementById("conn-stale-msg").hidden = true;

  // ── Start stale timer — if no update in 8s mark chain uncertain ──
  clearTimeout(staleTimer);
  staleTimer = setTimeout(markStale, 8000);

  // ── Reset state ──
  if (data.reset) {
    addTimelineEntry("Incident resolved — device reset", "green");
    document.body.classList.add("resolved");
    updateBannerResolved();
    armSessionExpiry();   // starts the 30-minute countdown to session close
  }

  // ── Emergency start ──
  if (data.emergency && emergencyStart === null) {
    emergencyStart = data.timestamp;
    addTimelineEntry("🚨 Emergency triggered", "red");
    playAlert();
  }

  // ── Escalation ──
  if (data.escalation) {
    addTimelineEntry(`⚡ Escalation: ${data.escalation.toUpperCase()}`, "amber");
    playAlert();
  }

  // ── Alert ──
  if (data.alert && !data.escalation) {
    addTimelineEntry(`⚠ Risk alert: ${capitalize(data.risk)}`, "amber");
  }

  // ── Periodic movement entries (every 10 ticks roughly) ──
  if (timelineEntries.length === 0 ||
      (timelineEntries.length % 10 === 0 && data.context)) {
    addTimelineEntry(`${capitalize(data.context)} · ${data.speed.toFixed(1)} m/s`, "blue");
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Banner
// ─────────────────────────────────────────────────────────────────────────────
function updateBanner(data) {
  const banner = document.getElementById("emergency-banner");
  const title  = document.getElementById("banner-title");
  const icon   = document.getElementById("banner-icon");

  banner.classList.remove("banner-normal", "banner-elevated", "banner-critical");

  if (data.emergency && data.risk === "critical") {
    banner.classList.add("banner-critical");
    icon.textContent  = "🆘";
    title.textContent = "CRITICAL EMERGENCY";
  } else if (data.emergency || data.risk === "elevated") {
    banner.classList.add("banner-elevated");
    icon.textContent  = "⚠️";
    title.textContent = data.emergency ? "Emergency Active" : "Elevated Risk";
  } else {
    banner.classList.add("banner-normal");
    icon.textContent  = "🟢";
    title.textContent = "Monitoring";
    document.getElementById("banner-sub").textContent = "No active emergency";
  }
}

function updateBannerResolved() {
  const banner = document.getElementById("emergency-banner");
  banner.className = "banner-normal";
  document.getElementById("banner-icon").textContent  = "✅";
  document.getElementById("banner-title").textContent = "Incident Resolved";
  document.getElementById("banner-sub").textContent   = "Device has been reset";
  emergencyStart = null;
}

// ─────────────────────────────────────────────────────────────────────────────
// Alert ring
// ─────────────────────────────────────────────────────────────────────────────
function updateAlertRing(risk) {
  const ring = document.getElementById("alert-ring");
  ring.className = `ring-${risk}`;
  document.getElementById("alert-ring-text").textContent = capitalize(risk);
}

// ─────────────────────────────────────────────────────────────────────────────
// Connection chain
// ─────────────────────────────────────────────────────────────────────────────
function setChainNode(id, state) {
  const dot = document.querySelector(`#${id} .cn-dot`);
  if (dot) dot.className = `cn-dot ${state}`;
}

function markStale() {
  setChainNode("cn-device", "err");
  document.getElementById("conn-stale-msg").hidden = false;
}

// ─────────────────────────────────────────────────────────────────────────────
// Timeline
// ─────────────────────────────────────────────────────────────────────────────
function addTimelineEntry(text, dotClass = "") {
  const ts   = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const entry = { ts, text, dotClass };
  timelineEntries.unshift(entry);
  if (timelineEntries.length > 200) timelineEntries.pop();

  // Prepend to DOM (newest first)
  const list = document.getElementById("timeline-list");
  const row  = document.createElement("div");
  row.className = "tl-entry";
  row.innerHTML = `
    <span class="tl-time">${ts}</span>
    <span class="tl-dot ${dotClass}"></span>
    <span class="tl-text">${text}</span>
  `;
  list.prepend(row);

  // Cap DOM nodes
  while (list.children.length > 200) list.removeChild(list.lastChild);

  document.getElementById("timeline-count").textContent = timelineEntries.length;
}

// ─────────────────────────────────────────────────────────────────────────────
// Notes
// ─────────────────────────────────────────────────────────────────────────────
function addNote(text) {
  const ts  = new Date().toLocaleTimeString();
  const el  = document.getElementById("notes-list");
  const div = document.createElement("div");
  div.className = "note-item";
  div.innerHTML = `<div>${text}</div><div class="note-ts">${ts}</div>`;
  el.prepend(div);
  showToast(`Note added: ${text}`);
}

function addNoteFromInput() {
  const inp = document.getElementById("note-input");
  const val = inp.value.trim();
  if (!val) return;
  addNote(val);
  inp.value = "";
}

document.getElementById("note-input").addEventListener("keydown", e => {
  if (e.key === "Enter") addNoteFromInput();
});

// ─────────────────────────────────────────────────────────────────────────────
// Actions
// ─────────────────────────────────────────────────────────────────────────────
function toggleOnMyWay() {
  onMyWayActive = !onMyWayActive;
  const btn = document.getElementById("btn-onway");

  if (onMyWayActive) {
    btn.classList.add("active");
    btn.querySelector(".action-label").textContent = "On my way ✓";

    // Log in onway card
    const card = document.getElementById("onway-card");
    card.hidden = false;
    const ts  = new Date().toLocaleTimeString();
    const div = document.createElement("div");
    div.className = "onway-item";
    div.innerHTML = `
      <div class="onway-who">You</div>
      <div>I'm on my way</div>
      <div class="onway-ts">${ts}</div>
    `;
    document.getElementById("onway-list").prepend(div);
    addTimelineEntry("🚗 Responder: I'm on my way", "green");
    showToast("Acknowledged — tracking your response");

    // [STUB] When backend supports responder presence:
    // POST /incident/{id}/responder/onway
  } else {
    btn.classList.remove("active");
    btn.querySelector(".action-label").textContent = "I'm on my way";
  }
}

function callEmergency() {
  const isMobile = /Mobi|Android/i.test(navigator.userAgent);
  if (isMobile) {
    window.location.href = "tel:112";
    addTimelineEntry("📞 Responder called 112", "red");
    return;
  }
  // Desktop — show dialog with coordinates
  const coordEl = document.getElementById("dialog-coords");
  if (lastPayload) {
    coordEl.textContent =
      `${lastPayload.latitude.toFixed(6)}, ${lastPayload.longitude.toFixed(6)}`;
  } else {
    coordEl.textContent = "Location not yet received";
  }
  document.getElementById("call-dialog").hidden = false;
  addTimelineEntry("📞 Responder opened Call 112", "red");
}

function closeCallDialog() {
  document.getElementById("call-dialog").hidden = true;
}

function shareLocation() {
  if (!lastPayload) { showToast("No location data yet"); return; }
  const { latitude: lat, longitude: lng } = lastPayload;
  const url = `https://www.google.com/maps?q=${lat},${lng}`;
  if (navigator.share) {
    navigator.share({
      title: "ResQNet — Live location",
      text: `Emergency device location: ${lat.toFixed(5)}, ${lng.toFixed(5)}`,
      url,
    }).catch(() => {});
  } else {
    navigator.clipboard.writeText(url).then(() => showToast("Location link copied"));
  }
  addTimelineEntry("📍 Location link shared", "blue");
}

// ─────────────────────────────────────────────────────────────────────────────
// WebSocket
// ─────────────────────────────────────────────────────────────────────────────
const connDot   = document.getElementById("conn-dot");
const connLabel = document.getElementById("conn-label");
let ws            = null;
let reconnectDelay = 1000;

function connectWS() {
  const key = _getApiKey();
  const url = key ? `${WS_URL}?token=${encodeURIComponent(key)}` : WS_URL;
  ws = new WebSocket(url);

  ws.onopen = () => {
    reconnectDelay = 1000;
    connDot.className   = "dot connected";
    connLabel.textContent = "Live";
    setChainNode("cn-dashboard", "ok");
    setChainNode("cn-cloud",     "ok");
  };

  ws.onmessage = e => {
    try {
      const data = JSON.parse(e.data);
      // Only process broadcasts for our device
      if (data.device_id !== deviceId) return;
      applyPayload(data);
    } catch (err) {
      console.error("WS parse error:", err);
    }
  };

  ws.onerror = () => {
    connDot.className     = "dot reconnecting";
    connLabel.textContent = "Error";
    setChainNode("cn-cloud",     "err");
    setChainNode("cn-dashboard", "err");
  };

  ws.onclose = () => {
    connDot.className     = "dot disconnected";
    connLabel.textContent = `Reconnecting in ${Math.round(reconnectDelay / 1000)}s…`;
    setChainNode("cn-cloud",     "err");
    setChainNode("cn-dashboard", "err");
    document.getElementById("conn-stale-msg").hidden = false;
    setTimeout(() => {
      connDot.className     = "dot reconnecting";
      connLabel.textContent = "Reconnecting…";
      connectWS();
    }, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 2, 30000);
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Load device — fetch latest state, then subscribe to WS
// ─────────────────────────────────────────────────────────────────────────────
async function loadDevice(idOverride) {
  const id = idOverride ||
    document.getElementById("demo-device-id").value.trim();
  if (!id) { showToast("Enter a device ID"); return; }

  deviceId     = id;
  incidentStart = Math.floor(Date.now() / 1000);

  // Fresh session — clear any prior expiry state
  sessionExpired   = false;
  sessionExpiresAt = null;
  clearInterval(sessionTimer);
  document.getElementById("session-expiry").textContent = "Active";
  document.getElementById("session-meta").classList.remove("session-warning");
  document.getElementById("session-expired-overlay").hidden = true;

  // Show dashboard, hide empty state
  document.getElementById("empty-state").hidden    = true;
  document.getElementById("dashboard").hidden      = false;
  document.getElementById("header-device-id").textContent = id;

  // Kick off duration timer
  startDurationTimer();
  addTimelineEntry(`Started monitoring device ${id}`, "blue");

  // Connect WebSocket
  connectWS();

  // Fetch latest snapshot from backend (real endpoint)
  try {
    const r = await fetch(`${BACKEND}/device/${id}`, {
      headers: _authHeaders(),
    });
    if (r.ok) {
      const data = await r.json();
      if (!data.error) {
        applyPayload(data);
        // Fetch history for the trail
        fetchHistory(id);
        map.setView([data.latitude, data.longitude], 16);
      }
    }
  } catch (err) {
    console.warn("Could not fetch initial device state:", err);
    showToast("Waiting for first broadcast…");
  }
}

// Fetch history and draw full trail
async function fetchHistory(id) {
  try {
    const r = await fetch(`${BACKEND}/device/${id}/history`, {
      headers: _authHeaders(),
    });
    if (!r.ok) return;
    const history = await r.json();
    if (!Array.isArray(history) || history.length === 0) return;

    history.forEach(p => {
      trailCoords.push([p.latitude, p.longitude]);
      if (p.emergency && emergencyStart === null) {
        emergencyStart = p.timestamp;
      }
    });

    if (trailPolyline) {
      trailPolyline.setLatLngs(trailCoords);
    } else {
      trailPolyline = L.polyline(trailCoords, {
        color: "#3b82f6", weight: 2.5, opacity: 0.7,
      }).addTo(map);
    }

    // Recalculate distance from history
    for (let i = 1; i < trailCoords.length; i++) {
      const [la1, ln1] = trailCoords[i - 1];
      const [la2, ln2] = trailCoords[i];
      totalDistance += haversine(la1, ln1, la2, ln2);
    }
    const distStr = totalDistance >= 1000
      ? `${(totalDistance / 1000).toFixed(2)} km`
      : `${Math.round(totalDistance)} m`;
    document.getElementById("st-distance").textContent = distStr;

    addTimelineEntry(`Loaded ${history.length} historical points`, "blue");
  } catch (err) {
    console.warn("History fetch failed:", err);
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// URL param auto-load  (?device=DEV_123 or ?incident=INC-xxx&token=xxx)
// ─────────────────────────────────────────────────────────────────────────────
(function checkUrlParams() {
  const params = new URLSearchParams(window.location.search);

  // Future: ?incident=INC-xxx&token=xxx  [STUB — needs backend incident endpoint]
  const incident = params.get("incident");
  const token    = params.get("token");
  if (incident && token) {
    // [STUB] fetch incident details from /incident/{id}?token={token}
    // and extract device_id, then call loadDevice(device_id)
    showToast("Incident links require backend support (coming soon)");
  }

  // Current: ?device=DEV_xxx for direct device monitoring
  const device = params.get("device");
  if (device) {
    loadDevice(device);
  }
})();

// ─────────────────────────────────────────────────────────────────────────────
// Live audio — consent-gated device mic stream
// [STUB]: Real version needs the DEVICE's mic streamed via WebRTC/backend
// signaling, gated on consent the USER granted when they set up ResQNet
// (not consent asked here in the browser). This demo button simulates the
// consent-and-connect UX pattern only — it does not stream real device audio.
// ─────────────────────────────────────────────────────────────────────────────
let audioSimActive = false;

function initAudioDemo() {
  const startBtn = document.getElementById("audio-start-btn");
  const stopBtn  = document.getElementById("audio-stop-btn");
  if (!startBtn) return;

  startBtn.disabled = false;
  startBtn.title = "Demo only — device audio streaming requires backend WebRTC signaling";

  startBtn.addEventListener("click", () => {
    if (audioSimActive) return;
    audioSimActive = true;

    document.getElementById("audio-wave").classList.add("active");
    document.getElementById("audio-label").textContent = "Connected (demo)";
    document.getElementById("consent-badge").textContent = "Consent on file";
    document.getElementById("consent-badge").classList.add("granted");
    startBtn.disabled = true;
    stopBtn.disabled = false;
    addTimelineEntry("🎧 Responder connected to live audio (demo)", "blue");
  });

  stopBtn.addEventListener("click", () => {
    if (!audioSimActive) return;
    audioSimActive = false;

    document.getElementById("audio-wave").classList.remove("active");
    document.getElementById("audio-label").textContent = "Feed unavailable";
    startBtn.disabled = false;
    stopBtn.disabled = true;
    addTimelineEntry("🎧 Audio disconnected", "blue");
  });
}
document.addEventListener("DOMContentLoaded", initAudioDemo);

// ─────────────────────────────────────────────────────────────────────────────
// Audio alert
// ─────────────────────────────────────────────────────────────────────────────
let audioCtx = null;
function playAlert() {
  try {
    if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    [880, 1100].forEach((freq, i) => {
      const osc  = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.connect(gain);
      gain.connect(audioCtx.destination);
      osc.frequency.value = freq;
      const t = audioCtx.currentTime + i * 0.22;
      gain.gain.setValueAtTime(0.25, t);
      gain.gain.exponentialRampToValueAtTime(0.001, t + 0.3);
      osc.start(t);
      osc.stop(t + 0.3);
    });
  } catch (e) { console.warn("Audio:", e); }
}

// ─────────────────────────────────────────────────────────────────────────────
// Toast
// ─────────────────────────────────────────────────────────────────────────────
let toastQueue = [], toastRunning = false;
function showToast(msg) {
  toastQueue.push(msg);
  if (!toastRunning) runToast();
}
function runToast() {
  if (!toastQueue.length) { toastRunning = false; return; }
  toastRunning = true;
  const t = document.getElementById("toast");
  t.textContent = toastQueue.shift();
  t.classList.add("show");
  setTimeout(() => { t.classList.remove("show"); setTimeout(runToast, 400); }, 2800);
}

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────
function capitalize(s) {
  if (!s) return "—";
  return s.charAt(0).toUpperCase() + s.slice(1);
}
