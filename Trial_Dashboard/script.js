// ─────────────────────────────────────────────────────────────────────────────
// ResQNet Dashboard + Integrated Simulator
// All simulation runs in the browser. No Python simulator needed.
// ─────────────────────────────────────────────────────────────────────────────

const BACKEND = "http://127.0.0.1:8000";
const WS_URL  = window.RESQNET_WS_URL || "ws://resqnet-backend.onrender.com/ws";

// ── Auth ─────────────────────────────────────────────────────────────────────
function _getApiKey() {
  return window.RESQNET_API_KEY || sessionStorage.getItem("resqnet_api_key") || "";
}
function _authHeaders() {
  const k = _getApiKey();
  return k ? { "X-API-Key": k, "Content-Type": "application/json" }
           : { "Content-Type": "application/json" };
}

// ── Map ───────────────────────────────────────────────────────────────────────
const map = L.map("map", { zoomControl: true }).setView([28.6139, 77.2090], 14);

const TILES = {
  dark:  L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
           { attribution: "© OpenStreetMap © CARTO" }),
  light: L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
           { attribution: "© OpenStreetMap" }),
};
TILES.dark.addTo(map);
let darkTheme = true;

function toggleTheme() {
  darkTheme = !darkTheme;
  if (darkTheme) {
    TILES.light.remove(); TILES.dark.addTo(map);
    document.body.classList.remove("light");
    document.getElementById("dark-btn").innerText = "☀ Light Mode";
  } else {
    TILES.dark.remove(); TILES.light.addTo(map);
    document.body.classList.add("light");
    document.getElementById("dark-btn").innerText = "🌙 Dark Mode";
  }
}

// ── Simulator constants (mirrors Python simulator) ────────────────────────────
const M_PER_DEG_LAT = 111000;
const SPEED_PROFILES = {
  stationary: { mean: 0.0,  std: 0.05, min: 0.0, max: 0.1  },
  walking:    { mean: 1.2,  std: 0.25, min: 0.4, max: 1.8  },
  running:    { mean: 3.0,  std: 0.40, min: 1.8, max: 4.0  },
  vehicle:    { mean: 11.0, std: 2.00, min: 5.0, max: 16.7 },
};
const TURN_RATE = { stationary: 30, walking: 12, running: 8, vehicle: 4 };
const PAUSE_CHANCE = { stationary: 0, walking: 0.03, running: 0.005, vehicle: 0 };

function gauss(mean, std) {
  // Box-Muller transform
  const u = 1 - Math.random(), v = Math.random();
  return mean + std * Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}
function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

// ── Device registry ───────────────────────────────────────────────────────────
// devices: Map<device_id, DeviceState>
const devices = new Map();
let selectedDeviceId = null;
let deviceCounter = 0;

const TRAIL_COLORS = { normal: "#00aaff", elevated: "#ff9900", critical: "#ff2222" };

function makeDeviceState(id, name, lat, lng, mode, isLocal = true) {
  return {
    id, name,
    lat, lng,
    heading: Math.random() * 360,
    speedSmooth: SPEED_PROFILES[mode].mean,
    mode,
    emergency: false,
    battery: 100.0,
    lowBatteryWarned: false,
    paused: false,
    pauseTicks: 0,
    risk: "normal",
    context: "walking",
    escalationStart: null,
    escalationLevel: 0,
    intervalId: null,
    tick: 0,
    // isLocal = true  → this browser tab owns the simulation loop (Add Device button)
    // isLocal = false → driven entirely by backend broadcasts (Python simulator,
    //                    send_updates.py, or a device added in another browser tab)
    isLocal,
    // Map objects
    marker: null,
    trailLayers: [],
    lastRisk: "normal",
    // Log entries for this device
    logs: [],
  };
}

// ── Movement engine ───────────────────────────────────────────────────────────
function moveTick(dev) {
  const mPerDegLng = M_PER_DEG_LAT * Math.cos(dev.lat * Math.PI / 180);

  if (dev.paused) {
    dev.pauseTicks--;
    if (dev.pauseTicks <= 0) dev.paused = false;
    dev.lat += gauss(0, 0.000003);
    dev.lng += gauss(0, 0.000003);
    return 0.05;
  }

  if (Math.random() < (PAUSE_CHANCE[dev.mode] || 0)) {
    dev.paused = true;
    dev.pauseTicks = 2 + Math.floor(Math.random() * 7);
    return 0.0;
  }

  // Heading drift
  dev.heading = (dev.heading + gauss(0, TURN_RATE[dev.mode] || 10)) % 360;
  if (dev.heading < 0) dev.heading += 360;

  // Speed lerp
  const p = SPEED_PROFILES[dev.mode];
  const target = clamp(gauss(p.mean, p.std), p.min, p.max);
  dev.speedSmooth += (target - dev.speedSmooth) * 0.25;

  const rad = dev.heading * Math.PI / 180;
  const dlat = (dev.speedSmooth * Math.cos(rad)) / M_PER_DEG_LAT;
  const dlng = (dev.speedSmooth * Math.sin(rad)) / mPerDegLng;

  dev.lat += dlat + gauss(0, 0.000004);
  dev.lng += dlng + gauss(0, 0.000004);

  // Clamp to valid coords
  dev.lat = clamp(dev.lat, -89.9, 89.9);
  dev.lng = clamp(dev.lng, -179.9, 179.9);

  return Math.round(dev.speedSmooth * 1000) / 1000;
}

