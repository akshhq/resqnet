"""
email_queue_routes.py — HTTP contract for the external Google Apps Script
email sender.

This backend never talks to Google directly. Instead, an Apps Script
(written and owned by the project author, not part of this codebase) runs
on a time-driven trigger and:

  1. GET  /email-queue/pending          → pulls jobs waiting to be sent
  2. ...sends each one via GmailApp.sendEmail(), building whatever HTML
     template it wants from the `payload` field of each job...
  3. POST /email-queue/{id}/mark-sent   → on success
     POST /email-queue/{id}/mark-failed → on failure, with an error message

All three routes are protected by the same optional X-API-Key mechanism as
the rest of the API (see auth.py) — nothing new to configure.

Full field-level contract: see EMAIL_QUEUE_INTEGRATION.md in the repo root.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import verify_api_key
from app.schemas import EmailQueueMarkFailed
from app import user_db

router = APIRouter(prefix="/email-queue", tags=["email-queue"])


@router.get("/pending", dependencies=[Depends(verify_api_key)])
async def get_pending_emails(limit: int = 50):
    """
    Returns up to `limit` pending email jobs, oldest first.
    Each item: { id, to_email, to_name, template_type, payload, created_at }
    """
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 200.")
    return await user_db.list_pending_emails(limit=limit)


@router.post("/{email_id}/mark-sent", dependencies=[Depends(verify_api_key)])
async def mark_sent(email_id: int):
    ok = await user_db.mark_email_sent(email_id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                             detail=f"No pending email job with id {email_id}.")
    return {"status": "marked_sent", "id": email_id}


@router.post("/{email_id}/mark-failed", dependencies=[Depends(verify_api_key)])
async def mark_failed(email_id: int, data: EmailQueueMarkFailed):
    ok = await user_db.mark_email_failed(email_id, data.error)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                             detail=f"No email job with id {email_id}.")
    return {"status": "marked_failed", "id": email_id}
