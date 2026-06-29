const timeline    = document.getElementById("timeline");
const statusBox   = document.getElementById("status");
const connMessage = document.getElementById("conn-message");
const connDot     = document.getElementById("conn-dot");
const connLabel   = document.getElementById("conn-label");

// ---------------------------------------------------------------------------
// Map setup — two tile layers for light/dark mode
// ---------------------------------------------------------------------------
const map = L.map("map").setView([28.61, 77.20], 15);

const tileLayers = {
  light: L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "© OpenStreetMap"
  }),
  dark: L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
    attribution: "© OpenStreetMap © CARTO"
  })
};
tileLayers.light.addTo(map);
let darkMode = false;

function toggleDarkMode() {
  darkMode = !darkMode;
  if (darkMode) {
    tileLayers.light.remove();
    tileLayers.dark.addTo(map);
    document.body.classList.add("dark");
    document.getElementById("dark-btn").innerText = "☀ Light Mode";
  } else {
    tileLayers.dark.remove();
    tileLayers.light.addTo(map);
    document.body.classList.remove("dark");
    document.getElementById("dark-btn").innerText = "🌙 Dark Mode";
  }
}

// ---------------------------------------------------------------------------
// Trail colours — bright so they're visible on both light and dark tiles
// ---------------------------------------------------------------------------
const TRAIL_COLORS = {
  normal:   "#00aaff",   // bright blue
  elevated: "#ff9900",   // bright orange
  critical: "#ff2222",   // bright red
};
const TRAIL_WEIGHT  = 4;
const TRAIL_OPACITY = 0.95;

// ---------------------------------------------------------------------------
// Trail state
// ---------------------------------------------------------------------------
// activeTrailLayers: all polyline segments currently on the map (live mode)
// replayTrailLayers: polylines drawn during replay (cleared when replay ends)
let activeTrailLayers = [];
let replayTrailLayers = [];

// Per-device position history: device_id → [{lat, lng, risk, timestamp}, ...]
// Kept in JS so we can extract the 5-min pre-emergency window without a fetch.
const positionHistory = {};
const HISTORY_WINDOW  = 5 * 60;   // 5 minutes in seconds

// Whether an emergency is currently active (controls whether trail is shown)
let emergencyActive = false;
// Timestamp when emergency started (to anchor the 5-min lookback)
let emergencyStartTs = null;

// Whether replay is running (suppresses live trail drawing)
let replayRunning = false;

function clearTrailLayers(layerArray) {
  layerArray.forEach(l => map.removeLayer(l));
  layerArray.length = 0;
  // Hide legend if no trails remain on map
  if (activeTrailLayers.length === 0 && replayTrailLayers.length === 0) {
    document.getElementById("trail-legend").classList.remove("visible");
  }
}

// Draw a list of {lat, lng, risk} points as coloured polyline segments on map.
// Returns the created layers so caller can track them.
function drawTrailFromPoints(points) {
  const layers = [];
  if (points.length < 2) return layers;

  let segStart = 0;
  for (let i = 1; i <= points.length; i++) {
    const riskChanged = i === points.length || points[i].risk !== points[i - 1].risk;
    if (riskChanged) {
      const seg   = points.slice(segStart, i);
      const color = TRAIL_COLORS[seg[0].risk] || TRAIL_COLORS.normal;
      const latlngs = seg.map(p => [p.lat, p.lng]);
      const poly = L.polyline(latlngs, {
        color, weight: TRAIL_WEIGHT, opacity: TRAIL_OPACITY
      }).addTo(map);
      layers.push(poly);
      segStart = i - 1;
    }
  }
  // Show legend whenever a trail is drawn
  document.getElementById("trail-legend").classList.add("visible");
  return layers;
}

// ---------------------------------------------------------------------------
// Marker
// ---------------------------------------------------------------------------
let marker       = null;
let blinkInterval = null;

// ---------------------------------------------------------------------------
// Toast
// ---------------------------------------------------------------------------
let toastQueue  = [];
let toastRunning = false;

