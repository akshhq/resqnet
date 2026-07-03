// ═══════════════════════════════════════════════════════════════════
//   OTP REGISTRATION — FRONTEND
// ═══════════════════════════════════════════════════════════════════
// Talks to the Apps Script web app deployed from OTP_Registration_Backend.gs.
//
// Flow:
//  1. registerUser(...)  -> POSTs form data, gets back an encrypted payload.
//     We decrypt it locally purely for instant UI feedback (e.g. shaking
//     the input red the moment a wrong digit is typed, before any network
//     round trip). This decrypted value is NEVER what actually authorizes
//     registration.
//  2. verifyOtp(...)     -> POSTs the entered OTP + regId to the backend's
//     action=verify endpoint. The backend independently checks the OTP
//     hash and the 10-minute expiry server-side. THIS is the call that
//     actually completes registration — trust the server's response, not
//     the client-side decrypt.
//
// SECURITY NOTE: SHARED_KEY below must match SHARED_KEY in the Apps
// Script backend. Because this file ships to the browser, that key is
// technically readable by anyone who opens dev tools — which is exactly
// why step 2 (server-side verify) is mandatory rather than optional.
// Never treat the client-side decrypt result as the source of truth for
// "is this user verified."

const WEB_APP_URL = window.RESQNET_WEB_APP_URL || "PUT_YOUR_DEPLOYED_APPS_SCRIPT_WEB_APP_URL_HERE";
const SHARED_KEY   = window.RESQNET_SHARED_KEY  || "REPLACE_WITH_A_LONG_RANDOM_SECRET_-_SHARE_ONLY_WITH_FRONTEND";

let pendingRegId = null;
let pendingExpiresAt = null;
let otpExpiryTimer = null;

// ── Step 1: Register ──────────────────────────────────────────────
async function registerUser({ name, dob, phone, email, password }) {
  const res = await fetch(WEB_APP_URL, {
    method: "POST",
    headers: { "Content-Type": "text/plain;charset=utf-8" }, // avoids CORS preflight on Apps Script
    body: JSON.stringify({ action: "register", name, dob, phone, email, password }),
  });
  const data = await res.json();

  if (!data.success) {
    throw new Error(data.error || "Registration failed");
  }

  pendingRegId = data.regId;
  pendingExpiresAt = data.expiresAt;

  // Decrypt locally, for instant client-side feedback only.
  const decrypted = await decrypt(data.encryptedPayload, SHARED_KEY);
  const { otp: previewOtp } = JSON.parse(decrypted);

  startOtpExpiryCountdown(pendingExpiresAt);

  return { regId: pendingRegId, expiresAt: pendingExpiresAt, _previewOtp: previewOtp };
  // _previewOtp exists only so you can do instant-feedback UI (e.g. disable
  // "Verify" button while the typed digits don't match yet). Do NOT use it
  // to decide the user is registered — call verifyOtp() for that.
}

// ── Step 2: Verify (authoritative) ──────────────────────────────────
async function verifyOtp(enteredOtp) {
  if (!pendingRegId) throw new Error("No registration in progress");
  if (Date.now() > pendingExpiresAt) throw new Error("OTP expired. Please resend.");

  const res = await fetch(WEB_APP_URL, {
    method: "POST",
    headers: { "Content-Type": "text/plain;charset=utf-8" },
    body: JSON.stringify({ action: "verify", regId: pendingRegId, otp: enteredOtp }),
  });
  const data = await res.json();

  if (data.success) {
    clearTimeout(otpExpiryTimer);
    pendingRegId = null;
  }
  return data; // { success, message } or { success:false, error }
}

// ── Optional: Resend ──────────────────────────────────────────────
async function resendOtp(email) {
  const res = await fetch(WEB_APP_URL, {
    method: "POST",
    headers: { "Content-Type": "text/plain;charset=utf-8" },
    body: JSON.stringify({ action: "resend", email }),
  });
  const data = await res.json();
  if (data.success) {
    pendingRegId = data.regId;
    pendingExpiresAt = data.expiresAt;
    startOtpExpiryCountdown(pendingExpiresAt);
  }
  return data;
}

// ── 10-minute client-side countdown (UX only; server enforces the real limit) ──
function startOtpExpiryCountdown(expiresAt) {
  clearTimeout(otpExpiryTimer);
  const msLeft = expiresAt - Date.now();
  otpExpiryTimer = setTimeout(() => {
    pendingRegId = null;
    document.dispatchEvent(new CustomEvent("otp-expired"));
  }, Math.max(msLeft, 0));
}

// ═══════════════════════════════════════════════════════════════════
//   CRYPTO — mirrors the HMAC-keystream cipher in the Apps Script backend
// ═══════════════════════════════════════════════════════════════════

async function decrypt(payload, key) {
  const [nonceB64, cipherB64] = payload.split(":");
  const nonceBytes = base64ToBytes(nonceB64);
  const cipherBytes = base64ToBytes(cipherB64);

  const keystream = await deriveKeystream(nonceBytes, key, cipherBytes.length);
  const plainBytes = cipherBytes.map((b, i) => b ^ keystream[i]);

  return new TextDecoder().decode(new Uint8Array(plainBytes));
}

async function deriveKeystream(nonceBytes, key, length) {
  const cryptoKey = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(key),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );

  let stream = [];
  let counter = 0;
  while (stream.length < length) {
    const input = new Uint8Array([...nonceBytes, counter]);
    const sig = await crypto.subtle.sign("HMAC", cryptoKey, input);
    stream = stream.concat(Array.from(new Uint8Array(sig)));
    counter++;
  }
  return stream.slice(0, length);
}

function base64ToBytes(b64) {
  const bin = atob(b64);
  return Array.from(bin, (c) => c.charCodeAt(0));
}

// ═══════════════════════════════════════════════════════════════════
//   EXAMPLE WIRING (adapt to your dashboard's actual form/DOM)
// ═══════════════════════════════════════════════════════════════════
/*
document.getElementById("registerForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const { regId } = await registerUser({
      name: form.name.value,
      dob: form.dob.value,
      phone: form.phone.value,
      email: form.email.value,
      password: form.password.value,
    });
    showOtpStep(); // reveal the OTP input UI
  } catch (err) {
    showError(err.message);
  }
});

document.getElementById("verifyForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const result = await verifyOtp(otpInput.value);
  if (result.success) {
    // registration fully complete — redirect, show success, etc.
  } else {
    showError(result.error);
  }
});

document.addEventListener("otp-expired", () => {
  showError("Your code expired. Please request a new one.");
});
*/
