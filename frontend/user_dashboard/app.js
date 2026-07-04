// ─────────────────────────────────────────────────────────────────────────────
// ResQNet User Dashboard — app.js
// Single-page app: auth, registration, device management, contacts.
// Uses Firebase for Auth, Neon Postgres (via Backend) for everything else.
// ─────────────────────────────────────────────────────────────────────────────
import { initializeApp } from "https://www.gstatic.com/firebasejs/12.15.0/firebase-app.js";
import { 
  getAuth, 
  createUserWithEmailAndPassword, 
  signInWithEmailAndPassword, 
  signOut, 
  sendEmailVerification,
  GoogleAuthProvider,
  signInWithPopup,
  RecaptchaVerifier,
  signInWithPhoneNumber,
  onAuthStateChanged
} from "https://www.gstatic.com/firebasejs/12.15.0/firebase-auth.js";

const firebaseConfig = window.firebaseConfig || {};
const app = initializeApp(firebaseConfig);
const auth = getAuth(app);


const BACKEND = (window.RESQNET_CONFIG && window.RESQNET_CONFIG.BACKEND_URL) || "https://resqnet-gti8.onrender.com";

// ── Auth / API key headers (reuses the same optional API key system as Trial_Dashboard) ──
function _getApiKey() {
  return window.RESQNET_API_KEY || sessionStorage.getItem("resqnet_api_key") || "";
}
function _headers() {
  const h = { "Content-Type": "application/json" };
  const k = _getApiKey();
  if (k) h["X-API-Key"] = k;
  return h;
}

// ── Session state ────────────────────────────────────────────────────────────
let currentUserId = localStorage.getItem("resqnet_user_id") || null;
let currentView = "home";
// pendingRegistration (declared near the REGISTER handler below) tracks
// the form data between "OTP sent" and "OTP verified".

// ── API helper ───────────────────────────────────────────────────────────────
async function api(method, path, body) {
  const opts = { method, headers: _headers() };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const res = await fetch(`${BACKEND}${path}`, opts);
  let data = null;
  try { data = await res.json(); } catch { /* no body */ }
  if (!res.ok) {
    let msg = `Request failed (${res.status})`;
    if (data && data.detail) {
      if (Array.isArray(data.detail)) {
        // FastAPI/Pydantic 422 shape: [{ loc: [...], msg: "...", type: "..." }, ...]
        msg = data.detail
          .map((e) => {
            const field = Array.isArray(e.loc) ? e.loc[e.loc.length - 1] : "field";
            return `${field}: ${e.msg}`;
          })
          .join("; ");
      } else {
        msg = data.detail;
      }
    }
    throw new Error(msg);
  }
  return data;
}

// ── Toast ─────────────────────────────────────────────────────────────────────
function showToast(msg, type = "") {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = type ? `toast-${type}` : "";
  requestAnimationFrame(() => t.classList.add("show"));
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.remove("show"), 3200);
}

// ── View router ──────────────────────────────────────────────────────────────
function showView(name) {
  document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
  const el = document.getElementById(`view-${name}`);
  if (el) el.classList.add("active");
  currentView = name;

  document.querySelectorAll(".nav-tab").forEach(t => {
    t.classList.toggle("active", t.dataset.view === name);
  });

  if (name === "home")        loadHome();
  if (name === "contacts")    loadContacts();
  if (name === "preferences") loadPreferences();
  if (name === "incidents")   loadIncidents();
}

function setLoggedIn(userId) {
  currentUserId = userId;
  localStorage.setItem("resqnet_user_id", userId);
  document.getElementById("top-nav").classList.remove("hidden");
  showView("home");
}

async function logout() {
  await signOut(auth);
  currentUserId = null;
  localStorage.removeItem("resqnet_user_id");
  document.getElementById("top-nav").classList.add("hidden");
  document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
  document.getElementById("view-auth").classList.add("active");
  document.getElementById("login-email").value = "";
  document.getElementById("login-password").value = "";
}

// ─────────────────────────────────────────────────────────────────────────────
// AUTH — tab switching
// ─────────────────────────────────────────────────────────────────────────────