// ── Context + risk classification ─────────────────────────────────────────────
function classifyContext(speed) {
  if (speed < 0.3) return "stationary";
  if (speed < 1.5) return "walking";
  if (speed < 3.5) return "running";
  return "vehicle";
}

function calculateRisk(emergency, prevSpeed, currSpeed) {
  if (emergency) return "critical";
  if (Math.abs(currSpeed - prevSpeed) > 5.0) return "elevated";
  return "normal";
}

// ── Escalation (mirrors Python backend logic) ─────────────────────────────────
const ESCALATION_STEPS = [[30, "escalated"], [90, "critical"]];

function checkEscalation(dev, emergency, timestamp) {
  if (!emergency) {
    dev.escalationStart = null;
    dev.escalationLevel = 0;
    return null;
  }
  if (dev.escalationStart === null) {
    dev.escalationStart = timestamp;
    dev.escalationLevel = 0;
    return null;
  }
  const elapsed = timestamp - dev.escalationStart;
  let fired = null;
  for (let i = 0; i < ESCALATION_STEPS.length; i++) {
    const [threshold, label] = ESCALATION_STEPS[i];
    if (elapsed >= threshold && dev.escalationLevel < i + 1) {
      dev.escalationLevel = i + 1;
      fired = label;
    }
  }
  return fired;
}

// ── POST to backend ───────────────────────────────────────────────────────────
async function postUpdate(payload) {
  try {
    const r = await fetch(`${BACKEND}/device/update`, {
      method: "POST",
      headers: _authHeaders(),
      body: JSON.stringify(payload),
    });
    return r.ok;
  } catch { return false; }
}

async function registerDevice(id) {
  try {
    await fetch(`${BACKEND}/device/register`, {
      method: "POST",
      headers: _authHeaders(),
      body: JSON.stringify({ device_id: id }),
    });
  } catch { /* backend might be down */ }
}

// ── Per-device tick ───────────────────────────────────────────────────────────
function deviceTick(dev) {
  dev.tick++;
  const ts = Math.floor(Date.now() / 1000);
  const prevSpeed = dev.speedSmooth;
  const speed = moveTick(dev);

  dev.context = classifyContext(speed);
  dev.risk    = calculateRisk(dev.emergency, prevSpeed, speed);

  const battery = Math.max(0, dev.battery - 0.05);
  dev.battery = battery;
  if (battery <= 20 && !dev.lowBatteryWarned) {
    dev.lowBatteryWarned = true;
    appendLog(dev, `⚠ LOW BATTERY: ${Math.round(battery)}%`, "log-emergency");
  }

  const escalation = checkEscalation(dev, dev.emergency, ts);
  if (escalation) {
    appendLog(dev, `🚨 ESCALATION: ${escalation.toUpperCase()}`, "log-escalation");
    playAlertSound();
  }

  const payload = {
    device_id:  dev.id,
    timestamp:  ts,
    latitude:   Math.round(dev.lat * 1e7) / 1e7,
    longitude:  Math.round(dev.lng * 1e7) / 1e7,
    speed:      speed,
    battery:    Math.round(battery),
    emergency:  dev.emergency,
    reset:      false,
  };

  postUpdate(payload);
  updateDeviceMap(dev, speed);
  updateDeviceCard(dev, speed);

  // Log entry for selected device
  const entry = `${new Date(ts * 1000).toLocaleTimeString()} · ${dev.context} · ${speed.toFixed(2)}m/s · bat:${Math.round(battery)}%` +
    (dev.emergency ? " · 🚨 EMERGENCY" : "");
  appendLog(dev, entry, dev.emergency ? "log-emergency" : "");
}

