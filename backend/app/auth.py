"""
auth.py — API key + WebSocket token validation for ResQNet.

Auth is OPTIONAL when API_KEY is not set in the environment.
  - No API_KEY set  → all requests pass through (dev/local mode)
  - API_KEY set     → all requests must include X-API-Key header (secure mode)

This means the system works out of the box with zero config, and
becomes secure the moment you add API_KEY to backend/.env.

Generating a key:
    python -c "import secrets; print(secrets.token_hex(32))"
"""

import os

from fastapi import Header, HTTPException, WebSocket, status
from starlette.requests import Request
from starlette.responses import JSONResponse
from slowapi.errors import RateLimitExceeded


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_api_key() -> str:
    """Return the configured API key, or empty string if not set."""
    return os.getenv("API_KEY", "").strip()


def _auth_enabled() -> bool:
    return bool(_get_api_key())


# ---------------------------------------------------------------------------
# 5.1 — HTTP API key dependency
# ---------------------------------------------------------------------------

async def verify_api_key(x_api_key: str = Header(default="", alias="X-API-Key")) -> None:
    """
    FastAPI dependency — Depends(verify_api_key).

    When API_KEY is set: header must match or → 403.
    When API_KEY is not set: passes through silently (dev mode).
    """
    if not _auth_enabled():
        return   # dev mode — no auth required
    if x_api_key != _get_api_key():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing API key."
        )


# ---------------------------------------------------------------------------
# 5.2 — WebSocket token validation
# ---------------------------------------------------------------------------

async def verify_ws_token(websocket: WebSocket, token: str = "") -> bool:
    """
    Called inside the WebSocket endpoint after accept().
    WebSocket must be accepted before it can be closed — closing an
    unaccepted socket silently fails and leaves the client hanging.

    When API_KEY is not set: always passes (dev mode).
    When API_KEY is set and token is wrong: sends a close frame and returns False.
    """
    if not _auth_enabled():
        return True   # dev mode — no token required
    if not token or token != _get_api_key():
        # Already accepted at this point — send proper close frame
        await websocket.close(code=4403, reason="Forbidden: invalid token.")
        return False
    return True


# ---------------------------------------------------------------------------
# 5.3 — Rate limit exceeded handler (version-safe)
# ---------------------------------------------------------------------------

def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """
    Custom handler avoids importing slowapi's private _rate_limit_exceeded_handler
    which has changed signature across versions and causes a type error in
    app.add_exception_handler().
    """
    return JSONResponse(
        status_code=429,
        content={
            "error": "Rate limit exceeded.",
            "detail": str(exc.detail) if hasattr(exc, "detail") else "Too many requests.",
            "retry_after": "60 seconds"
        }
    )