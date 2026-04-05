import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
import httpx

from db import get_db
from models import User, OAuthState
from services.auth_service import create_token, hash_password

router = APIRouter(prefix="/auth", tags=["google"])

GOOGLE_CLIENT_ID     = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")


def _get_domain():
    """Return the app domain without protocol, checking all known env vars."""
    for var in ("REPLIT_DEV_DOMAIN", "APP_DOMAIN"):
        v = os.getenv(var, "").strip()
        if v:
            return v
    replit = os.getenv("REPLIT_DOMAINS", "").split(",")[0].strip()
    if replit:
        return replit
    vercel = os.getenv("VERCEL_URL", "").strip()
    if vercel:
        return vercel
    return "localhost:5000"


def _get_redirect_uri():
    return f"https://{_get_domain()}/api/auth/google/callback"


def _now():
    return datetime.now(timezone.utc)


# ── State helpers (DB-backed, works on serverless/Vercel) ────────────────────

def _save_state(db: Session, state: str):
    db.merge(OAuthState(state=state))
    # Clean up states older than 10 minutes
    cutoff = _now() - timedelta(minutes=10)
    db.query(OAuthState).filter(OAuthState.created_at < cutoff).delete()
    db.commit()


def _consume_state(db: Session, state: str) -> bool:
    """Return True and delete the state if it exists, else False."""
    obj = db.query(OAuthState).filter(OAuthState.state == state).first()
    if not obj:
        return False
    db.delete(obj)
    db.commit()
    return True


# ── 1. Frontend calls this to start OAuth flow ────────────────────────────────
@router.get("/google/redirect")
def google_redirect(db: Session = Depends(get_db)):
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise HTTPException(500, "Google OAuth sozlanmagan.")
    state = secrets.token_urlsafe(16)
    _save_state(db, state)
    redirect_uri = _get_redirect_uri()
    url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={GOOGLE_CLIENT_ID}"
        f"&redirect_uri={redirect_uri}"
        "&response_type=code"
        "&scope=openid%20email%20profile"
        f"&state={state}"
        "&prompt=select_account"
    )
    return RedirectResponse(url)


# ── 2. Google redirects here with ?code=… ────────────────────────────────────
@router.get("/google/callback")
async def google_callback(code: str | None = None,
                           state: str | None = None,
                           error: str | None = None,
                           db: Session = Depends(get_db)):
    if error:
        return RedirectResponse(f"/?auth_error={error}")

    if not code or not state or not _consume_state(db, state):
        return RedirectResponse("/?auth_error=invalid_state")

    redirect_uri = _get_redirect_uri()

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
    if resp.status_code != 200:
        import logging
        logging.error(f"Google token exchange failed: {resp.status_code} — {resp.text} | redirect_uri={redirect_uri}")
        return RedirectResponse("/?auth_error=token_exchange_failed")

    tokens = resp.json()
    id_token_str = tokens.get("id_token")
    if not id_token_str:
        return RedirectResponse("/?auth_error=no_id_token")

    try:
        info = id_token.verify_oauth2_token(
            id_token_str,
            google_requests.Request(),
            GOOGLE_CLIENT_ID,
        )
    except ValueError:
        return RedirectResponse("/?auth_error=token_invalid")

    email = info.get("email", "").lower().strip()
    if not email:
        return RedirectResponse("/?auth_error=no_email")

    user = db.query(User).filter(User.email == email).first()
    if not user:
        dummy_pw = secrets.token_hex(24)
        user = User(email=email, password_hash=hash_password(dummy_pw))
        db.add(user)
        db.commit()
        db.refresh(user)

    user.failed_logins = 0
    user.locked_until  = None
    db.commit()

    jwt = create_token(user.id)
    return RedirectResponse(f"/?google_token={jwt}&user_id={user.id}&email={email}")


# ── 3. Legacy: GSI credential POST ───────────────────────────────────────────
class GoogleTokenIn(BaseModel):
    credential: str


@router.post("/google")
def google_login(body: GoogleTokenIn, db: Session = Depends(get_db)):
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(500, "Google OAuth sozlanmagan.")
    try:
        info = id_token.verify_oauth2_token(
            body.credential,
            google_requests.Request(),
            GOOGLE_CLIENT_ID,
        )
    except ValueError as e:
        raise HTTPException(400, f"Google token yaroqsiz: {e}")

    email = info.get("email", "").lower().strip()
    if not email:
        raise HTTPException(400, "Google akkauntdan email olinmadi.")

    user = db.query(User).filter(User.email == email).first()
    if not user:
        dummy_pw = secrets.token_hex(24)
        user = User(email=email, password_hash=hash_password(dummy_pw))
        db.add(user)
        db.commit()
        db.refresh(user)

    user.failed_logins = 0
    user.locked_until  = None
    db.commit()

    return {
        "access_token": create_token(user.id),
        "user_id": user.id,
        "email": user.email,
    }