// ── Map updates ───────────────────────────────────────────────────────────────
function updateDeviceMap(dev, speed) {
  const pos  = [dev.lat, dev.lng];
  const color = dev.emergency ? "#ff2222"
              : dev.risk === "elevated" ? "#ff9900" : "#22c55e";

  if (!dev.marker) {
    dev.marker = L.circleMarker(pos, {
      radius: 9, color, fillColor: color, fillOpacity: 0.9, weight: 2
    }).addTo(map);
    dev.marker.bindTooltip(dev.name, { permanent: false, direction: "top" });
  } else {
    dev.marker.setLatLng(pos);
    dev.marker.setStyle({ color, fillColor: color });
  }

  // Trail — only during emergency
  if (dev.emergency) {
    const trailColor = TRAIL_COLORS[dev.risk] || TRAIL_COLORS.normal;
    if (dev.trailLayers.length === 0 || dev.lastRisk !== dev.risk) {
      const poly = L.polyline([pos], { color: trailColor, weight: 4, opacity: 0.95 }).addTo(map);
      dev.trailLayers.push(poly);
      dev.lastRisk = dev.risk;
    } else {
      dev.trailLayers[dev.trailLayers.length - 1].addLatLng(pos);
    }
  }

  // Pan map if this is the selected device and it leaves view
  if (dev.id === selectedDeviceId && !map.getBounds().contains(pos)) {
    map.panTo(pos);
  }
}

// ── Device card DOM ───────────────────────────────────────────────────────────
function updateDeviceCard(dev, speed) {
  const card = document.getElementById(`card-${dev.id}`);
  if (!card) return;

  const dot = card.querySelector(".device-status-dot");
  dot.className = "device-status-dot" +
    (dev.emergency ? " emergency" : dev.paused ? " paused" : "");

  card.querySelector(".stat-speed").textContent = `${speed.toFixed(1)} m/s`;
  card.querySelector(".stat-ctx").textContent   = dev.context;
  card.querySelector(".stat-bat").textContent   = `🔋 ${Math.round(dev.battery)}%`;

  const riskPill = card.querySelector(".stat-risk");
  riskPill.textContent = dev.risk;
  riskPill.className   = `stat-pill stat-risk risk-${dev.risk}`;

  const panicBtn = card.querySelector(".ctrl-btn.panic");
  panicBtn.classList.toggle("active", dev.emergency);
}

function renderDeviceCard(dev) {
  const div = document.createElement("div");
  div.className = "device-card" + (dev.id === selectedDeviceId ? " selected" : "");
  div.id = `card-${dev.id}`;
  div.onclick = (e) => {
    if (e.target.closest("button, select")) return;
    selectDevice(dev.id);
  };

  div.innerHTML = `
    <div class="device-card-top">
      <div class="device-status-dot"></div>
      <span class="device-name" title="${dev.id}">${dev.name}</span>
      <button class="device-remove-btn" onclick="removeDevice('${dev.id}')" title="Remove">✕</button>
    </div>
    <div class="device-card-stats">
      <span class="stat-pill stat-speed">0.0 m/s</span>
      <span class="stat-pill stat-ctx">${dev.mode}</span>
      <span class="stat-pill stat-risk risk-normal">normal</span>
      <span class="stat-pill stat-bat">🔋 100%</span>
    </div>
    <div class="device-controls">
      <button class="ctrl-btn panic" onclick="togglePanic('${dev.id}')">🚨 Panic</button>
      <button class="ctrl-btn reset" onclick="resetDevice('${dev.id}')">✅ Reset</button>
      <select class="mode-select" onchange="setMode('${dev.id}', this.value)">
        <option value="walking"    ${dev.mode==="walking"    ? "selected":""}>Walk</option>
        <option value="stationary" ${dev.mode==="stationary" ? "selected":""}>Still</option>
        <option value="running"    ${dev.mode==="running"    ? "selected":""}>Run</option>
        <option value="vehicle"    ${dev.mode==="vehicle"    ? "selected":""}>Vehicle</option>
      </select>
      <button class="ctrl-btn turn" onclick="sharpTurn('${dev.id}')">↩ Turn</button>
    </div>
  `;
  return div;
}

// ── Device controls ───────────────────────────────────────────────────────────
function togglePanic(id) {
  const dev = devices.get(id);
  if (!dev) return;
  dev.emergency = !dev.emergency;
  if (dev.emergency) {
    appendLog(dev, "🚨 PANIC TRIGGERED", "log-emergency");
    playAlertSound();
  } else {
    // Send explicit reset tick
    sendReset(dev);
  }
}

