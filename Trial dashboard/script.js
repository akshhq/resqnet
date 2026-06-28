const timeline   = document.getElementById("timeline");
const statusBox  = document.getElementById("status");
const connMessage = document.getElementById("conn-message");

// Fix 4.1: connection status dot elements
const connDot   = document.getElementById("conn-dot");
const connLabel = document.getElementById("conn-label");

const map = L.map("map").setView([28.61, 77.20], 15);

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: "© OpenStreetMap"
}).addTo(map);

let marker = null;
let blinkInterval = null;

// Fix 4.2: polyline trail — grows with each position update, coloured by risk
let trail = L.polyline([], { weight: 2, opacity: 0.7 }).addTo(map);
let lastRisk = "normal"; // track risk at each point for trail colouring

// FIX #9: Toast queue so overlapping alerts don't clobber each other
let toastQueue = [];
let toastRunning = false;

// Fix 1.13: derive the WebSocket URL from the current page's host so the
// dashboard works when the backend is deployed anywhere — not just localhost.
// Override by setting window.RESQNET_WS_URL before this script loads.
const WS_URL = window.RESQNET_WS_URL || `ws://${window.location.hostname}:8000/ws/live`;

// Fix 1.6: reconnection state
let ws = null;
let reconnectDelay = 1000;   // start at 1 s, doubles each attempt, caps at 30 s
const RECONNECT_MAX = 30000;

function connect() {
  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    reconnectDelay = 1000;
    // Use connMessage — NOT statusBox.innerText — so we never wipe the
    // child spans that handlePayload depends on.
    connMessage.innerText   = "Connected. Waiting for device data…";
    connMessage.style.display = "block";
    // Fix 4.1: show green dot on successful connection
    connDot.className   = "connected";
    connLabel.innerText = "Live";
  };

  ws.onmessage = (event) => {
    handlePayload(JSON.parse(event.data));
  };

  ws.onerror = () => {
    console.warn("WebSocket error — will attempt reconnect via onclose.");
    // Fix 4.1: amber dot on error before close fires
    connDot.className   = "reconnecting";
    connLabel.innerText = "Error";
  };

  ws.onclose = () => {
    const seconds = Math.round(reconnectDelay / 1000);
    connMessage.innerText     = `Disconnected. Reconnecting in ${seconds}s…`;
    connMessage.style.display = "block";
    // Fix 4.1: red dot while disconnected
    connDot.className   = "disconnected";
    connLabel.innerText = `Reconnecting in ${seconds}s`;

    setTimeout(() => {
      connMessage.innerText   = "Reconnecting…";
      // Fix 4.1: amber dot while actively trying to reconnect
      connDot.className   = "reconnecting";
      connLabel.innerText = "Reconnecting…";
      connect();
    }, reconnectDelay);

    reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX);
  };
}

connect();