// ---------------------------------------------------------------------------
// WebSocket
// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// 5.2: API key — optional. Works with no key (dev mode).
// To enable auth: set window.RESQNET_API_KEY before this script loads,
// or enter it in the settings panel below. Leave blank to skip.
// ---------------------------------------------------------------------------
function _getApiKey() {
  if (window.RESQNET_API_KEY) return window.RESQNET_API_KEY;
  return sessionStorage.getItem("resqnet_api_key") || "";
}

function _setApiKey(k) {
  sessionStorage.setItem("resqnet_api_key", k.trim());
}

// Returns fetch headers — empty object when no key is set
function _authHeaders() {
  const k = _getApiKey();
  return k ? { "X-API-Key": k } : {};
}

const WS_URL = window.RESQNET_WS_URL ||
  `ws://${window.location.hostname}:8000/ws/live`;

// Apply key from the UI panel and reconnect WebSocket
function applyApiKey() {
  const input = document.getElementById("key-input");
  const k = input.value.trim();
  _setApiKey(k);
  input.value = "";
  input.placeholder = k ? "key saved — click Apply to reconnect" : "leave blank if auth disabled";

  // Close existing socket cleanly then reconnect with new token.
  // Do NOT null out ws.onclose — let the close handler fire normally
  // so the reconnect path runs via connect(). We just reset the delay.
  reconnectDelay = 1000;
  if (ws) {
    ws.close();   // onclose fires → calls connect() with the new token
  } else {
    connect();
  }
  showToast(k ? "🔑 API key saved. Reconnecting…" : "🔓 Auth cleared. Reconnecting…");
}

// Pre-fill key input from storage on load
window.addEventListener("load", () => {
  const stored = sessionStorage.getItem("resqnet_api_key");
  if (stored) {
    const inp = document.getElementById("key-input");
    if (inp) inp.placeholder = "key saved — click Apply to reconnect";
  }
});

function _buildWsUrl() {
  const key = _getApiKey();
  return key ? `${WS_URL}?token=${encodeURIComponent(key)}` : WS_URL;
}

let ws = null;
let reconnectDelay = 1000;
const RECONNECT_MAX = 30000;

function connect() {
  ws = new WebSocket(_buildWsUrl());

  ws.onopen = () => {
    reconnectDelay = 1000;
    connMessage.innerText     = "Connected. Waiting for device data…";
    connMessage.style.display = "block";
    connDot.className         = "connected";
    connLabel.innerText       = "Live";
  };

  ws.onmessage = (event) => { handlePayload(JSON.parse(event.data)); };

  ws.onerror = () => {
    connDot.className   = "reconnecting";
    connLabel.innerText = "Error";
  };

  ws.onclose = () => {
    const secs = Math.round(reconnectDelay / 1000);
    connMessage.innerText     = `Disconnected. Reconnecting in ${secs}s…`;
    connMessage.style.display = "block";
    connDot.className         = "disconnected";
    connLabel.innerText       = `Reconnecting in ${secs}s`;
    setTimeout(() => {
      connMessage.innerText   = "Reconnecting…";
      connDot.className       = "reconnecting";
      connLabel.innerText     = "Reconnecting…";
      connect();
    }, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX);
  };
}

connect();

// ---------------------------------------------------------------------------
// Audio
// ---------------------------------------------------------------------------
let audioCtx = null;
function playAlertSound() {
  try {
    if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    const osc  = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.connect(gain);
    gain.connect(audioCtx.destination);
    osc.frequency.value = 880;
    gain.gain.setValueAtTime(0.4, audioCtx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + 0.4);
    osc.start();
    osc.stop(audioCtx.currentTime + 0.4);
  } catch (e) {
    console.warn("Audio alert failed:", e);
  }
}

