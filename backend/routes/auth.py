from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from db import get_db
from services.auth_service import (
    create_token, decode_token, get_user, get_user_by_email,
    login, register, verify_password, hash_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])
oauth2 = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

# ---------- helpers ----------

def _now():
    return datetime.now(timezone.utc)

def _lock_message(locked_until):
    diff = locked_until - _now()
    total = int(diff.total_seconds())
    if total <= 0:
        return None
    h, rem = divmod(total, 3600)
    m, s   = divmod(rem, 60)
    if h >= 24:
        return f"Akkaunt {h // 24} kunga bloklangan. Ertaga urinib ko'ring."
    if h > 0:
        return f"Akkaunt {h} soat {m} daqiqaga bloklangan."
    return f"Akkaunt {m} daqiqa {s} soniyaga bloklangan."

def _apply_lock(user, db):
    """Increment failed attempts and set lock duration."""
    f = (user.failed_logins or 0) + 1
    user.failed_logins = f
    if f >= 10:
        user.locked_permanent = True
        user.locked_until = None
    elif f >= 7:
        user.locked_until = _now() + timedelta(days=1)
    elif f >= 5:
        user.locked_until = _now() + timedelta(hours=1)
    elif f >= 3:
        user.locked_until = _now() + timedelta(minutes=15)
    db.commit()

def _check_lock(user):
    """Raise HTTP 429 if user is locked."""
    if getattr(user, 'locked_permanent', False):
        raise HTTPException(429, "Akkaunt umrbod bloklangan. Administrator bilan bog'laning.")
    lu = getattr(user, 'locked_until', None)
    if lu:
        if lu.tzinfo is None:
            lu = lu.replace(tzinfo=timezone.utc)
        if lu > _now():
            msg = _lock_message(lu)
            raise HTTPException(429, msg or "Akkaunt vaqtincha bloklangan.")


# ---------- deps ----------

def current_user(token: str = Depends(oauth2), db: Session = Depends(get_db)):
    user_id = decode_token(token)
    if not user_id:
        raise HTTPException(401, "Token yaroqsiz yoki muddati tugagan.")
    user = get_user(db, user_id)
    if not user:
        raise HTTPException(401, "Foydalanuvchi topilmadi.")
    return user


# ---------- routes ----------

class RegisterIn(BaseModel):
    email: EmailStr
    password: str


@router.post("/register", status_code=201)
def register_route(body: RegisterIn, db: Session = Depends(get_db)):
    if len(body.password) < 4:
        raise HTTPException(400, "Parol kamida 4 ta belgi bo'lsin.")
    if get_user_by_email(db, body.email):
        raise HTTPException(400, "Bu email allaqachon ro'yxatdan o'tgan.")
    user = register(db, body.email, body.password)
    return {"access_token": create_token(user.id), "user_id": user.id, "email": user.email}


@router.post("/login")
def login_route(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    # find user first (to track attempts)
    from services.auth_service import get_user_by_email as _gube
    user = _gube(db, form.username)

    if user:
        _check_lock(user)

    # verify credentials
    ok = login(db, form.username, form.password)
    if not ok:
        if user:
            _apply_lock(user, db)
            f = user.failed_logins
            if getattr(user, 'locked_permanent', False):
                raise HTTPException(429, "Akkaunt umrbod bloklangan. Administrator bilan bog'laning.")
            if f >= 10:
                raise HTTPException(429, "Akkaunt umrbod bloklangan.")
            elif f >= 7:
                raise HTTPException(429, f"7 marta xato kiritildi. Akkaunt 1 kunga bloklandi.")
            elif f >= 5:
                raise HTTPException(429, f"5 marta xato kiritildi. Akkaunt 1 soatga bloklandi.")
            elif f >= 3:
                raise HTTPException(429, f"3 marta xato kiritildi. Akkaunt 15 daqiqaga bloklandi.")
        raise HTTPException(401, "Email yoki parol noto'g'ri.")

    # successful login — reset counters
    user.failed_logins = 0
    user.locked_until  = None
    db.commit()
    return {"access_token": create_token(user.id), "user_id": user.id, "email": user.email}


@router.get("/me")
def me(user=Depends(current_user)):
    return {
        "id": user.id, "email": user.email,
        "streak": user.streak or 0, "created_at": user.created_at,
    }


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str


@router.post("/change-password")
def change_password(body: ChangePasswordIn, user=Depends(current_user), db: Session = Depends(get_db)):
    # --- 1) verify current password ---
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(400, "Joriy parol noto'g'ri.")

    # --- 2) new != current ---
    if body.new_password == body.current_password:
        raise HTTPException(400, "Yangi parol joriy parol bilan bir xil bo'lmasin.")

    # --- 3) new != previous ---
    if user.prev_password_hash and verify_password(body.new_password, user.prev_password_hash):
        raise HTTPException(400, "Oldingi parolni qayta ishlata olmaysiz.")

    # --- 4) min length ---
    if len(body.new_password) < 4:
        raise HTTPException(400, "Yangi parol kamida 4 ta belgi bo'lsin.")

    # --- 5) monthly limit: max 2 changes per calendar month ---
    cur_month = _now().strftime("%Y-%m")
    if user.pw_change_month != cur_month:
        user.pw_change_count = 0
        user.pw_change_month = cur_month
    if (user.pw_change_count or 0) >= 2:
        raise HTTPException(429, "Bir oyda faqat 2 marta parol almashtirish mumkin.")

    # --- save ---
    user.prev_password_hash = user.password_hash
    user.password_hash      = hash_password(body.new_password)
    user.pw_change_count    = (user.pw_change_count or 0) + 1
    user.pw_change_month    = cur_month
    db.commit()
    remaining = 2 - user.pw_change_count
    return {
        "ok": True,
        "message": f"Parol muvaffaqiyatli o'zgartirildi. Bu oy yana {remaining} marta o'zgartirish mumkin.",
    }
