# Email Queue Integration — Apps Script Contract

> **Note:** registration OTP does **not** use this queue. Registration
> verification is handled entirely by a separate, self-contained Apps
> Script (`OTP_Registration_Backend.gs`) with its own Sheet, its own
> `regId`, and its own OTP round-trip — the frontend talks to it directly
> via `otp-frontend.js`, never through this backend. This document covers
> a *different* mechanism: the generic outbound email queue reserved for
> **Phase 4 emergency alerts** (and any other future transactional email
> that doesn't need its own dedicated OTP-style flow).

This document is the HTTP contract between the ResQNet backend and a
Google Apps Script you write to send those emails via `GmailApp`. The
backend never talks to Google directly — it only maintains a queue table
in Postgres. Your script polls it, sends mail, and reports back.

This mirrors the pattern in your TRYST script (poll → send → mark status),
just against an HTTP API instead of a Sheet tab.

---

## Endpoints

All three require the header `X-API-Key: <your key>` **only if** `API_KEY`
is set in the backend's `.env`. If it's unset (dev mode), the header is
ignored — you can omit it entirely while testing.

Base URL: `http://127.0.0.1:8000` (or wherever the backend is deployed —
Apps Script's `UrlFetchApp` can reach any public URL, so for a script
running in the cloud you'll need the backend reachable at a real address,
not `127.0.0.1`).

### 1. `GET /email-queue/pending?limit=50`

Returns pending jobs, oldest first.

**Response:**
```json
[
  {
    "id": 1,
    "to_email": "aksh@example.com",
    "to_name": "Aksh Kumar",
    "template_type": "email_otp",
    "payload": {
      "code": "755245",
      "purpose": "registration",
      "expires_in_seconds": 300
    },
    "created_at": 1782946921
  }
]
```

Empty array `[]` when there's nothing to send — this is the normal steady
state, not an error.

### 2. `POST /email-queue/{id}/mark-sent`

Call this immediately after `GmailApp.sendEmail()` succeeds for that job.
No request body needed.

**Response:** `{ "status": "marked_sent", "id": 1 }`

### 3. `POST /email-queue/{id}/mark-failed`

Call this if `GmailApp.sendEmail()` throws. Mirrors your `logError()`
pattern.

**Request body:**
```json
{ "error": "GmailApp quota exceeded" }
```

**Response:** `{ "status": "marked_failed", "id": 1 }`

---

## `template_type` values

Right now there are none live yet — this queue is currently empty because
nothing enqueues into it. It exists so Phase 4 doesn't need new
infrastructure when it lands.

### `emergency_alert` (coming in Phase 4 — not live yet)

When emergency notification dispatch is built, a `template_type` called
`emergency_alert` will start appearing in the pending list, carrying an
incident ID, device name, live location, and a responder dashboard link
with an embedded token. Documented here once that phase lands so you can
extend your script's `if/switch` on `template_type` rather than rewrite it.

---

## Suggested Apps Script shape

Following the same structure as your TRYST script — a periodic
`sendPendingEmails()` function on a time-driven trigger (every 1–2 minutes
is plenty for OTP codes given the 5-minute expiry):

```javascript
function sendPendingEmails() {
  const API_BASE = "http://YOUR_BACKEND_HOST:8000";
  const API_KEY  = "..."; // only if backend auth is enabled

  const res = UrlFetchApp.fetch(API_BASE + "/email-queue/pending?limit=50", {
    method: "get",
    headers: API_KEY ? { "X-API-Key": API_KEY } : {},
    muteHttpExceptions: true,
  });

  const jobs = JSON.parse(res.getContentText());

  jobs.forEach(function(job) {
    try {
      if (job.template_type === "email_otp") {
        GmailApp.sendEmail(
          job.to_email,
          "ResQNet — Your Verification Code",
          "Your code is " + job.payload.code + ". It expires in 5 minutes.",
          { name: "ResQNet", htmlBody: buildOtpEmailHtml(job) }
        );
      }
      // future: else if (job.template_type === "emergency_alert") { ... }

      UrlFetchApp.fetch(API_BASE + "/email-queue/" + job.id + "/mark-sent", {
        method: "post",
        headers: API_KEY ? { "X-API-Key": API_KEY } : {},
        muteHttpExceptions: true,
      });
    } catch (err) {
      UrlFetchApp.fetch(API_BASE + "/email-queue/" + job.id + "/mark-failed", {
        method: "post",
        contentType: "application/json",
        headers: API_KEY ? { "X-API-Key": API_KEY } : {},
        payload: JSON.stringify({ error: String(err) }),
        muteHttpExceptions: true,
      });
    }
  });
}
```

This is illustrative, not something for you to copy verbatim — you know
your own script's conventions (error sheet logging, `esc()` helper, HTML
builder functions) far better than this snippet does. The only hard
requirements are: poll `/email-queue/pending`, send via `GmailApp`, report
back via `mark-sent` or `mark-failed`.

---

## Testing without waiting on real email

You can watch the queue directly against Postgres while testing:

```sql
SELECT id, to_email, template_type, payload, status, created_at
FROM email_queue
ORDER BY created_at DESC
LIMIT 10;
```

Or hit `GET /email-queue/pending` from a browser/Postman with your API key
to see exactly what your script will receive.