function resetDevice(id) {
  const dev = devices.get(id);
  if (!dev) return;
  dev.emergency = false;
  dev.escalationStart = null;
  dev.escalationLevel = 0;
  dev.trailLayers.forEach(l => map.removeLayer(l));
  dev.trailLayers = [];
  sendReset(dev);
  appendLog(dev, "✅ RESET", "log-reset");
}

async function sendReset(dev) {
  const payload = {
    device_id:  dev.id,
    timestamp:  Math.floor(Date.now() / 1000),
    latitude:   dev.lat,
    longitude:  dev.lng,
    speed:      0,
    battery:    Math.round(dev.battery),
    emergency:  false,
    reset:      true,
  };
  postUpdate(payload);
}

function setMode(id, mode) {
  const dev = devices.get(id);
  if (dev) dev.mode = mode;
}

function sharpTurn(id) {
  const dev = devices.get(id);
  if (dev) {
    dev.heading = Math.random() * 360;
    appendLog(dev, `↩ Sharp turn → ${Math.round(dev.heading)}°`, "");
  }
}

// ── Add / Remove devices ──────────────────────────────────────────────────────
function openModal() {
  document.getElementById("add-modal").classList.add("open");
  document.getElementById("modal-name").value = `Unit ${++deviceCounter}`;
  document.getElementById("modal-name").focus();
}

function closeModal() {
  document.getElementById("add-modal").classList.remove("open");
}

document.getElementById("modal-location").addEventListener("change", function() {
  document.getElementById("custom-coords-row").style.display =
    this.value === "custom" ? "block" : "none";
});

// Allow Enter key in modal
document.getElementById("add-modal").addEventListener("keydown", e => {
  if (e.key === "Enter") confirmAddDevice();
  if (e.key === "Escape") closeModal();
});

function confirmAddDevice() {
  const name = document.getElementById("modal-name").value.trim() || `Device ${deviceCounter}`;
  const locVal = document.getElementById("modal-location").value;
  const mode   = document.getElementById("modal-mode").value;

  let lat, lng;
  if (locVal === "custom") {
    const parts = document.getElementById("modal-custom").value.split(",");
    lat = parseFloat(parts[0]);
    lng = parseFloat(parts[1]);
    if (isNaN(lat) || isNaN(lng)) { showToast("Invalid coordinates."); return; }
  } else {
    [lat, lng] = locVal.split(",").map(Number);
  }

  const id  = `DEV_${Date.now()}`;
  const dev = makeDeviceState(id, name, lat, lng, mode);
  devices.set(id, dev);

  // Register with backend
  registerDevice(id);

  // Start tick interval
  dev.intervalId = setInterval(() => deviceTick(dev), 1000);

  // Render card
  const card = renderDeviceCard(dev);
  document.getElementById("device-list").appendChild(card);

  // Auto-select first device
  if (!selectedDeviceId) selectDevice(id);

  closeModal();
  showToast(`✅ ${name} added`);
}

function removeDevice(id) {
  const dev = devices.get(id);
  if (!dev) return;
  clearInterval(dev.intervalId);
  if (dev.marker) map.removeLayer(dev.marker);
  dev.trailLayers.forEach(l => map.removeLayer(l));
  devices.delete(id);

  const card = document.getElementById(`card-${id}`);
  if (card) card.remove();

  if (selectedDeviceId === id) {
    const next = devices.keys().next().value;
    selectedDeviceId = null;
    if (next) selectDevice(next);
    else {
      document.getElementById("log-device-label").textContent = "—";
      document.getElementById("log-list").innerHTML = "";
    }
  }
  showToast("Device removed");
}

function selectDevice(id) {
  // Deselect old
  if (selectedDeviceId) {
    const old = document.getElementById(`card-${selectedDeviceId}`);
    if (old) old.classList.remove("selected");
  }
  selectedDeviceId = id;
  const card = document.getElementById(`card-${id}`);
  if (card) card.classList.add("selected");

  const dev = devices.get(id);
  if (!dev) return;

  document.getElementById("log-device-label").textContent = dev.name;

  // Re-render log for this device
  renderLog(dev);

  // Pan map to this device
  if (dev.marker) map.panTo(dev.marker.getLatLng());
}

// ── Log ───────────────────────────────────────────────────────────────────────
function appendLog(dev, text, cls) {
  dev.logs.unshift({ text, cls }); // newest first
  if (dev.logs.length > 200) dev.logs.length = 200;
  if (dev.id === selectedDeviceId) renderLog(dev);
}

function renderLog(dev) {
  const el = document.getElementById("log-list");
  el.innerHTML = "";
  dev.logs.forEach(entry => {
    const li = document.createElement("div");
    li.className = `log-entry ${entry.cls || ""}`;
    li.textContent = entry.text;
    el.appendChild(li);
  });
}

