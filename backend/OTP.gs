// ═══════════════════════════════════════════════════════════════════
//   OTP REGISTRATION BACKEND (Google Apps Script Web App)
// ═══════════════════════════════════════════════════════════════════
// Deploy this as a Web App (Deploy > New deployment > Web app).
// Execute as: Me | Who has access: Anyone (or "Anyone with Google account"
// if you want extra gating — your dashboard will call it via fetch()).
//
// Your dashboard calls this with POST requests, action-routed:
//   action=register  -> creates a pending user, sends OTP email,
//                        returns an encrypted payload for client-side UX
//   action=verify     -> AUTHORITATIVE check. Confirms OTP + 10-min expiry
//                        server-side. This is what actually completes
//                        registration.
//   action=resend      -> issues a fresh OTP if the old one expired/was lost
//
// Deployed at (production): see backend/.env -> Web_App_URL
//
// ================== CONFIG ==================
const SHEET_ID     = "1B7FDx0mVVKscbIh9iYZ569LqrnLy4dO_bHcLYGulLIg";
const REG_TAB      = "Registrations";
const OTP_TTL_MS   = 10 * 60 * 1000; // 10 minutes, per spec

// Shared secret used to derive the HMAC keystream for encryption AND to
// hash OTPs/passwords before storing them. Keep this identical to the
// SHARED_KEY constant in otp-frontend.js.
// IMPORTANT: move this into Script Properties (File > Project properties)
// rather than hardcoding before you ship — hardcoding it here is only for
// clarity in this sample.
const SHARED_KEY = "44eaa2e5b2dd6acee7cda2dce6f8499036cf272b761ad765970ec23fb385024d";

// Registrations sheet columns:
// RegID | Name | DOB | Phone | Email | PasswordHash | OTPHash | CreatedAt |
// ExpiresAt | Status | VerifiedAt
const R = {
  REG_ID: 1, NAME: 2, DOB: 3, PHONE: 4, EMAIL: 5, PASSWORD_HASH: 6,
  OTP_HASH: 7, CREATED_AT: 8, EXPIRES_AT: 9, STATUS: 10, VERIFIED_AT: 11,
};

// ================== WEB APP ENTRY POINT ==================
function doPost(e) {
  var out;
  try {
    var body = JSON.parse(e.postData.contents || "{}");
    var action = String(body.action || "").toLowerCase();

    if (action === "register") out = handleRegister(body);
    else if (action === "verify") out = handleVerify(body);
    else if (action === "resend") out = handleResend(body);
    else out = { success: false, error: "Unknown action" };

  } catch (err) {
    out = { success: false, error: "Server error: " + err };
  }
  return ContentService.createTextOutput(JSON.stringify(out))
    .setMimeType(ContentService.MimeType.JSON);
}

// ================== REGISTER ==================
function handleRegister(body) {
  var name  = String(body.name  || "").trim();
  var dob   = String(body.dob   || "").trim();
  var phone = String(body.phone || "").trim();
  var email = String(body.email || "").trim().toLowerCase();
  var password = String(body.password || "");

  if (!name || !dob || !phone || !email || !password) {
    return { success: false, error: "Missing required fields" };
  }
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    return { success: false, error: "Invalid email address" };
  }

  var sheet = getRegSheet();
  var existingRow = findRowByEmail(sheet, email);

  // Block re-registration if a still-valid pending OTP exists, or the
  // account is already verified.
  if (existingRow) {
    var status  = sheet.getRange(existingRow, R.STATUS).getValue();
    var expires = sheet.getRange(existingRow, R.EXPIRES_AT).getValue();
    if (status === "VERIFIED") {
      return { success: false, error: "Email already registered and verified" };
    }
    if (status === "PENDING" && new Date(expires).getTime() > Date.now()) {
      return { success: false, error: "A pending OTP already exists for this email. Please check your inbox or wait for it to expire." };
    }
    // Otherwise: expired/failed pending row -> fall through and overwrite it
  }

  var regId = "REG-" + Utilities.getUuid().split("-")[0].toUpperCase();
  var otp   = generateOtp();
  var now   = new Date();
  var expiresAt = new Date(now.getTime() + OTP_TTL_MS);

  var passwordHash = hmacHex(password, SHARED_KEY + regId); // per-record salt
  var otpHash      = hmacHex(otp, SHARED_KEY + regId);

  var rowData = [regId, name, dob, phone, email, passwordHash, otpHash, now, expiresAt, "PENDING", ""];
  if (existingRow) {
    sheet.getRange(existingRow, 1, 1, rowData.length).setValues([rowData]);
  } else {
    sheet.appendRow(rowData);
  }

  sendOtpEmail(email, name, otp);

  // Encrypted payload is for CLIENT-SIDE UX ONLY (instant "wrong code"
  // feedback). The frontend must still call action=verify — that call is
  // the one that actually completes registration.
  var payload = JSON.stringify({ otp: otp, expiresAt: expiresAt.getTime(), regId: regId });
  var encryptedPayload = encrypt(payload, SHARED_KEY);

  return { success: true, regId: regId, encryptedPayload: encryptedPayload, expiresAt: expiresAt.getTime() };
}

