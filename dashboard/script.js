const timeline = document.getElementById("timeline");

const statusBox = document.getElementById("status");

const map = L.map("map").setView([28.61, 77.20], 15);

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: "© OpenStreetMap"
}).addTo(map);

let marker = null;

let blinkInterval = null;

const ws = new WebSocket("ws://localhost:8000/ws/live");

ws.onopen = () => {
  statusBox.innerText = "WebSocket connected. Waiting for data...";
};

ws.onmessage = (event) => {
  console.log("WS DATA RECEIVED", event.data);

  const data = JSON.parse(event.data);

  const statusEl = document.getElementById("status");

  if (data.emergency) {
    statusEl.textContent = `🚨 EMERGENCY (${data.risk.toUpperCase()})`;
  } else {
    statusEl.textContent = `✅ NORMAL (${data.context})`;
  }

  if (data.escalation) {
    statusEl.textContent += " | ESCALATED";
  }

  
  if (data.alert) {
  showToast(`🚨 ALERT: ${data.risk.toUpperCase()}`);
}
  if (data.escalation) {
  showToast(`🚨 ESCALATION: ${data.escalation.toUpperCase()}`);
}

  const { latitude, longitude, emergency, risk, context } = data;

  let color = "green";

// Panic has highest priority (visual urgency)
if (data.emergency === true) {
  color = "red";
}
// Elevated risk without panic
else if (data.risk === "elevated") {
  color = "orange";
}
// Critical risk without explicit panic (edge case)
else if (data.risk === "critical") {
  color = "red";
}

  if (data.emergency) {
  if (!blinkInterval) {
    blinkInterval = setInterval(() => {
      marker.setStyle({ fillOpacity: marker.options.fillOpacity === 0.8 ? 0.2 : 0.8 });
    }, 500);
  }
  } 
  else {
    if (blinkInterval) {
      clearInterval(blinkInterval);
      blinkInterval = null;
      marker.setStyle({ fillOpacity: 0.8 });
    }
  }


  statusBox.innerHTML = `
    <b>Device:</b> ${data.device_id}<br/>
    <b>Context:</b> ${context}<br/>
    <b>Risk:</b> ${risk}<br/>
    <b>Emergency:</b> ${emergency}
  `;
  const item = document.createElement("li");
  item.innerText = `${new Date(data.timestamp * 1000).toLocaleTimeString()} — ${data.context} — ${data.risk}`;
  timeline.prepend(item);

  if (!marker) {
    marker = L.circleMarker([latitude, longitude], {
      radius: 10,
      color: color,
      fillColor: color,
      fillOpacity: 0.8
    }).addTo(map);
  } else {
    marker.setLatLng([latitude, longitude]);
    marker.setStyle({
  color: color,
  fillColor: color
});

  }

  map.setView([latitude, longitude], map.getZoom());
};

ws.onerror = () => {
  statusBox.innerText = "WebSocket error";
};

async function replay(deviceId) {
  const res = await fetch(`http://127.0.0.1:8000/device/${deviceId}/history`);
  const history = await res.json();

  for (const point of history) {
    ws.onmessage({ data: JSON.stringify(point) });
    await new Promise(r => setTimeout(r, 1000));
  }
}

function showToast(message) {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.classList.add("show");

  setTimeout(() => {
    toast.classList.remove("show");
  }, 3000);
}
