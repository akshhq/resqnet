"""
session_proxy_routes.py — Server-side proxy for the Emergency Session Token
Apps Script (validate / resolve actions).

WHY THIS EXISTS:
The Responder Dashboard used to call the Apps Script webapp URL directly
from the browser (SESSION_TOKEN_WEBAPP_URL baked into frontend config.js).
That leaked the URL to anyone who opened dev tools, letting them call
action=validate / action=resolve directly — bypassing this backend
entirely, with no rate limiting or logging.

Now the frontend only ever talks to THIS backend. The Apps Script URL
lives exclusively in backend/.env (SESSION_TOKEN_WEBAPP_URL) and never
reaches the client.

Mounted in main.py with:
    from app.session_proxy_routes import router as session_proxy_router
    app.include_router(session_proxy_router)
"""

import json
import os
import time

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from slowapi import Limiter
from slowapi.util import get_remote_address

from app import user_db

router = APIRouter(prefix="/session-token", tags=["session-token"])
limiter = Limiter(key_func=get_remote_address, default_limits=[])

SESSION_TOKEN_WEBAPP_URL = os.getenv("SESSION_TOKEN_WEBAPP_URL", "").strip()


async def _call_apps_script(payload: dict) -> dict:
    if not SESSION_TOKEN_WEBAPP_URL:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Session token service is not configured on this server.",
        )
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            res = await client.post(
                SESSION_TOKEN_WEBAPP_URL,
                content=json.dumps(payload),
                headers={"Content-Type": "text/plain;charset=utf-8"},
            )
            return res.json()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not reach the emergency session service.",
        )


@router.post("/validate")
@limiter.limit("20/minute")
async def validate_session_token(request: Request):
    """
    Proxies { userID, token } -> Apps Script action=validate.
    No API key required: the magic-link token itself is the credential,
    same trust model as before — this endpoint just hides the upstream URL.
    """
    body = await request.json()
    user_id = body.get("userID")
    token = body.get("token")
    if not user_id or not token:
        raise HTTPException(status_code=400, detail="userID and token are required.")

    return await _call_apps_script({"action": "validate", "userID": user_id, "token": token})


@router.post("/resolve")
@limiter.limit("20/minute")
async def resolve_session_token(request: Request):
    """
    Proxies { userID, token, resolvedBy } -> Apps Script action=resolve
    (closes the magic link / Sheet row), AND closes the matching Postgres
    incidents row so User Dashboard incident history stays in sync.
    Replaces the old (missing) /user/incidents/resolve-by-token route.
    """
    body = await request.json()
    user_id = body.get("userID")
    token = body.get("token")
    resolved_by = body.get("resolvedBy", "responder")
    if not user_id or not token:
        raise HTTPException(status_code=400, detail="userID and token are required.")

    result = await _call_apps_script({
        "action": "resolve", "userID": user_id, "token": token, "resolvedBy": resolved_by,
    })

    try:
        await user_db.close_incident_by_token(token, int(time.time()))
    except Exception as e:
        print(f"[WARNING] Could not close Postgres incident for token: {e}")

    return result
