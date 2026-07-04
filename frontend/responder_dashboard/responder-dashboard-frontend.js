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

const WEB_APP_URL = (window.RESQNET_CONFIG && window.RESQNET_CONFIG.SESSION_TOKEN_URL) || "https://script.google.com/macros/s/AKfycbxKCrT4zueJWbdPSpSPAnkCOaz1beC0l_zz_Gs62FqMX3mjYTyFns6yeg_x6zrBj0kIgQ/exec";

// ── Called from script.js's checkUrlParams() when ?uid=&token= are present ──
// Validates the magic link against the Apps Script and returns the result
// ({ success, incident } or { success:false, error }) so script.js can hand
// incident.deviceId into loadDevice() and drive the live map/WS feed from
// there. Only renders an error state into #incident-root (if present) on
// failure — on success it does NOT render its own UI, since script.js's
// existing dashboard takes over completely.
async function initIncidentPage() {
  const params = new URLSearchParams(window.location.search);
  const userId = params.get("uid");
  const token = params.get("token");

  if (!userId || !token) {
    renderError("This link is missing required parameters.");
    return { success: false, error: "Missing uid/token" };
  }

  try {
    const res = await fetch(WEB_APP_URL, {
      method: "POST",
      headers: { "Content-Type": "text/plain;charset=utf-8" }, // avoids CORS preflight on Apps Script
      body: JSON.stringify({ action: "validate", userID: userId, token }),
    });
    const data = await res.json();

    if (!data.success) {
      renderError(data.error || "This session link is no longer valid.");
      return data;
    }

    return data; // { success: true, incident: { deviceId, name, ... } }
  } catch (err) {
    renderError("Could not reach the emergency backend. Check your connection and retry.");
    return { success: false, error: "Network error" };
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
  const root = document.getElementById("incident-root");
  if (root) root.innerHTML = `<p>Loading incident…</p>`;
  else if (typeof showToast === "function") showToast("Loading incident…");
}

function renderError(message) {
  // #incident-root only exists in a standalone demo page. In the real
  // dashboard (index.html + script.js) it doesn't, so fall back to the
  // toast + empty-state that's already part of that page.
  const root = document.getElementById("incident-root");
  if (root) {
    root.innerHTML = `
      <div class="incident-error">
        <h2>Unable to open incident</h2>
        <p>${escapeHtml(message)}</p>
      </div>
    `;
    return;
  }
  if (typeof showToast === "function") showToast(message);
  const emptyState = document.getElementById("empty-state");
  if (emptyState) emptyState.hidden = false;
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