const timeline = document.getElementById("timeline");
const statusBox = document.getElementById("status");

const map = L.map("map").setView([28.61, 77.20], 15);

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: "© OpenStreetMap"
}).addTo(map);

let marker = null;
let blinkInterval = null;

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
    reconnectDelay = 1000;   // reset backoff on successful connection
    statusBox.innerText = "WebSocket connected. Waiting for data...";
  };

  // Fix 1.5: handler logic lives in handlePayload() so replay() can call it
  // directly without constructing a fake WebSocket event object.
  ws.onmessage = (event) => {
    handlePayload(JSON.parse(event.data));
  };

  // Fix 1.6: on error just log — onclose always fires after onerror and
  // that is where reconnection is triggered.
  ws.onerror = () => {
    console.warn("WebSocket error — will attempt reconnect via onclose.");
  };

  // Fix 1.6: reconnect with exponential backoff on any close (error or server
  // restart). Show a live countdown in the status box so the operator knows
  // the dashboard is not permanently dead.
  ws.onclose = () => {
    const seconds = Math.round(reconnectDelay / 1000);
    statusBox.innerText = `Disconnected. Reconnecting in ${seconds}s…`;

    setTimeout(() => {
      statusBox.innerText = "Reconnecting…";
      connect();
    }, reconnectDelay);

    reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX);
  };
}

connect();

// Fix 1.5: standalone handler so both ws.onmessage and replay() use the same
// code path without faking a WebSocket event object.
function handlePayload(data) {
  console.log("WS DATA RECEIVED", data);

  const { latitude, longitude, emergency, risk, context } = data;

  // FIX #5: Single status update block — removed the dead first assignments
  // that were immediately overwritten by the innerHTML block below.
  statusBox.innerHTML = `
    <b>Device:</b> ${data.device_id}<br/>
    <b>Context:</b> ${context}<br/>
    <b>Risk:</b> ${risk}<br/>
    <b>Emergency:</b> ${emergency}
    ${data.escalation ? `<br/><b>Escalation:</b> ${data.escalation.toUpperCase()}` : ""}
  `;

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

  // FIX #3: Blink interval only started after marker is guaranteed to exist
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

  // Timeline entry
  const item = document.createElement("li");
  // Fix 1.8: cap timeline at 100 entries so the DOM doesn't grow unbounded
  // during long sessions and slow the browser to a crawl.
  item.innerText = `${new Date(data.timestamp * 1000).toLocaleTimeString()} — ${data.context} — ${data.risk}`;
  timeline.prepend(item);
  while (timeline.children.length > 100) {
    timeline.removeChild(timeline.lastChild);
  }

  // Fix 1.7: only re-centre the map when the marker has drifted outside the
  // currently visible bounds. This way the operator can freely pan/zoom
  // without the map snapping back every second.
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