// ================== VERIFY (authoritative) ==================
function handleVerify(body) {
  var regId = String(body.regId || "").trim();
  var otp   = String(body.otp   || "").trim();

  if (!regId || !otp) return { success: false, error: "Missing regId or otp" };

  var sheet = getRegSheet();
  var row = findRowByRegId(sheet, regId);
  if (!row) return { success: false, error: "Registration not found" };

  var status  = sheet.getRange(row, R.STATUS).getValue();
  var expires = new Date(sheet.getRange(row, R.EXPIRES_AT).getValue()).getTime();
  var otpHash = sheet.getRange(row, R.OTP_HASH).getValue();

  if (status === "VERIFIED") {
    return { success: false, error: "Already verified" };
  }
  if (Date.now() > expires) {
    sheet.getRange(row, R.STATUS).setValue("EXPIRED");
    return { success: false, error: "OTP expired. Please request a new one." };
  }
  if (hmacHex(otp, SHARED_KEY + regId) !== otpHash) {
    return { success: false, error: "Incorrect OTP" };
  }

  sheet.getRange(row, R.STATUS).setValue("VERIFIED");
  sheet.getRange(row, R.VERIFIED_AT).setValue(new Date());
  return { success: true, message: "Registration complete" };
}

// ================== RESEND ==================
function handleResend(body) {
  var email = String(body.email || "").trim().toLowerCase();
  if (!email) return { success: false, error: "Missing email" };

  var sheet = getRegSheet();
  var row = findRowByEmail(sheet, email);
  if (!row) return { success: false, error: "No pending registration for this email" };

  var status = sheet.getRange(row, R.STATUS).getValue();
  if (status === "VERIFIED") return { success: false, error: "Already verified" };

  var regId = sheet.getRange(row, R.REG_ID).getValue();
  var name  = sheet.getRange(row, R.NAME).getValue();
  var otp   = generateOtp();
  var now   = new Date();
  var expiresAt = new Date(now.getTime() + OTP_TTL_MS);

  sheet.getRange(row, R.OTP_HASH).setValue(hmacHex(otp, SHARED_KEY + regId));
  sheet.getRange(row, R.CREATED_AT).setValue(now);
  sheet.getRange(row, R.EXPIRES_AT).setValue(expiresAt);
  sheet.getRange(row, R.STATUS).setValue("PENDING");

  sendOtpEmail(email, name, otp);

  var payload = JSON.stringify({ otp: otp, expiresAt: expiresAt.getTime(), regId: regId });
  var encryptedPayload = encrypt(payload, SHARED_KEY);

  return { success: true, regId: regId, encryptedPayload: encryptedPayload, expiresAt: expiresAt.getTime() };
}

// ================== SHEET HELPERS ==================
function getRegSheet() {
  var ss = SpreadsheetApp.openById(SHEET_ID);
  var sheet = ss.getSheetByName(REG_TAB);
  if (!sheet) {
    sheet = ss.insertSheet(REG_TAB);
    sheet.appendRow(["RegID", "Name", "DOB", "Phone", "Email", "PasswordHash", "OTPHash", "CreatedAt", "ExpiresAt", "Status", "VerifiedAt"]);
    sheet.setFrozenRows(1);
  }
  return sheet;
}

function findRowByEmail(sheet, email) {
  var lastRow = sheet.getLastRow();
  if (lastRow < 2) return null;
  var emails = sheet.getRange(2, R.EMAIL, lastRow - 1, 1).getValues();
  for (var i = 0; i < emails.length; i++) {
    if (String(emails[i][0]).trim().toLowerCase() === email) return i + 2;
  }
  return null;
}

