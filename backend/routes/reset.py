import os
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from db import get_db
from models import User
from services.auth_service import hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["reset"])

GMAIL_USER     = os.getenv("GMAIL_USER", "")
GMAIL_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")


def _now():
    return datetime.now(timezone.utc)


def _send_reset_email(to_email: str, reset_link: str, token_short: str = ""):
    msg = MIMEMultipart("alternative")
    now_str  = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M")
    msg["Subject"] = f"BrainBridge — Parolni tiklash ({now_str} UTC)"
    msg["From"]    = f"BrainBridge <{GMAIL_USER}>"
    msg["To"]      = to_email

    html = f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#f0eeff;font-family:sans-serif">
  <div style="max-width:480px;margin:40px auto;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 4px 24px rgba(108,99,255,.12)">
    <div style="background:#6c63ff;padding:28px 32px">
      <h1 style="margin:0;color:#fff;font-size:22px;font-weight:700">&#129504; BrainBridge</h1>
    </div>
    <div style="padding:32px">
      <h2 style="margin:0 0 12px;color:#1a1a2e;font-size:18px">Parolni tiklash</h2>
      <p style="color:#555;font-size:15px;line-height:1.6;margin:0 0 8px">
        Parolni tiklash so'rovi qabul qilindi.
      </p>
      <p style="color:#777;font-size:14px;line-height:1.6;margin:0 0 24px">
        Quyidagi tugmani bosing va yangi parol o'rnating.<br>
        Havola <strong>30 daqiqa</strong> davomida amal qiladi.
      </p>
      <a href="{reset_link}"
         style="display:inline-block;padding:14px 32px;background:#6c63ff;color:#fff;border-radius:10px;text-decoration:none;font-weight:700;font-size:15px">
        Parolni tiklash &#8594;
      </a>
      <p style="margin:24px 0 0;color:#aaa;font-size:12px">
        Agar siz bu so'rovni yubormagan bo'lsangiz, xatni e'tiborsiz qoldiring.
      </p>
      <!-- unique: {token_short} {now_str} -->
    </div>
  </div>
</body>
</html>"""

    plain = f"Parolni tiklash havolasi:\n{reset_link}\n\nHavola 30 daqiqa davomida amal qiladi.\nVaqt: {now_str} UTC"

    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html,  "html",  "utf-8"))

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(GMAIL_USER, GMAIL_PASSWORD)
        server.sendmail(GMAIL_USER, to_email, msg.as_string())


class ForgotIn(BaseModel):
    email: EmailStr


class ResetIn(BaseModel):
    token: str
    new_password: str


@router.post("/forgot-password")
async def forgot_password(body: ForgotIn, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email.lower().strip()).first()
    if not user:
        return {"ok": True, "message": "Agar email ro'yxatdan o'tgan bo'lsa, xat yuborildi."}

    existing_exp = user.reset_token_exp
    if existing_exp and existing_exp.tzinfo is None:
        existing_exp = existing_exp.replace(tzinfo=timezone.utc)

    if not user.reset_token or not existing_exp or _now() > existing_exp:
        token = secrets.token_urlsafe(32)
        user.reset_token     = token
        user.reset_token_exp = _now() + timedelta(minutes=30)
        db.commit()
    else:
        token = user.reset_token

    domain = (
        os.getenv("REPLIT_DEV_DOMAIN", "").strip()
        or os.getenv("APP_DOMAIN", "").strip()
        or os.getenv("REPLIT_DOMAINS", "").split(",")[0].strip()
        or os.getenv("VERCEL_URL", "").strip()
        or "localhost:5000"
    )
    reset_link = f"https://{domain}/?reset_token={token}"

    try:
        _send_reset_email(body.email, reset_link, token_short=token[:8])
    except Exception as e:
        raise HTTPException(500, f"Xat yuborishda xato: {str(e)}")

    return {"ok": True, "message": "Agar email ro'yxatdan o'tgan bo'lsa, xat yuborildi."}


@router.post("/reset-password")
def reset_password(body: ResetIn, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.reset_token == body.token).first()
    if not user:
        raise HTTPException(400, "Token yaroqsiz yoki muddati tugagan.")

    exp = user.reset_token_exp
    if exp:
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if _now() > exp:
            raise HTTPException(400, "Token muddati tugagan. Qaytadan so'rov yuboring.")

    if len(body.new_password) < 4:
        raise HTTPException(400, "Parol kamida 4 ta belgi bo'lsin.")

    if verify_password(body.new_password, user.password_hash):
        raise HTTPException(400, "Yangi parol joriy parol bilan bir xil bo'lmasin.")

    user.prev_password_hash = user.password_hash
    user.password_hash      = hash_password(body.new_password)
    user.reset_token        = None
    user.reset_token_exp    = None
    user.failed_logins      = 0
    user.locked_until       = None
    user.locked_permanent   = False
    db.commit()
    return {"ok": True, "message": "Parol muvaffaqiyatli tiklandi. Endi kirishingiz mumkin."}