// Fix 4.5: play a short beep via Web Audio API when emergency triggers.
// AudioContext must be created after a user gesture on some browsers, but for
// an emergency dashboard the first alert toast/click is sufficient to unlock it.
let audioCtx = null;
function playAlertSound() {
  try {
    if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = audioCtx.createOscillator();
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

function handlePayload(data) {
  console.log("WS DATA RECEIVED", data);

  // Hide the "waiting" connection message the moment real data arrives
  connMessage.style.display = "none";

  const { latitude, longitude, emergency, risk, context } = data;

  // Update status fields individually via named spans so innerHTML never
  // wipes the battery bar elements that live inside the same status box.
  document.getElementById("s-device").innerText     = data.device_id;
  document.getElementById("s-context").innerText    = context;
  document.getElementById("s-risk").innerText       = risk;
  document.getElementById("s-emergency").innerText  = emergency;
  const escRow = document.getElementById("s-esc-row");
  const escVal = document.getElementById("s-escalation");
  if (data.escalation) {
    escVal.innerText      = data.escalation.toUpperCase();
    escRow.style.display  = "block";
  } else {
    escRow.style.display  = "none";
  }
  const resetRow = document.getElementById("s-reset-row");
  resetRow.style.display = data.reset ? "block" : "none";

  // Fix 4.3: battery bar — update in-place, no innerHTML wipe involved
  if (data.battery !== undefined) {
    const batteryBarWrap = document.getElementById("battery-bar-wrap");
    const batteryBar     = document.getElementById("battery-bar");
    const batteryText    = document.getElementById("battery-text");
    batteryBarWrap.style.display = "block";
    batteryText.style.display    = "block";
    batteryBar.style.width       = `${data.battery}%`;
    batteryText.innerText        = `Battery: ${data.battery}%`;
    if (data.battery <= 20) {
      batteryBar.style.background = "#ef4444";
      batteryText.style.color     = "#ef4444";
    } else if (data.battery <= 50) {
      batteryBar.style.background = "#f59e0b";
      batteryText.style.color     = "#92400e";
    } else {
      batteryBar.style.background = "#22c55e";
      batteryText.style.color     = "#166534";
    }
  }

  // Fix 4.5: play alert sound on first emergency trigger or escalation
  if (data.alert || data.escalation) {
    playAlertSound();
  }

  // Toast alerts
  if (data.alert) {
    showToast(`🚨 ALERT: ${data.risk.toUpperCase()}`);
  }
  if (data.escalation) {
    showToast(`🚨 ESCALATION: ${data.escalation.toUpperCase()}`);
  }

  // Marker colour
  let color = "green";
  if (data.emergency === true) {
    color = "red";
  } else if (data.risk === "critical") {
    color = "red";
  } else if (data.risk === "elevated") {
    color = "orange";
  }

  // Create or update marker
  if (!marker) {
    marker = L.circleMarker([latitude, longitude], {
      radius: 10,
      color: color,
      fillColor: color,
      fillOpacity: 0.8
    }).addTo(map);
  } else {
    marker.setLatLng([latitude, longitude]);
    marker.setStyle({ color: color, fillColor: color });
  }

  // Fix 4.2: append this position to the movement trail.
  // Start a new coloured segment whenever risk level changes so the trail
  // visually shows where the situation escalated.
  if (risk !== lastRisk) {
    // Begin a fresh polyline segment in the new risk colour
    const segColor = risk === "critical" ? "#ef4444"
                   : risk === "elevated" ? "#f59e0b"
                   : "#3b82f6";
    trail = L.polyline([[latitude, longitude]], {
      color: segColor, weight: 2, opacity: 0.7
    }).addTo(map);
    lastRisk = risk;
  } else {
    trail.addLatLng([latitude, longitude]);
  }

  // Blink on emergency
  if (data.emergency && marker) {
    if (!blinkInterval) {
      blinkInterval = setInterval(() => {
        marker.setStyle({
          fillOpacity: marker.options.fillOpacity === 0.8 ? 0.2 : 0.8
        });
      }, 500);
    }
  } else {
    if (blinkInterval) {
      clearInterval(blinkInterval);
      blinkInterval = null;
      if (marker) marker.setStyle({ fillOpacity: 0.8 });
    }
  }

  // Fix 4.4: show ISO timestamp on hover via title attribute, human time as text
  const item = document.createElement("li");
  const humanTime = new Date(data.timestamp * 1000).toLocaleTimeString();
  const isoTime   = new Date(data.timestamp * 1000).toISOString();
  item.title      = `Unix: ${data.timestamp}  |  ISO: ${isoTime}`;
  item.innerText  = `${humanTime} — ${context} — ${risk}`;
  timeline.prepend(item);
  while (timeline.children.length > 100) {
    timeline.removeChild(timeline.lastChild);
  }

  // Fix 1.7: only re-centre when device drifts outside visible bounds
  const latLng = [latitude, longitude];
  if (!map.getBounds().contains(latLng)) {
    map.setView(latLng, map.getZoom());
  }
}

// FIX #9: Toast queue — shows one at a time, queues the rest
function showToast(message) {
  toastQueue.push(message);
  if (!toastRunning) processToastQueue();
}

function processToastQueue() {
  if (toastQueue.length === 0) {
    toastRunning = false;
    return;
  }

  toastRunning = true;
  const toast = document.getElementById("toast");
  toast.textContent = toastQueue.shift();
  toast.classList.add("show");

  setTimeout(() => {
    toast.classList.remove("show");
    // Small gap between toasts so the fade-out is visible
    setTimeout(processToastQueue, 400);
  }, 3000);
}

// Fix 1.5: call handlePayload() directly — no fake event object needed.
// Previously this called ws.onmessage({ data: JSON.stringify(point) }) which
// is fragile: any code in the handler that reads native WebSocket event
// properties (type, target, etc.) would silently fail or throw.
async function replay(deviceId) {
  const res = await fetch(`http://${window.location.hostname}:8000/device/${deviceId}/history`);
  const history = await res.json();

  for (const point of history) {
    handlePayload(point);
    await new Promise(r => setTimeout(r, 1000));
  }
}