function findRowByRegId(sheet, regId) {
  var lastRow = sheet.getLastRow();
  if (lastRow < 2) return null;
  var ids = sheet.getRange(2, R.REG_ID, lastRow - 1, 1).getValues();
  for (var i = 0; i < ids.length; i++) {
    if (String(ids[i][0]).trim() === regId) return i + 2;
  }
  return null;
}

// ================== OTP / CRYPTO HELPERS ==================
function generateOtp() {
  return String(Math.floor(100000 + Math.random() * 900000)); // 6 digits
}

// HMAC-SHA256 -> hex string. Used for hashing OTPs/passwords (one-way).
function hmacHex(message, key) {
  var raw = Utilities.computeHmacSha256Signature(message, key);
  return raw.map(function(b) {
    var v = (b < 0 ? b + 256 : b).toString(16);
    return v.length === 1 ? "0" + v : v;
  }).join("");
}

// HMAC-based stream cipher: keystream = HMAC(nonce + counter, key),
// XORed against the plaintext bytes. Reversible (encrypt === decrypt
// given the same nonce), and easy to replicate in browser JS with
// SubtleCrypto so both sides derive the same keystream.
function encrypt(plainText, key) {
  var nonceBytes = [];
  for (var i = 0; i < 8; i++) nonceBytes.push(Math.floor(Math.random() * 256));
  var nonceB64 = Utilities.base64Encode(nonceBytes);

  var plainBytes = Utilities.newBlob(plainText).getBytes();
  var keystream = deriveKeystream(nonceBytes, key, plainBytes.length);

  var cipherBytes = plainBytes.map(function(b, i) {
    return (b ^ keystream[i]) & 0xff;
  });

  return nonceB64 + ":" + Utilities.base64Encode(cipherBytes);
}

function deriveKeystream(nonceBytes, key, length) {
  var stream = [];
  var counter = 0;
  while (stream.length < length) {
    var block = Utilities.computeHmacSha256Signature(
      nonceBytes.concat([counter]), key
    );
    stream = stream.concat(block.map(function(b) { return b < 0 ? b + 256 : b; }));
    counter++;
  }
  return stream.slice(0, length);
}

// ================== EMAIL ==================
function sendOtpEmail(email, name, otp) {
  var subject = "Your verification code";
  var plain = "Your verification code is " + otp + ". It expires in 10 minutes.";
  GmailApp.sendEmail(email, subject, plain, {
    name: "Account Verification",
    htmlBody: buildOtpEmailHtml(name, otp),
  });
}

function buildOtpEmailHtml(name, otp) {
  var safeName = esc(name || "there");
  return "<!DOCTYPE html><html><body style='margin:0;padding:0;background:#f5f5f7;font-family:Helvetica,Arial,sans-serif;'>" +
    "<table width='100%' cellpadding='0' cellspacing='0'><tr><td align='center' style='padding:40px 16px;'>" +
    "<table width='420' cellpadding='0' cellspacing='0' style='max-width:420px;width:100%;background:#ffffff;border-radius:12px;border:1px solid #e5e5e7;'>" +
    "<tr><td style='padding:32px 32px 8px;'>" +
    "<p style='margin:0 0 20px;font-size:14px;color:#6e6e73;'>Hi " + safeName + ",</p>" +
    "<p style='margin:0 0 24px;font-size:14px;color:#6e6e73;line-height:1.5;'>Use the code below to verify your email address. This code expires in 10 minutes.</p>" +
    "</td></tr>" +
    "<tr><td align='center' style='padding:0 32px 24px;'>" +
    "<div style='display:inline-block;background:#f5f5f7;border-radius:8px;padding:16px 28px;font-size:32px;font-weight:600;letter-spacing:8px;color:#1d1d1f;'>" + esc(otp) + "</div>" +
    "</td></tr>" +
    "<tr><td style='padding:0 32px 32px;'>" +
    "<p style='margin:0;font-size:12px;color:#a1a1a6;line-height:1.5;'>If you didn't request this, you can safely ignore this email.</p>" +
    "</td></tr>" +
    "</table></td></tr></table></body></html>";
}

function esc(str) {
  return String(str || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}