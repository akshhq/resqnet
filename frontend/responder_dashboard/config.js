// ─────────────────────────────────────────────────────────────────────────────
// ResQNet frontend config — STATIC, committed file. No build step, no
// generation, nothing to run before deploying. Edit these values directly
// if they ever change.
//
// Live-first, local-fallback: on load, resqnetResolveConfig() pings the
// live Render backend's /health endpoint with a short timeout. If it
// answers, RESQNET_CONFIG stays pointed at the live backend. If it
// doesn't (backend asleep, offline, you're doing local-only dev with no
// internet), it falls back to the local backend automatically.
//
// To run everything locally: start the backend with
//   uvicorn app.main:app --reload --port 8000
// and this file will detect it and switch over on its own — no config
// edits needed.
// ─────────────────────────────────────────────────────────────────────────────

window.RESQNET_LIVE = {
  BACKEND_URL: "https://resqnet-gti8.onrender.com",
  WS_URL: "wss://resqnet-gti8.onrender.com/ws/live",
};

window.RESQNET_LOCAL = {
  BACKEND_URL: "http://localhost:8000",
  WS_URL: "ws://localhost:8000/ws/live",
};

// Sane default so any code that reads window.RESQNET_CONFIG before
// resolution finishes still gets a valid object.
window.RESQNET_CONFIG = window.RESQNET_LIVE;

window.firebaseConfig = {
  apiKey: "AIzaSyCfzVv3lM54b9wxV_Z1jouRzNax9Wmjy1Y",
  authDomain: "resqnet-72ee8.firebaseapp.com",
  projectId: "resqnet-72ee8",
  storageBucket: "resqnet-72ee8.firebasestorage.app",
  messagingSenderId: "328390220781",
  appId: "1:328390220781:web:bef2cfd8c6c5549440e5e4",
  measurementId: "G-EQ8F0RFZW9",
};

// Memoized resolver — every dashboard file can call this and they'll all
// share one probe result instead of each pinging /health separately.
window.resqnetResolveConfig = (function () {
  let pending = null;
  return function resolve(timeoutMs) {
    if (pending) return pending;
    timeoutMs = timeoutMs || 2500;
    pending = (async () => {
      try {
        const ctrl = new AbortController();
        const timer = setTimeout(() => ctrl.abort(), timeoutMs);
        const res = await fetch(window.RESQNET_LIVE.BACKEND_URL + "/health", {
          signal: ctrl.signal,
          cache: "no-store",
        });
        clearTimeout(timer);
        if (res.ok) {
          window.RESQNET_CONFIG = window.RESQNET_LIVE;
          return window.RESQNET_LIVE;
        }
      } catch (err) {
        // Live backend unreachable or timed out — fall back to local.
      }
      window.RESQNET_CONFIG = window.RESQNET_LOCAL;
      console.warn("ResQNet: live backend unreachable, using local backend at " + window.RESQNET_LOCAL.BACKEND_URL);
      return window.RESQNET_LOCAL;
    })();
    return pending;
  };
})();
