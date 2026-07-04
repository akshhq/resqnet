// ═══════════════════════════════════════════════════════════════════
//   EMERGENCY SESSION TOKEN BACKEND (Google Apps Script Web App)
// ═══════════════════════════════════════════════════════════════════
// Deploy as a Web App (Deploy > New deployment > Web app).
// Execute as: Me | Who has access: Anyone (the FastAPI backend calls this
// server-to-server the moment an emergency starts, so it can't require a
// Google login).
//
// Flow:
//   1. FastAPI backend POSTs action=trigger the moment a device's
//      emergency flag flips on (main.py: device_update()). Body includes
//      userID, deviceID, name, lat/lng, AND contactEmails — the list of
//      this user's emergency-contact addresses (Postgres emergency_contacts
//      table), so both responders and personal contacts get the link.
//   2. This script generates a 6-char [A-Z0-9] session token, stores an
//      ACTIVE incident row, and emails:
//        - RESPONDER_EMAILS (fixed list below) — full "Open Incident
//          Dashboard" email with the live-tracking link.
//        - every address in contactEmails — a shorter "your contact
//          triggered an emergency" email with the same link, so family/
//          friends can also watch the live location feed.
//   3. Responder (or contact) opens the link. The responder dashboard
//      reads uid/token from the URL and calls action=validate on load —
//      no login step, the link itself is the credential.
//   4. When handled, the responder dashboard calls action=resolve, which
//      kills the token immediately (link becomes dead) rather than
//      waiting for the TTL to lapse. The FastAPI backend's own
//      /device/update reset flow independently closes its Postgres
//      incidents row — the two systems don't share a database, so both
//      close paths matter.
//
// Deployed at (production): see backend/.env -> SESSION_TOKEN_WEBAPP_URL
//
// ================== CONFIG ==================
const SHEET_ID          = "1J3t8UhsigJrw9BKgV6ya6U8hTUVhiSf3anFtoUcg4MA";
const SESSIONS_TAB       = "EmergencySessions";
const DASHBOARD_BASE_URL = "https://aksh.is-a.dev/resqnet/frontend/responder_dashboard/index.html";
const RESPONDER_EMAILS   = ["responder1@example.com", "responder2@example.com"]; // or a Google Group address
const TOKEN_CHARS        = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
const TOKEN_LENGTH       = 6;
const TOKEN_TTL_HOURS    = 24; // safety-net expiry; normally closed via "resolve" instead

// EmergencySessions sheet columns:
// Token | UserID | DeviceID | Name | Latitude | Longitude | CreatedAt |
// ExpiresAt | Status | ResolvedAt | ResolvedBy | ContactEmails
const S = {
  TOKEN: 1, USER_ID: 2, DEVICE_ID: 3, NAME: 4, LAT: 5, LNG: 6,
  CREATED_AT: 7, EXPIRES_AT: 8, STATUS: 9, RESOLVED_AT: 10, RESOLVED_BY: 11,
  CONTACT_EMAILS: 12,
};

// ================== WEB APP ENTRY POINT ==================
function doPost(e) {
  var out;
  try {
    var body = JSON.parse(e.postData.contents || "{}");
    var action = String(body.action || "").toLowerCase();

    if (action === "trigger") out = handleTrigger(body);
    else if (action === "validate") out = handleValidate(body);
    else if (action === "resolve") out = handleResolve(body);
    else out = { success: false, error: "Unknown action" };

  } catch (err) {
    out = { success: false, error: "Server error: " + err };
  }
  return ContentService.createTextOutput(JSON.stringify(out))
    .setMimeType(ContentService.MimeType.JSON);
}

