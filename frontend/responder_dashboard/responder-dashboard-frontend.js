// ═══════════════════════════════════════════════════════════════════
//   RESPONDER DASHBOARD — FRONTEND
// ═══════════════════════════════════════════════════════════════════
// This is the page the responder lands on when they click the link from
// the emergency email: https://your-dashboard.example.com/incident?uid=...&token=...
//
// On load, it reads uid/token straight from the URL (no login form) and
// calls action=validate against the Apps Script backend. If the backend
// confirms the token is live and matches that userID, the incident is
// shown. If the token is wrong, expired, or already resolved, the
// backend says so and the page shows that instead — the token itself is
// what gates access, not a session/cookie/login.
//
// SECURITY NOTE: because the token is the only credential, anyone who
// gets hold of the link can open it. Keep TOKEN_TTL_HOURS reasonable in
// the backend, resolve incidents promptly (which kills the link), and
// avoid forwarding these emails/links outside your responder group.

const WEB_APP_URL = "https://script.google.com/macros/s/AKfycbxKCrT4zueJWbdPSpSPAnkCOaz1beC0l_zz_Gs62FqMX3mjYTyFns6yeg_x6zrBj0kIgQ/exec";

// ── Runs on page load ───────────────────────────────────────────────
async function initIncidentPage() {
  const params = new URLSearchParams(window.location.search);
  const userId = params.get("uid");
  const token = params.get("token");

  if (!userId || !token) {
    renderError("This link is missing required parameters.");
    return;
  }

  renderLoading();

  try {
    const res = await fetch(WEB_APP_URL, {
      method: "POST",
      headers: { "Content-Type": "text/plain;charset=utf-8" }, // avoids CORS preflight on Apps Script
      body: JSON.stringify({ action: "validate", userID: userId, token }),
    });
    const data = await res.json();

    if (!data.success) {
      renderError(data.error || "This session link is no longer valid.");
      return;
    }

    renderIncident(data.incident, { userId, token });
  } catch (err) {
    renderError("Could not reach the emergency backend. Check your connection and retry.");
  }
}

// ── Mark resolved ────────────────────────────────────────────────────
async function resolveIncident(userId, token, resolvedBy) {
  const res = await fetch(WEB_APP_URL, {
    method: "POST",
    headers: { "Content-Type": "text/plain;charset=utf-8" },
    body: JSON.stringify({ action: "resolve", userID: userId, token, resolvedBy }),
  });
  return res.json(); // { success, message } or { success:false, error }
}

// ═══════════════════════════════════════════════════════════════════
//   EXAMPLE RENDERING (swap for your actual dashboard UI/framework)
// ═══════════════════════════════════════════════════════════════════

function renderLoading() {
  document.getElementById("incident-root").innerHTML = `<p>Loading incident…</p>`;
}

function renderError(message) {
  document.getElementById("incident-root").innerHTML = `
    <div class="incident-error">
      <h2>Unable to open incident</h2>
      <p>${escapeHtml(message)}</p>
    </div>
  `;
}

function renderIncident(incident, { userId, token }) {
  const locationHtml = incident.lat && incident.lng
    ? `<a href="https://maps.google.com/?q=${incident.lat},${incident.lng}" target="_blank">View last known location</a>`
    : `<p>No location data available.</p>`;

  document.getElementById("incident-root").innerHTML = `
    <div class="incident-card">
      <h1>Emergency: ${escapeHtml(incident.name)}</h1>
      <p><strong>User ID:</strong> ${escapeHtml(incident.userId)}</p>
      <p><strong>Device ID:</strong> ${escapeHtml(incident.deviceId)}</p>
      <p><strong>Triggered at:</strong> ${new Date(incident.createdAt).toLocaleString()}</p>
      <p><strong>Status:</strong> ${escapeHtml(incident.status)}</p>
      ${locationHtml}
      <button id="resolve-btn">Mark Resolved</button>
    </div>
  `;

  document.getElementById("resolve-btn").addEventListener("click", async () => {
    const resolvedBy = prompt("Your name (for the incident log):") || "";
    const result = await resolveIncident(userId, token, resolvedBy);
    if (result.success) {
      renderError("Incident marked resolved. This link is now closed.");
    } else {
      alert(result.error || "Could not resolve incident.");
    }
  });
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

// Run on load
window.addEventListener("DOMContentLoaded", initIncidentPage);