document.querySelectorAll(".auth-tab").forEach(tab => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".auth-tab").forEach(t => t.classList.remove("active"));
    document.querySelectorAll(".auth-form").forEach(f => f.classList.remove("active"));
    tab.classList.add("active");
    document.getElementById(`tab-${tab.dataset.tab}`).classList.add("active");
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// REGISTER — Firebase Auth
// ─────────────────────────────────────────────────────────────────────────────

document.getElementById("register-btn").addEventListener("click", async () => {
  const name     = document.getElementById("reg-name").value.trim();
  const dob      = document.getElementById("reg-dob").value;
  const phone    = normalisePhone(document.getElementById("reg-phone").value.trim());
  const email    = document.getElementById("reg-email").value.trim();
  const password = document.getElementById("reg-password").value;

  if (!name || !dob || !phone || !email || !password) {
    showToast("Please fill in all fields.", "error");
    return;
  }
  if (password.length < 8) {
    showToast("Password must be at least 8 characters.", "error");
    return;
  }

  try {
    const userCredential = await createUserWithEmailAndPassword(auth, email, password);
    const user = userCredential.user;
    
    // Send email verification
    await sendEmailVerification(user);

    // Create profile in our Postgres backend
    await api("POST", "/user/register", { 
      user_id: user.uid,
      name, dob, phone, email 
    });

    showToast("Account created! Please check your email to verify.", "success");
    setLoggedIn(user.uid);
  } catch (err) {
    showToast(err.message, "error");
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// LOGIN — Firebase Auth
// ─────────────────────────────────────────────────────────────────────────────

document.getElementById("login-btn").addEventListener("click", async () => {
  const email    = document.getElementById("login-email").value.trim();
  const password = document.getElementById("login-password").value;

  if (!email || !password) {
    showToast("Enter your email and password.", "error");
    return;
  }

  try {
    const userCredential = await signInWithEmailAndPassword(auth, email, password);
    showToast("Signed in.", "success");
    // Registration with backend handles user creation, setLoggedIn will be called via onAuthStateChanged or directly
  } catch (err) {
    showToast(err.message, "error");
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// LOGIN — Google Auth
// ─────────────────────────────────────────────────────────────────────────────
const googleProvider = new GoogleAuthProvider();
document.getElementById("google-login-btn").addEventListener("click", async () => {
  try {
    const result = await signInWithPopup(auth, googleProvider);
    showToast("Signed in with Google.", "success");
    
    // Ensure user profile exists on backend
    try {
      await api("POST", "/user/register", { 
        user_id: result.user.uid,
        name: result.user.displayName || "Google User",
        dob: "2000-01-01", // Placeholder for Google auth
        phone: result.user.phoneNumber || "",
        email: result.user.email 
      });
    } catch(e) { /* Might already exist */ }
  } catch (err) {
    showToast(err.message, "error");
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// LOGIN — Phone Auth
// ─────────────────────────────────────────────────────────────────────────────
let confirmationResult = null;

// Initialize Recaptcha after DOM load
window.recaptchaVerifier = new RecaptchaVerifier(auth, 'recaptcha-container', {
  'size': 'invisible'
});

document.getElementById("send-otp-btn").addEventListener("click", async () => {
  const phoneNumber = document.getElementById("login-phone").value.trim();
  if (!phoneNumber) {
    showToast("Please enter a phone number", "error");
    return;
  }
  
  try {
    const appVerifier = window.recaptchaVerifier;
    confirmationResult = await signInWithPhoneNumber(auth, phoneNumber, appVerifier);
    document.getElementById("otp-container").classList.remove("hidden");
    showToast("OTP sent!", "success");
  } catch (err) {
    showToast(err.message, "error");
  }
});

document.getElementById("verify-otp-btn").addEventListener("click", async () => {
  const code = document.getElementById("login-otp").value.trim();
  if (!code || !confirmationResult) return;

  try {
    const result = await confirmationResult.confirm(code);
    showToast("Signed in with Phone.", "success");
    
    // Ensure user profile exists on backend
    try {
      await api("POST", "/user/register", { 
        user_id: result.user.uid,
        name: "Phone User",
        dob: "2000-01-01",
        phone: result.user.phoneNumber || "",
        email: "" 
      });
    } catch(e) { /* Might already exist */ }
  } catch (err) {
    showToast(err.message, "error");
  }
});

function normalisePhone(raw) {
  // Strip spaces/dashes, keep leading + if present
  return raw.replace(/[\s-]/g, "");
}



// ─────────────────────────────────────────────────────────────────────────────
// LOGOUT
// ─────────────────────────────────────────────────────────────────────────────

document.getElementById("logout-btn").addEventListener("click", logout);

// ─────────────────────────────────────────────────────────────────────────────
// NAV TABS
// ─────────────────────────────────────────────────────────────────────────────

document.querySelectorAll(".nav-tab").forEach(tab => {
  tab.addEventListener("click", () => showView(tab.dataset.view));
});

// ─────────────────────────────────────────────────────────────────────────────
// HOME VIEW — device cards
// ─────────────────────────────────────────────────────────────────────────────

async function loadHome() {
  if (!currentUserId) return;
  try {
    const profile = await api("GET", `/user/${currentUserId}`);
    renderHome(profile);
  } catch (err) {
    showToast(err.message, "error");
  }
}

function renderHome(profile) {
  document.getElementById("home-greeting").textContent = `Hi, ${profile.user.name.split(" ")[0]}`;
  document.getElementById("home-uid").textContent = profile.user.user_id;

  const grid = document.getElementById("device-cards");
  const empty = document.getElementById("no-devices");

  if (!profile.devices.length) {
    grid.innerHTML = "";
    grid.appendChild(empty);
    return;
  }

  grid.innerHTML = "";
  profile.devices.forEach(dev => {
    const battClass = dev.battery == null ? "" :
      dev.battery <= 20 ? "bat-low" : dev.battery <= 50 ? "bat-mid" : "bat-ok";
    const lastSeenText = dev.last_seen
      ? timeAgo(dev.last_seen)
      : "never";

    const card = document.createElement("div");
    card.className = `device-card status-${dev.status}`;
    card.innerHTML = `
      <div class="device-card-top">
        <div class="device-status-dot ${dev.status}"></div>
        <span class="device-name">${escapeHtml(dev.friendly_name)}</span>
      </div>
      <div class="device-id">${dev.device_id}</div>
      <div class="device-meta" style="margin-top:8px;">
        <span class="meta-pill">${dev.status}</span>
        <span class="meta-pill ${battClass}">🔋 ${dev.battery ?? "—"}%</span>
        <span class="meta-pill">Last seen: ${lastSeenText}</span>
      </div>
      <div class="device-actions">
        <button class="btn-danger" onclick="removeDeviceConfirm('${dev.device_id}')">Remove</button>
      </div>
    `;
    grid.appendChild(card);
  });
}

function timeAgo(unixTs) {
  const diff = Math.floor(Date.now() / 1000) - unixTs;
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

// ── Add device modal ──
document.getElementById("add-device-btn").addEventListener("click", () => {
  document.getElementById("dev-name").value = "";
  document.getElementById("dev-id-preview").textContent =
    currentUserId ? `${currentUserId}_XXXXX` : "—";
  document.getElementById("device-modal").classList.remove("hidden");
});
document.getElementById("cancel-add-device").addEventListener("click", () => {
  document.getElementById("device-modal").classList.add("hidden");
});
document.getElementById("confirm-add-device").addEventListener("click", async () => {
  const name = document.getElementById("dev-name").value.trim();
  if (!name) { showToast("Enter a device nickname.", "error"); return; }
  try {
    const dev = await api("POST", "/user/devices/register", {
      user_id: currentUserId, friendly_name: name
    });
    showToast(`Device "${name}" registered: ${dev.device_id}`, "success");
    document.getElementById("device-modal").classList.add("hidden");
    loadHome();
  } catch (err) {
    showToast(err.message, "error");
  }
});

async function removeDeviceConfirm(deviceId) {
  if (!confirm(`Remove device ${deviceId}? This cannot be undone.`)) return;
  try {
    await api("DELETE", `/user/devices/${deviceId}`);
    showToast("Device removed.", "success");
    loadHome();
  } catch (err) {
    showToast(err.message, "error");
  }
}

document.getElementById("goto-contacts").addEventListener("click", () => showView("contacts"));

// ─────────────────────────────────────────────────────────────────────────────
// CONTACTS VIEW
// ─────────────────────────────────────────────────────────────────────────────

async function loadContacts() {
  if (!currentUserId) return;
  try {
    const contacts = await api("GET", `/user/${currentUserId}/contacts`);
    renderContacts(contacts);
    renderContactsSummary(contacts);
  } catch (err) {
    showToast(err.message, "error");
  }
}

function renderContactsSummary(contacts) {
  const el = document.getElementById("contacts-summary");
  if (!currentView === "home") return;
  if (!contacts.length) {
    el.innerHTML = `<div class="empty-state" style="padding:14px;">No emergency contacts added yet.</div>`;
    return;
  }
  el.innerHTML = contacts.map(c => `
    <div class="contact-summary-row">
      <span class="contact-priority-badge">${c.priority}</span>
      <span class="contact-summary-name">${escapeHtml(c.name)}</span>
      <span class="contact-summary-phone">${c.phone}</span>
    </div>
  `).join("");
}

function renderContacts(contacts) {
  const el = document.getElementById("contact-cards");
  const showAddBtn = document.getElementById("show-add-contact-btn");

  if (!contacts.length) {
    el.innerHTML = `<div class="empty-state">No emergency contacts yet. Add up to 3.</div>`;
  } else {
    el.innerHTML = contacts.map(c => `
      <div class="contact-card">
        <div class="contact-card-top">
          <span class="priority-badge">P${c.priority}</span>
          <span class="contact-card-name">${escapeHtml(c.name)}</span>
        </div>
        <div class="contact-card-detail">📞 ${c.phone}</div>
        ${c.email ? `<div class="contact-card-detail">✉️ ${c.email}</div>` : ""}
        <div class="contact-notify-pills">
          <span class="notify-pill ${c.notify_sms ? "active" : ""}">SMS</span>
          <span class="notify-pill ${c.notify_whatsapp ? "active" : ""}">WhatsApp</span>
          <span class="notify-pill ${c.notify_email ? "active" : ""}">Email</span>
        </div>
        <div class="contact-actions">
          <button class="btn-danger" onclick="removeContact(${c.id})">Remove</button>
        </div>
      </div>
    `).join("");
  }

  showAddBtn.style.display = contacts.length >= 3 ? "none" : "block";
}

document.getElementById("show-add-contact-btn").addEventListener("click", () => {
  document.getElementById("add-contact-form").classList.remove("hidden");
  document.getElementById("show-add-contact-btn").classList.add("hidden");
});
document.getElementById("cancel-contact-btn").addEventListener("click", () => {
  document.getElementById("add-contact-form").classList.add("hidden");
  document.getElementById("show-add-contact-btn").classList.remove("hidden");
});

document.getElementById("save-contact-btn").addEventListener("click", async () => {
  const name  = document.getElementById("c-name").value.trim();
  const phone = normalisePhone(document.getElementById("c-phone").value.trim());
  const email = document.getElementById("c-email").value.trim() || null;
  const priority = parseInt(document.getElementById("c-priority").value, 10);
  const notify_sms      = document.getElementById("c-sms").checked;
  const notify_whatsapp = document.getElementById("c-wa").checked;
  const notify_email    = document.getElementById("c-email-chk").checked;

  if (!name || !phone) { showToast("Name and phone are required.", "error"); return; }

  try {
    await api("POST", `/user/${currentUserId}/contacts`, {
      name, phone, email, priority, notify_sms, notify_whatsapp, notify_email
    });
    showToast("Contact added.", "success");
    document.getElementById("add-contact-form").classList.add("hidden");
    document.getElementById("show-add-contact-btn").classList.remove("hidden");
    ["c-name","c-phone","c-email"].forEach(id => document.getElementById(id).value = "");
    loadContacts();
  } catch (err) {
    showToast(err.message, "error");
  }
});

async function removeContact(contactId) {
  if (!confirm("Remove this emergency contact?")) return;
  try {
    await api("DELETE", `/user/contacts/${contactId}`);
    showToast("Contact removed.", "success");
    loadContacts();
  } catch (err) {
    showToast(err.message, "error");
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// PREFERENCES VIEW
// ─────────────────────────────────────────────────────────────────────────────

async function loadPreferences() {
  if (!currentUserId) return;
  try {
    const p = await api("GET", `/user/${currentUserId}/preferences`);
    document.getElementById("pref-emergency").checked = p.notify_on_emergency;
    document.getElementById("pref-escalation").checked = p.notify_on_escalation;
    document.getElementById("pref-battery").checked = p.notify_on_low_battery;
    document.getElementById("pref-quiet-enabled").checked = p.quiet_hours_enabled;
    document.getElementById("pref-quiet-start").value = p.quiet_hours_start || "22:00";
    document.getElementById("pref-quiet-end").value = p.quiet_hours_end || "07:00";
    toggleQuietFields();
  } catch (err) {
    showToast(err.message, "error");
  }
}

function toggleQuietFields() {
  const enabled = document.getElementById("pref-quiet-enabled").checked;
  document.getElementById("quiet-hours-fields").classList.toggle("hidden", !enabled);
}
document.getElementById("pref-quiet-enabled").addEventListener("change", toggleQuietFields);

document.getElementById("save-prefs-btn").addEventListener("click", async () => {
  try {
    await api("PATCH", `/user/${currentUserId}/preferences`, {
      notify_on_emergency:   document.getElementById("pref-emergency").checked,
      notify_on_escalation:  document.getElementById("pref-escalation").checked,
      notify_on_low_battery: document.getElementById("pref-battery").checked,
      quiet_hours_enabled:   document.getElementById("pref-quiet-enabled").checked,
      quiet_hours_start:     document.getElementById("pref-quiet-start").value,
      quiet_hours_end:       document.getElementById("pref-quiet-end").value,
    });
    showToast("Settings saved.", "success");
  } catch (err) {
    showToast(err.message, "error");
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// INCIDENTS VIEW
// ─────────────────────────────────────────────────────────────────────────────

async function loadIncidents() {
  if (!currentUserId) return;
  try {
    const incidents = await api("GET", `/user/${currentUserId}/incidents`);
    renderIncidents(incidents);
  } catch (err) {
    showToast(err.message, "error");
  }
}

function renderIncidents(incidents) {
  const el = document.getElementById("incident-list");
  if (!incidents.length) {
    el.innerHTML = `<div class="empty-state">No incidents recorded yet.</div>`;
    return;
  }
  el.innerHTML = incidents.map(inc => {
    const started = new Date(inc.started_at * 1000).toLocaleString();
    const duration = inc.ended_at
      ? formatDuration(inc.ended_at - inc.started_at)
      : "ongoing";
    return `
      <div class="incident-card" onclick="viewIncidentReplay('${inc.incident_id}')">
        <div class="incident-top">
          <span class="incident-status ${inc.status}">${inc.status.toUpperCase()}</span>
          <span class="incident-id">${inc.incident_id}</span>
        </div>
        <div class="incident-time">${started}</div>
        <div class="incident-meta">Device: ${inc.device_id}</div>
        <div class="incident-duration">Duration: ${duration}</div>
      </div>
    `;
  }).join("");
}

function formatDuration(seconds) {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds/60)}m ${seconds%60}s`;
  return `${Math.floor(seconds/3600)}h ${Math.floor((seconds%3600)/60)}m`;
}

function viewIncidentReplay(incidentId) {
  // Placeholder: full replay UI (map + timeline scrubber) is a Trial_Dashboard-style
  // feature to be built out — for now, deep-link to the live dashboard's replay
  // panel with this incident's device ID pre-filled.
  showToast(`Replay for ${incidentId} — open the live dashboard's Replay panel with this device ID.`, "");
}

// ─────────────────────────────────────────────────────────────────────────────
// Boot
// ─────────────────────────────────────────────────────────────────────────────

onAuthStateChanged(auth, (user) => {
  if (user) {
    setLoggedIn(user.uid);
  } else {
    currentUserId = null;
    localStorage.removeItem("resqnet_user_id");
    showView("auth");
  }
});

// Expose globals for inline event handlers (since this is now a module)
window.removeDeviceConfirm = removeDeviceConfirm;
window.removeContact = removeContact;
window.viewIncidentReplay = viewIncidentReplay;