// ================== TRIGGER (FastAPI backend calls this) ==================
function handleTrigger(body) {
  var userId   = String(body.userID   || "").trim();
  var deviceId = String(body.deviceID || "").trim();
  var name     = String(body.name     || "").trim();
  var lat      = body.lat != null ? Number(body.lat) : null;
  var lng      = body.lng != null ? Number(body.lng) : null;
  var contactEmails = Array.isArray(body.contactEmails)
    ? body.contactEmails.filter(function (x) { return typeof x === "string" && x.indexOf("@") > -1; })
    : [];

  if (!userId || !deviceId || !name) {
    return { success: false, error: "Missing userID, deviceID, or name" };
  }

  var sheet = getSheet();

  // If this user/device already has a live incident, reuse it instead of
  // spawning a duplicate token + duplicate alert emails.
  var existingRow = findActiveRowByUserDevice(sheet, userId, deviceId);
  if (existingRow) {
    var token = sheet.getRange(existingRow, S.TOKEN).getValue();
    return {
      success: true,
      reused: true,
      token: token,
      link: buildLink(userId, token),
      expiresAt: new Date(sheet.getRange(existingRow, S.EXPIRES_AT).getValue()).getTime(),
    };
  }

  var token = generateUniqueToken(sheet);
  var now = new Date();
  var expiresAt = new Date(now.getTime() + TOKEN_TTL_HOURS * 60 * 60 * 1000);

  sheet.appendRow([
    token, userId, deviceId, name,
    lat != null ? lat : "", lng != null ? lng : "",
    now, expiresAt, "ACTIVE", "", "",
    contactEmails.join(","),
  ]);

  var link = buildLink(userId, token);
  sendEmergencyEmail({ userId, name, deviceId, lat, lng, link });
  if (contactEmails.length) {
    sendContactAlertEmails({ contactEmails, name, link });
  }

  return { success: true, reused: false, token: token, link: link, expiresAt: expiresAt.getTime() };
}

// ================== VALIDATE (responder dashboard calls this on load) ==================
function handleValidate(body) {
  var userId = String(body.userID || "").trim();
  var token  = String(body.token  || "").trim().toUpperCase();

  if (!userId || !token) return { success: false, error: "Missing userID or token" };

  var sheet = getSheet();
  var row = findRowByToken(sheet, token);

  if (!row) return { success: false, error: "Invalid or unknown session link" };

  var rowUserId = String(sheet.getRange(row, S.USER_ID).getValue()).trim();
  if (rowUserId !== userId) return { success: false, error: "Invalid session link" };

  var status = sheet.getRange(row, S.STATUS).getValue();
  if (status === "RESOLVED") return { success: false, error: "This incident has already been resolved" };

  var expiresAt = new Date(sheet.getRange(row, S.EXPIRES_AT).getValue()).getTime();
  if (Date.now() > expiresAt) {
    sheet.getRange(row, S.STATUS).setValue("EXPIRED");
    return { success: false, error: "This session link has expired" };
  }

  return {
    success: true,
    incident: {
      userId: rowUserId,
      name: sheet.getRange(row, S.NAME).getValue(),
      deviceId: sheet.getRange(row, S.DEVICE_ID).getValue(),
      lat: sheet.getRange(row, S.LAT).getValue() || null,
      lng: sheet.getRange(row, S.LNG).getValue() || null,
      createdAt: new Date(sheet.getRange(row, S.CREATED_AT).getValue()).getTime(),
      status: status,
    },
  };
}

// ================== RESOLVE (responder dashboard calls this when handled) ==================
function handleResolve(body) {
  var userId     = String(body.userID     || "").trim();
  var token      = String(body.token      || "").trim().toUpperCase();
  var resolvedBy = String(body.resolvedBy || "").trim();

  if (!userId || !token) return { success: false, error: "Missing userID or token" };

  var sheet = getSheet();
  var row = findRowByToken(sheet, token);
  if (!row) return { success: false, error: "Invalid or unknown session link" };

  var rowUserId = String(sheet.getRange(row, S.USER_ID).getValue()).trim();
  if (rowUserId !== userId) return { success: false, error: "Invalid session link" };

  var status = sheet.getRange(row, S.STATUS).getValue();
  if (status === "RESOLVED") return { success: false, error: "Already resolved" };

  sheet.getRange(row, S.STATUS).setValue("RESOLVED");
  sheet.getRange(row, S.RESOLVED_AT).setValue(new Date());
  sheet.getRange(row, S.RESOLVED_BY).setValue(resolvedBy || "Unspecified responder");

  return { success: true, message: "Incident marked resolved" };
}

// ================== SHEET HELPERS ==================
function getSheet() {
  var ss = SpreadsheetApp.openById(SHEET_ID);
  var sheet = ss.getSheetByName(SESSIONS_TAB);
  if (!sheet) {
    sheet = ss.insertSheet(SESSIONS_TAB);
    sheet.appendRow(["Token", "UserID", "DeviceID", "Name", "Latitude", "Longitude", "CreatedAt", "ExpiresAt", "Status", "ResolvedAt", "ResolvedBy", "ContactEmails"]);
    sheet.setFrozenRows(1);
  }
  return sheet;
}

