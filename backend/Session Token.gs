// ═══════════════════════════════════════════════════════════════════
//   EMERGENCY SESSION TOKEN BACKEND (Google Apps Script Web App)
// ═══════════════════════════════════════════════════════════════════

// ================== CONFIG ==================
// The base URL of the live hosted Responder Dashboard
const DASHBOARD_BASE_URL = "https://aksh.is-a.dev/resqnet/frontend/responder_dashboard/index.html";

// Fixed list of responder emails who get notified of every emergency
const RESPONDER_EMAILS = ["kumaraksh1107@gmail.com"]; // Replace with your email or Google Group

const SHEET_ID        = "1J3t8UhsigJrw9BKgV6ya6U8hTUVhiSf3anFtoUcg4MA";
const SESSIONS_TAB    = "EmergencySessions";
const TOKEN_CHARS     = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
const TOKEN_LENGTH    = 6;
const TOKEN_TTL_HOURS = 24;

// EmergencySessions sheet columns map:
const S = {
  TOKEN: 1, USER_ID: 2, DEVICE_ID: 3, NAME: 4, LAT: 5, LNG: 6,
  CREATED_AT: 7, EXPIRES_AT: 8, STATUS: 9, RESOLVED_AT: 10, RESOLVED_BY: 11,
  CONTACT_EMAILS: 12
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
    out = { success: false, error: "Server error: " + err.toString() };
  }
  return ContentService.createTextOutput(JSON.stringify(out))
    .setMimeType(ContentService.MimeType.JSON);
}

// Allow verification via browser GET request
function doGet(e) {
  return ContentService.createTextOutput(JSON.stringify({ 
    success: true, 
    message: "ResQNet Web App is active and running!" 
  })).setMimeType(ContentService.MimeType.JSON);
}

// ================== TRIGGER ==================
function handleTrigger(body) {
  var userId   = String(body.userID   || "").trim();
  var deviceId = String(body.deviceID || "").trim();
  var name     = String(body.name     || "").trim();
  var lat      = body.lat != null ? Number(body.lat) : null;
  var lng      = body.lng != null ? Number(body.lng) : null;
  var contactEmails = Array.isArray(body.contactEmails)
    ? body.contactEmails.filter(function (x) { return typeof x === "string" && x.indexOf("@") > -1; })
    : [];
  var responderEmails = Array.isArray(body.responderEmails)
    ? body.responderEmails.filter(function (x) { return typeof x === "string" && x.indexOf("@") > -1; })
    : [];

  if (!userId || !deviceId || !name) {
    return { success: false, error: "Missing userID, deviceID, or name" };
  }

  var sheet = getOrCreateSheet();

  // Reuse active token if one already exists for this device/user
  var existingRow = findActiveRowByUserDevice(sheet, userId, deviceId);
  if (existingRow) {
    var token = sheet.getRange(existingRow, S.TOKEN).getValue();
    return {
      success: true,
      reused: true,
      token: token,
      link: buildLink(userId, token),
      expiresAt: new Date(sheet.getRange(existingRow, S.EXPIRES_AT).getValue()).getTime()
    };
  }

  var token = generateUniqueToken(sheet);
  var now = new Date();
  var expiresAt = new Date(now.getTime() + TOKEN_TTL_HOURS * 60 * 60 * 1000);

  sheet.appendRow([
    token, userId, deviceId, name,
    lat != null ? lat : "", lng != null ? lng : "",
    now, expiresAt, "ACTIVE", "", "",
    contactEmails.join(",")
  ]);

  var link = buildLink(userId, token);
  var emailErrors = [];
  var errors1 = sendEmergencyEmail({ userId, name, deviceId, lat, lng, link, responderEmails: responderEmails });
  emailErrors = emailErrors.concat(errors1);
  if (contactEmails.length) {
    var errors2 = sendContactAlertEmails({ contactEmails, name, link });
    emailErrors = emailErrors.concat(errors2);
  }

  return { 
    success: true, 
    reused: false, 
    token: token, 
    link: link, 
    expiresAt: expiresAt.getTime(),
    emailErrors: emailErrors 
  };
}

// ================== VALIDATE ==================
function handleValidate(body) {
  var userId = String(body.userID || "").trim();
  var token  = String(body.token  || "").trim().toUpperCase();

  if (!userId || !token) return { success: false, error: "Missing userID or token" };

  var sheet = getOrCreateSheet();
  var row = findRowByToken(sheet, token);

  if (!row) return { success: false, error: "Invalid or unknown session link" };

  var rowUserId = String(sheet.getRange(row, S.USER_ID).getValue()).trim();
  if (rowUserId !== userId) return { success: false, error: "Invalid session link" };

  var status = sheet.getRange(row, S.STATUS).getValue();
  if (status === "RESOLVED") return { success: false, error: "This incident has already been resolved" };

  // Parse expiration date cleanly
  var expiresVal = sheet.getRange(row, S.EXPIRES_AT).getValue();
  var expiresAt = (expiresVal instanceof Date) ? expiresVal.getTime() : new Date(expiresVal).getTime();
  
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
      status: status
    }
  };
}