// ---------------------------------------------------------------------------
// Main payload handler
// ---------------------------------------------------------------------------
function handlePayload(data) {
  try {
  connMessage.style.display = "none";

  const { latitude, longitude, emergency, risk, context } = data;
  const deviceId = data.device_id;

  // --- Accumulate position history for this device ---
  if (!positionHistory[deviceId]) positionHistory[deviceId] = [];
  positionHistory[deviceId].push({
    lat: latitude, lng: longitude,
    risk, ts: data.timestamp
  });
  // Trim to last 10 minutes (keep more than 5 min so replay has full window)
  const cutoff = data.timestamp - 600;
  positionHistory[deviceId] = positionHistory[deviceId].filter(p => p.ts >= cutoff);

  // --- Emergency state transitions ---
  const wasEmergency = emergencyActive;

  if (emergency && !wasEmergency) {
    // Emergency just started — record start time and draw pre-emergency trail
    emergencyActive  = true;
    emergencyStartTs = data.timestamp;
    clearTrailLayers(activeTrailLayers);

    // Pull last 5 min of history before this moment
    const windowStart = emergencyStartTs - HISTORY_WINDOW;
    const prePoints   = positionHistory[deviceId].filter(
      p => p.ts >= windowStart && p.ts <= emergencyStartTs
    );
    if (prePoints.length >= 2) {
      activeTrailLayers.push(...drawTrailFromPoints(prePoints));
    }
  }

  if (!emergency && wasEmergency) {
    // Emergency ended (reset) — clear the trail
    emergencyActive  = false;
    emergencyStartTs = null;
    clearTrailLayers(activeTrailLayers);
  }

  // --- Extend live trail tick-by-tick during active emergency ---
  // (skip during replay — replay draws its own trail all at once)
  if (emergencyActive && !replayRunning) {
    const prev = activeTrailLayers.length > 0
      ? activeTrailLayers[activeTrailLayers.length - 1]
      : null;

    // If risk changed, start a new segment; else extend the last one
    const color = TRAIL_COLORS[risk] || TRAIL_COLORS.normal;
    if (!prev || prev.options.color !== color) {
      const poly = L.polyline([[latitude, longitude]], {
        color, weight: TRAIL_WEIGHT, opacity: TRAIL_OPACITY
      }).addTo(map);
      activeTrailLayers.push(poly);
      document.getElementById("trail-legend").classList.add("visible");
    } else {
      prev.addLatLng([latitude, longitude]);
    }
  }

  // --- Status box ---
  document.getElementById("s-device").innerText    = deviceId;
  document.getElementById("s-context").innerText   = context;
  document.getElementById("s-risk").innerText      = risk;
  document.getElementById("s-emergency").innerText = emergency;

  const escRow = document.getElementById("s-esc-row");
  const escVal = document.getElementById("s-escalation");
  if (data.escalation) {
    escVal.innerText     = data.escalation.toUpperCase();
    escRow.style.display = "block";
  } else {
    escRow.style.display = "none";
  }
  document.getElementById("s-reset-row").style.display = data.reset ? "block" : "none";

  // --- Battery bar ---
  if (data.battery !== undefined) {
    const wrap = document.getElementById("battery-bar-wrap");
    const bar  = document.getElementById("battery-bar");
    const txt  = document.getElementById("battery-text");
    wrap.style.display = "block";
    txt.style.display  = "block";
    bar.style.width    = `${data.battery}%`;
    txt.innerText      = `Battery: ${data.battery}%`;
    if (data.battery <= 20) {
      bar.style.background = "#ef4444"; txt.style.color = "#ef4444";
    } else if (data.battery <= 50) {
      bar.style.background = "#f59e0b"; txt.style.color = "#92400e";
    } else {
      bar.style.background = "#22c55e"; txt.style.color = "#166534";
    }
  }

  // --- Alerts ---
  if (data.alert || data.escalation) playAlertSound();
  if (data.alert)     showToast(`🚨 ALERT: ${risk.toUpperCase()}`);
  if (data.escalation) showToast(`🚨 ESCALATION: ${data.escalation.toUpperCase()}`);

  // --- Marker ---
  const color = emergency ? "#ff2222"
              : risk === "elevated" ? "#ff9900"
              : "#22c55e";

  if (!marker) {
    marker = L.circleMarker([latitude, longitude], {
      radius: 10, color, fillColor: color, fillOpacity: 0.9
    }).addTo(map);
  } else {
    marker.setLatLng([latitude, longitude]);
    marker.setStyle({ color, fillColor: color });
  }

  if (emergency && marker) {
    if (!blinkInterval) {
      blinkInterval = setInterval(() => {
        marker.setStyle({
          fillOpacity: marker.options.fillOpacity === 0.9 ? 0.2 : 0.9
        });
      }, 500);
    }
  } else {
    if (blinkInterval) {
      clearInterval(blinkInterval);
      blinkInterval = null;
      if (marker) marker.setStyle({ fillOpacity: 0.9 });
    }
  }

  // --- Timeline ---
  const item      = document.createElement("li");
  const humanTime = new Date(data.timestamp * 1000).toLocaleTimeString();
  const isoTime   = new Date(data.timestamp * 1000).toISOString();
  item.title      = `Unix: ${data.timestamp}  |  ISO: ${isoTime}`;
  item.innerText  = `${humanTime} — ${context} — ${risk}`;
  timeline.prepend(item);
  while (timeline.children.length > 100) timeline.removeChild(timeline.lastChild);

  // --- Auto-pan only when device leaves visible bounds ---
  const latLng = [latitude, longitude];
  if (!map.getBounds().contains(latLng)) map.setView(latLng, map.getZoom());
  } catch (err) {
    console.error("handlePayload crashed:", err, "\nData was:", JSON.stringify(data));
  }
}