function findRowByToken(sheet, token) {
  var lastRow = sheet.getLastRow();
  if (lastRow < 2) return null;
  var tokens = sheet.getRange(2, S.TOKEN, lastRow - 1, 1).getValues();
  for (var i = 0; i < tokens.length; i++) {
    if (String(tokens[i][0]).trim() === token) return i + 2;
  }
  return null;
}

function findActiveRowByUserDevice(sheet, userId, deviceId) {
  var lastRow = sheet.getLastRow();
  if (lastRow < 2) return null;
  var data = sheet.getRange(2, 1, lastRow - 1, S.CONTACT_EMAILS).getValues();
  for (var i = 0; i < data.length; i++) {
    var row = data[i];
    var status = row[S.STATUS - 1];
    var expiresAt = new Date(row[S.EXPIRES_AT - 1]).getTime();
    if (
      String(row[S.USER_ID - 1]).trim() === userId &&
      String(row[S.DEVICE_ID - 1]).trim() === deviceId &&
      status === "ACTIVE" &&
      Date.now() < expiresAt
    ) {
      return i + 2;
    }
  }
  return null;
}

// ================== TOKEN GENERATION ==================
function generateUniqueToken(sheet) {
  var existing = getActiveTokenSet(sheet);
  var token;
  do {
    token = randomToken();
  } while (existing.has(token));
  return token;
}

function randomToken() {
  var out = "";
  for (var i = 0; i < TOKEN_LENGTH; i++) {
    out += TOKEN_CHARS.charAt(Math.floor(Math.random() * TOKEN_CHARS.length));
  }
  return out;
}

function getActiveTokenSet(sheet) {
  var set = new Set();
  var lastRow = sheet.getLastRow();
  if (lastRow < 2) return set;
  var tokens = sheet.getRange(2, S.TOKEN, lastRow - 1, 1).getValues();
  tokens.forEach(function(r) { set.add(String(r[0]).trim()); });
  return set;
}

// ================== LINK BUILDING ==================
function buildLink(userId, token) {
  return DASHBOARD_BASE_URL + "?uid=" + encodeURIComponent(userId) + "&token=" + encodeURIComponent(token);
}

// ================== EMAIL — RESPONDERS ==================
function sendEmergencyEmail({ userId, name, deviceId, lat, lng, link }) {
  var subject = "\u26A0 EMERGENCY ALERT — " + name;
  var plain = "EMERGENCY triggered by " + name + " (User ID: " + userId + ").\nOpen the incident: " + link;

  var recipients = RESPONDER_EMAILS.join(",");
  GmailApp.sendEmail(recipients, subject, plain, {
    name: "Emergency Alert System",
    htmlBody: buildEmergencyEmailHtml({ userId, name, deviceId, lat, lng, link }),
  });
}

