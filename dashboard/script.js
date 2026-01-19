const timeline = document.getElementById("timeline");

const statusBox = document.getElementById("status");

const map = L.map("map").setView([28.61, 77.20], 15);

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: "© OpenStreetMap"
}).addTo(map);

let marker = null;

const ws = new WebSocket("ws://localhost:8000/ws/live");

ws.onopen = () => {
  statusBox.innerText = "WebSocket connected. Waiting for data...";
};

ws.onmessage = (event) => {
  console.log("WS DATA RECEIVED", event.data);

  const data = JSON.parse(event.data);
  
  if (data.alert) {
  alert(`🚨 ALERT!\nDevice: ${data.device_id}\nRisk: ${data.risk}`);
}
  if (data.escalation) {
  alert(`🚨 ESCALATION: ${data.escalation.toUpperCase()}`);
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