// ---------------------------------------------------------------------------
// Toast
// ---------------------------------------------------------------------------
function showToast(message) {
  toastQueue.push(message);
  if (!toastRunning) processToastQueue();
}

function processToastQueue() {
  if (toastQueue.length === 0) { toastRunning = false; return; }
  toastRunning = true;
  const toast = document.getElementById("toast");
  toast.textContent = toastQueue.shift();
  toast.classList.add("show");
  setTimeout(() => {
    toast.classList.remove("show");
    setTimeout(processToastQueue, 400);
  }, 3000);
}

// ---------------------------------------------------------------------------
// Replay
// ---------------------------------------------------------------------------
async function replay(deviceId) {
  if (!deviceId) return;

  // Fetch full history from backend
  const res     = await fetch(
    `http://${window.location.hostname}:8000/device/${deviceId}/history`,
    { headers: _authHeaders() }
  );
  const history = await res.json();
  if (!history.length) { showToast("No history found for that device."); return; }

  // Clear any existing live trail and suppres live trail drawing
  clearTrailLayers(activeTrailLayers);
  clearTrailLayers(replayTrailLayers);
  replayRunning = true;

  // Find the emergency window in history (first panic trigger → reset/end)
  const emergencyPoints = [];
  let inEmergency = false;

  for (const point of history) {
    if (point.emergency && !inEmergency) inEmergency = true;
    if (!point.emergency && inEmergency) inEmergency = false;   // reset happened
    if (inEmergency) {
      emergencyPoints.push({
        lat: point.latitude, lng: point.longitude,
        risk: point.risk, ts: point.timestamp
      });
    }
  }

  // Pre-emergency: 5 min before first panic
  let prePoints = [];
  if (emergencyPoints.length > 0) {
    const firstPanicTs  = emergencyPoints[0].ts;
    const windowStart   = firstPanicTs - HISTORY_WINDOW;
    prePoints = history
      .filter(p => p.timestamp >= windowStart && p.timestamp < firstPanicTs)
      .map(p => ({ lat: p.latitude, lng: p.longitude, risk: "normal", ts: p.timestamp }));
  }

  const trailPoints = [...prePoints, ...emergencyPoints];

  if (trailPoints.length < 2) {
    showToast("No emergency path in history to replay.");
    replayRunning = false;
    return;
  }

  // Draw the full path all at once (don't animate it point-by-point)
  replayTrailLayers.push(...drawTrailFromPoints(trailPoints));

  // Fit map to the replay trail
  const allLatLngs = trailPoints.map(p => [p.lat, p.lng]);
  map.fitBounds(L.latLngBounds(allLatLngs), { padding: [40, 40] });

  // Now step through payloads to animate the marker and timeline
  // handlePayload is called but trail drawing is suppressed (replayRunning=true)
  for (const point of history) {
    handlePayload(point);
    await new Promise(r => setTimeout(r, 300));
  }

  replayRunning = false;
  showToast("Replay complete.");
}