// ================== RESOLVE ==================
function handleResolve(body) {
  var userId = String(body.userID || "").trim();
  var token  = String(body.token  || "").trim().toUpperCase();
  var resolvedBy = String(body.resolvedBy || "responder").trim();

  if (!userId || !token) return { success: false, error: "Missing userID or token" };

  var sheet = getOrCreateSheet();
  var row = findRowByToken(sheet, token);

  if (!row) return { success: false, error: "Session token not found" };

  var rowUserId = String(sheet.getRange(row, S.USER_ID).getValue()).trim();
  if (rowUserId !== userId) return { success: false, error: "Invalid session user" };

  sheet.getRange(row, S.STATUS).setValue("RESOLVED");
  sheet.getRange(row, S.RESOLVED_AT).setValue(new Date());
  sheet.getRange(row, S.RESOLVED_BY).setValue(resolvedBy);

  return { success: true };
}

// ================== HELPERS ==================

function getOrCreateSheet() {
  var ss;
  if (typeof SHEET_ID !== "undefined" && SHEET_ID) {
    ss = SpreadsheetApp.openById(SHEET_ID);
  } else {
    ss = SpreadsheetApp.getActiveSpreadsheet();
  }
  var sheet = ss.getSheetByName(SESSIONS_TAB);
  if (!sheet) {
    sheet = ss.insertSheet(SESSIONS_TAB);
    // Write headers
    sheet.appendRow([
      "Token", "UserID", "DeviceID", "Name", "Latitude", "Longitude",
      "CreatedAt", "ExpiresAt", "Status", "ResolvedAt", "ResolvedBy", "ContactEmails"
    ]);
  }
  return sheet;
}

function findRowByToken(sheet, token) {
  var rows = sheet.getLastRow();
  for (var r = 2; r <= rows; r++) {
    var t = String(sheet.getRange(r, S.TOKEN).getValue()).trim().toUpperCase();
    if (t === token) return r;
  }
  return null;
}

function findActiveRowByUserDevice(sheet, userId, deviceId) {
  var rows = sheet.getLastRow();
  for (var r = 2; r <= rows; r++) {
    var u = String(sheet.getRange(r, S.USER_ID).getValue()).trim();
    var d = String(sheet.getRange(r, S.DEVICE_ID).getValue()).trim();
    var status = String(sheet.getRange(r, S.STATUS).getValue()).trim();
    if (u === userId && d === deviceId && status === "ACTIVE") {
      return r;
    }
  }
  return null;
}

function generateUniqueToken(sheet) {
  var token;
  var attempts = 0;
  do {
    token = "";
    for (var i = 0; i < TOKEN_LENGTH; i++) {
      token += TOKEN_CHARS.charAt(Math.floor(Math.random() * TOKEN_CHARS.length));
    }
    attempts++;
  } while (findRowByToken(sheet, token) !== null && attempts < 100);
  return token;
}

function buildLink(userId, token) {
  return DASHBOARD_BASE_URL + "?uid=" + encodeURIComponent(userId) + "&token=" + encodeURIComponent(token);
}

function sendEmergencyEmail(p) {
  var subject = "🚨 ResQNet Emergency Alert: " + p.name + " is in distress";
  var body = "ResQNet has detected a critical emergency alert.\n\n" +
             "User: " + p.name + "\n" +
             "Device ID: " + p.deviceId + "\n" +
             "Location: " + (p.lat || "Unknown") + ", " + (p.lng || "Unknown") + "\n\n" +
             "Access the live tracking map immediately here:\n" + p.link + "\n\n" +
             "— ResQNet Response Network Service";

  var targets = RESPONDER_EMAILS.concat(p.responderEmails || []);
  var uniqueTargets = [];
  targets.forEach(function (email) {
    var clean = email.trim().toLowerCase();
    if (clean && uniqueTargets.indexOf(clean) === -1) {
      uniqueTargets.push(clean);
    }
  });

  var errors = [];
  uniqueTargets.forEach(function (email) {
    try {
      MailApp.sendEmail(email, subject, body);
    } catch (e) {
      errors.push("Responder (" + email + "): " + e.toString());
      Logger.log("Failed to send email to responder " + email + ": " + e);
    }
  });
  return errors;
}

function sendContactAlertEmails(p) {
  var subject = "🚨 ResQNet Emergency Alert: Your contact needs help";
  var body = "You are receiving this because you are registered as an emergency contact for " + p.name + ".\n\n" +
             "They have triggered an SOS panic signal. You can track their live location in real time using the link below:\n\n" +
             p.link + "\n\n" +
             "Please take appropriate actions immediately.\n\n" +
             "— ResQNet Response Network Service";

  var errors = [];
  p.contactEmails.forEach(function (email) {
    try {
      MailApp.sendEmail(email.trim(), subject, body);
    } catch (e) {
      errors.push("Contact (" + email + "): " + e.toString());
      Logger.log("Failed to send email to contact " + email + ": " + e);
    }
  });
  return errors;
}