// ── Handle a broadcast from the backend ───────────────────────────────────────
// Covers TWO kinds of devices:
//   1. Local devices  (isLocal=true)  — created via "+ Add Device" in this tab.
//      Movement is computed here in the browser; this function just syncs the
//      authoritative risk/escalation/reset state computed by the backend.
//   2. Remote devices (isLocal=false) — anything broadcasting from OUTSIDE this
//      tab: the Python simulator.py, send_updates.py, or a device added in a
//      different browser tab. These have no local interval — their marker,
//      card, and log are driven entirely by incoming broadcasts.
// This lets the Python simulator and the in-browser simulator run at the same
// time, on the same map, in the same sidebar, simultaneously.
function handleBroadcast(data) {
  let dev = devices.get(data.device_id);

  // First time seeing this device → auto-create a REMOTE card for it.
  if (!dev) {
    dev = makeDeviceState(
      data.device_id,
      data.device_id,        // no friendly name available — use the raw ID
      data.latitude,
      data.longitude,
      data.context || "walking",
      false                  // isLocal = false: backend drives this device
    );
    devices.set(data.device_id, dev);

    const card = renderDeviceCard(dev);
    document.getElementById("device-list").appendChild(card);

    if (!selectedDeviceId) selectDevice(dev.id);
    showToast(`📡 ${dev.id} connected (external device)`);
  }

  // Sync authoritative fields from the backend on every broadcast.
  dev.lat       = data.latitude;
  dev.lng       = data.longitude;
  dev.risk      = data.risk;
  dev.context   = data.context;
  dev.battery   = data.battery;
  dev.emergency = data.emergency;

  // Remote devices have no local tick loop — render their map marker and
  // card here directly using the broadcast data, exactly like deviceTick()
  // does for local devices.
  if (!dev.isLocal) {
    updateDeviceMap(dev, data.speed);
    updateDeviceCard(dev, data.speed);

    const entry = `${new Date(data.timestamp * 1000).toLocaleTimeString()} · ` +
      `${data.context} · ${data.speed.toFixed(2)}m/s · bat:${data.battery}%` +
      (data.emergency ? " · 🚨 EMERGENCY" : "");
    appendLog(dev, entry, data.emergency ? "log-emergency" : "");
  }

  if (data.escalation) {
    appendLog(dev, `🚨 SERVER: ${data.escalation.toUpperCase()}`, "log-escalation");
    playAlertSound();
  }
  if (data.reset) {
    dev.trailLayers.forEach(l => map.removeLayer(l));
    dev.trailLayers = [];
    appendLog(dev, "✅ SERVER: Reset confirmed", "log-reset");
  }
}

// ── WebSocket — receive broadcasts from backend ───────────────────────────────
// (Even though simulator runs in browser, backend still broadcasts to confirm
//  receipt and compute escalation authoritatively)
const connDot   = document.getElementById("conn-dot");
const connLabel = document.getElementById("conn-label");
let ws = null;
let reconnectDelay = 1000;

function connect() {
  const key = _getApiKey();
  const url = key ? `${WS_URL}?token=${encodeURIComponent(key)}` : WS_URL;
  ws = new WebSocket(url);

  ws.onopen = () => {
    reconnectDelay = 1000;
    connDot.className   = "connected";
    connLabel.innerText = "Live";
  };

  ws.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data);
      handleBroadcast(data);
    } catch(err) {
      console.error("WS parse error:", err);
    }
  };

  ws.onerror = () => {
    connDot.className   = "reconnecting";
    connLabel.innerText = "Error";
  };

  ws.onclose = () => {
    connDot.className   = "disconnected";
    connLabel.innerText = `Reconnecting in ${Math.round(reconnectDelay/1000)}s`;
    setTimeout(() => {
      connDot.className   = "reconnecting";
      connLabel.innerText = "Reconnecting…";
      connect();
    }, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 2, 30000);
  };
}
connect();

// ── Toast ─────────────────────────────────────────────────────────────────────
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

// ── Audio ─────────────────────────────────────────────────────────────────────
let audioCtx = null;
function playAlertSound() {
  try {
    if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.connect(gain); gain.connect(audioCtx.destination);
    osc.frequency.value = 880;
    gain.gain.setValueAtTime(0.3, audioCtx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + 0.4);
    osc.start(); osc.stop(audioCtx.currentTime + 0.4);
  } catch(e) { console.warn("Audio:", e); }
}

// ── Modal location select ─────────────────────────────────────────────────────
// (Listener already declared above near modal HTML)