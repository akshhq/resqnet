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

const ws = new WebSocket("ws://127.0.0.1:8000/ws/live");

ws.onopen = () => {
  statusBox.innerText = "WebSocket connected. Waiting for data...";
};

ws.onmessage = (event) => {
  console.log("WS DATA RECEIVED", event.data);

  const data = JSON.parse(event.data);
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
  item.innerText = `${new Date(data.timestamp * 1000).toLocaleTimeString()} — ${data.context} — ${data.risk}`;
  timeline.prepend(item);

  map.setView([latitude, longitude], map.getZoom());
};

ws.onerror = () => {
  statusBox.innerText = "WebSocket error";
};

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

async function replay(deviceId) {
  // Uses the corrected /history endpoint (fix #2 in main.py)
  const res = await fetch(`http://127.0.0.1:8000/device/${deviceId}/history`);
  const history = await res.json();

  for (const point of history) {
    ws.onmessage({ data: JSON.stringify(point) });
    await new Promise(r => setTimeout(r, 1000));
  }
}