function buildEmergencyEmailHtml({ userId, name, deviceId, lat, lng, link }) {
  var safeName  = esc(name);
  var safeUid   = esc(userId);
  var safeDevId = esc(deviceId);
  var locationBlock = "";
  if (lat != null && lng != null && lat !== "" && lng !== "") {
    var mapsLink = "https://maps.google.com/?q=" + lat + "," + lng;
    locationBlock =
      "<tr><td style='padding:0 32px 20px;'>" +
        "<a href='" + mapsLink + "' style='display:block;background:#fff5f5;border:1px solid #ffd7d7;border-radius:8px;padding:12px 16px;color:#c0392b;font-size:13px;text-decoration:none;'>" +
          "\uD83D\uDCCD View last known location" +
        "</a>" +
      "</td></tr>";
  }

  return "<!DOCTYPE html><html><body style='margin:0;padding:0;background:#f5f5f7;font-family:Helvetica,Arial,sans-serif;'>" +
    "<table width='100%' cellpadding='0' cellspacing='0'><tr><td align='center' style='padding:40px 16px;'>" +
    "<table width='460' cellpadding='0' cellspacing='0' style='max-width:460px;width:100%;background:#ffffff;border-radius:12px;border:1px solid #ffd7d7;'>" +
    "<tr><td align='center' style='background:#c0392b;padding:18px;border-radius:12px 12px 0 0;'>" +
      "<span style='color:#ffffff;font-size:14px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;'>\u26A0 Emergency Alert</span>" +
    "</td></tr>" +
    "<tr><td style='padding:28px 32px 4px;'>" +
      "<p style='margin:0 0 4px;font-size:13px;color:#a1a1a6;text-transform:uppercase;letter-spacing:0.05em;'>Triggered by</p>" +
      "<p style='margin:0 0 20px;font-size:20px;font-weight:600;color:#1d1d1f;'>" + safeName + "</p>" +
    "</td></tr>" +
    "<tr><td style='padding:0 32px 20px;'>" +
      "<table width='100%' cellpadding='0' cellspacing='0' style='font-size:13px;color:#6e6e73;'>" +
        "<tr><td style='padding:4px 0;width:90px;'>User ID</td><td style='padding:4px 0;color:#1d1d1f;font-family:\"Courier New\",monospace;'>" + safeUid + "</td></tr>" +
        "<tr><td style='padding:4px 0;'>Device ID</td><td style='padding:4px 0;color:#1d1d1f;font-family:\"Courier New\",monospace;'>" + safeDevId + "</td></tr>" +
      "</table>" +
    "</td></tr>" +
    locationBlock +
    "<tr><td align='center' style='padding:0 32px 32px;'>" +
      "<a href='" + link + "' style='display:block;background:#c0392b;color:#ffffff;text-align:center;padding:14px 20px;border-radius:8px;font-size:15px;font-weight:600;text-decoration:none;'>Open Incident Dashboard</a>" +
    "</td></tr>" +
    "<tr><td style='padding:0 32px 28px;'>" +
      "<p style='margin:0;font-size:11px;color:#a1a1a6;line-height:1.5;'>This link opens the responder dashboard directly for this incident. No login required.</p>" +
    "</td></tr>" +
    "</table></td></tr></table></body></html>";
}

// ================== EMAIL — EMERGENCY CONTACTS ==================
// Shorter, personal-toned email sent to the triggering user's own
// emergency contacts (from Postgres emergency_contacts, forwarded in via
// contactEmails). Same link as the responder email, so a family member
// can watch the same live location feed — but the copy is written for a
// worried relative, not an on-duty responder.
function sendContactAlertEmails({ contactEmails, name, link }) {
  var subject = "\u26A0 " + name + " has triggered an emergency alert";
  var plain = name + " has triggered an emergency alert on ResQNet. " +
    "You can view their live location here: " + link;
  var htmlBody = buildContactAlertHtml({ name, link });

  contactEmails.forEach(function (email) {
    try {
      GmailApp.sendEmail(email, subject, plain, {
        name: "ResQNet Emergency Alert",
        htmlBody: htmlBody,
      });
    } catch (err) {
      // One bad address shouldn't stop the rest of the contacts from
      // being notified — log and continue.
      Logger.log("Failed to email emergency contact " + email + ": " + err);
    }
  });
}

function buildContactAlertHtml({ name, link }) {
  var safeName = esc(name);
  return "<!DOCTYPE html><html><body style='margin:0;padding:0;background:#f5f5f7;font-family:Helvetica,Arial,sans-serif;'>" +
    "<table width='100%' cellpadding='0' cellspacing='0'><tr><td align='center' style='padding:40px 16px;'>" +
    "<table width='440' cellpadding='0' cellspacing='0' style='max-width:440px;width:100%;background:#ffffff;border-radius:12px;border:1px solid #ffd7d7;'>" +
    "<tr><td align='center' style='background:#c0392b;padding:18px;border-radius:12px 12px 0 0;'>" +
      "<span style='color:#ffffff;font-size:14px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;'>\u26A0 Emergency Alert</span>" +
    "</td></tr>" +
    "<tr><td style='padding:28px 32px 20px;'>" +
      "<p style='margin:0 0 16px;font-size:15px;color:#1d1d1f;line-height:1.6;'><strong>" + safeName + "</strong> has triggered an emergency alert on ResQNet. You're listed as one of their emergency contacts.</p>" +
      "<p style='margin:0;font-size:14px;color:#6e6e73;line-height:1.6;'>You can view their live location and status using the link below.</p>" +
    "</td></tr>" +
    "<tr><td align='center' style='padding:0 32px 32px;'>" +
      "<a href='" + link + "' style='display:block;background:#c0392b;color:#ffffff;text-align:center;padding:14px 20px;border-radius:8px;font-size:15px;font-weight:600;text-decoration:none;'>View Live Location</a>" +
    "</td></tr>" +
    "</table></td></tr></table></body></html>";
}

function esc(str) {
  return String(str || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}