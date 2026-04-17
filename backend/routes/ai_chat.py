"""
AI Chat routes — BrainBot
GET    /api/ai/chats            — chat sessiyalar ro'yxati
POST   /api/ai/chats            — yangi chat
PATCH  /api/ai/chats/{id}       — chat nomini tahrirlash
DELETE /api/ai/chats/{id}       — chatni o'chirish
GET    /api/ai/chats/{id}/messages — xabarlar tarixi
POST   /api/ai/chats/{id}/send  — xabar yuborish → Groq javob
POST   /api/ai/extract-words    — rasm/matndan so'z chiqarish
"""
import base64
import json
import os
import re
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional

from db import get_db
from routes.auth import current_user
from models import AIChatSession, AIChatMessage

router = APIRouter(prefix="/ai", tags=["ai-chat"])

# ── Groq client ───────────────────────────────────────────────────────────────
_groq = None
try:
    from groq import Groq
    _key = os.getenv("GROQ_API_KEY", "")
    if _key:
        _groq = Groq(api_key=_key)
except ImportError:
    pass

# ── System prompt ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = (
    "You are BrainBot — an expert English teacher and AI assistant for Uzbek learners. "
    "You are integrated into the BrainBridge language learning platform.\n\n"
    "Your specialties:\n"
    "1. Grammar rules — explain clearly with Uzbek and English examples\n"
    "2. Writing check — find errors, suggest corrections in this format: ❌ Error → ✅ Correction\n"
    "3. Writing assistance — help write essays, emails, stories, cover letters\n"
    "4. Idiom analysis — explain meaning, origin, usage context, 3 natural examples\n"
    "5. Vocabulary — explain words, collocations, synonyms\n\n"
    "Language rules:\n"
    "- Respond in the same language the user writes (mostly Uzbek or Uzbek+English mix)\n"
    "- Be encouraging, supportive, and patient\n"
    "- For grammar rules: use simple language the learner understands\n"
    "- For writing checks: always show the corrected version\n"
    "- Keep responses well-structured with clear sections\n"
    "- Use emojis sparingly to make responses friendlier"
)

# ── Schemas ──────────────────────────────────────────────────────────────────
class CreateChatIn(BaseModel):
    name: Optional[str] = "Yangi suhbat"

class RenameChatIn(BaseModel):
    name: str

class SendMessageIn(BaseModel):
    content: str

class ExtractWordsIn(BaseModel):
    image_b64: Optional[str] = None  # base64 encoded image
    text:      Optional[str] = None  # or raw text

# ── Helpers ──────────────────────────────────────────────────────────────────
def _get_session(db: Session, session_id: int, user_id: int) -> AIChatSession:
    s = db.query(AIChatSession).filter_by(id=session_id, user_id=user_id).first()
    if not s:
        raise HTTPException(404, "Chat topilmadi")
    return s

def _serialize_session(s: AIChatSession) -> dict:
    return {
        "id":         s.id,
        "name":       s.name,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }

def _serialize_message(m: AIChatMessage) -> dict:
    return {
        "id":         m.id,
        "session_id": m.session_id,
        "role":       m.role,
        "content":    m.content,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }

# ── Routes ───────────────────────────────────────────────────────────────────

@router.get("/chats")
def list_chats(db: Session = Depends(get_db), user=Depends(current_user)):
    sessions = (
        db.query(AIChatSession)
        .filter_by(user_id=user.id)
        .order_by(AIChatSession.updated_at.desc())
        .all()
    )
    return [_serialize_session(s) for s in sessions]


@router.post("/chats", status_code=201)
def create_chat(body: CreateChatIn, db: Session = Depends(get_db), user=Depends(current_user)):
    s = AIChatSession(user_id=user.id, name=body.name or "Yangi suhbat")
    db.add(s)
    db.commit()
    db.refresh(s)
    return _serialize_session(s)


@router.patch("/chats/{session_id}")
def rename_chat(session_id: int, body: RenameChatIn,
                db: Session = Depends(get_db), user=Depends(current_user)):
    s = _get_session(db, session_id, user.id)
    s.name = body.name[:100]
    db.commit()
    return _serialize_session(s)


@router.delete("/chats/{session_id}")
def delete_chat(session_id: int, db: Session = Depends(get_db), user=Depends(current_user)):
    s = _get_session(db, session_id, user.id)
    db.delete(s)
    db.commit()
    return {"ok": True}


@router.get("/chats/{session_id}/messages")
def get_messages(session_id: int, db: Session = Depends(get_db), user=Depends(current_user)):
    s = _get_session(db, session_id, user.id)
    return [_serialize_message(m) for m in s.messages]


@router.post("/chats/{session_id}/send")
def send_message(session_id: int, body: SendMessageIn,
                 db: Session = Depends(get_db), user=Depends(current_user)):
    s = _get_session(db, session_id, user.id)

    # Save user message
    user_msg = AIChatMessage(session_id=s.id, role="user", content=body.content)
    db.add(user_msg)
    db.commit()

    # Build history for Groq (last 20 messages for context)
    history = [
        {"role": m.role, "content": m.content}
        for m in s.messages[-20:]
    ]

    # Get AI response
    ai_content = _call_groq(history)

    # Auto-name chat from first message
    if len(s.messages) <= 2 and s.name == "Yangi suhbat":
        words = body.content.strip().split()[:6]
        s.name = " ".join(words)[:80] or "Yangi suhbat"

    # Save assistant message
    ai_msg = AIChatMessage(session_id=s.id, role="assistant", content=ai_content)
    db.add(ai_msg)
    db.commit()
    db.refresh(s)

    return {
        "user_message":      _serialize_message(user_msg),
        "assistant_message": _serialize_message(ai_msg),
        "session_name":      s.name,
    }


@router.post("/extract-words")
def extract_words(body: ExtractWordsIn, user=Depends(current_user)):
    """Rasm yoki matndan inglizcha so'zlarni chiqaradi."""
    if not _groq:
        raise HTTPException(503, "AI xizmati ulangmagan (GROQ_API_KEY yo'q)")

    if body.image_b64:
        # Vision model bilan rasm tahlil
        try:
            response = _groq.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{body.image_b64}"},
                        },
                        {
                            "type": "text",
                            "text": (
                                "Extract all English words from this image that a language learner might want to study. "
                                "For each word provide the Uzbek translation. "
                                "Respond ONLY with JSON array, no markdown:\n"
                                '[{"word": "example", "translation": "misol"}, ...]'
                            ),
                        },
                    ],
                }],
                temperature=0.1,
                max_tokens=800,
            )
            raw = response.choices[0].message.content.strip()
        except Exception as e:
            raise HTTPException(500, f"Rasm tahlil xatosi: {str(e)}")
    elif body.text:
        # Text dan so'z chiqarish
        prompt = (
            "Extract all notable English vocabulary words from this text that a learner should study. "
            "Provide Uzbek translations. "
            "Respond ONLY with JSON array, no markdown:\n"
            '[{"word": "example", "translation": "misol"}, ...]\n\n'
            f"Text: {body.text}"
        )
        try:
            response = _groq.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=600,
            )
            raw = response.choices[0].message.content.strip()
        except Exception as e:
            raise HTTPException(500, f"Matn tahlil xatosi: {str(e)}")
    else:
        raise HTTPException(400, "image_b64 yoki text kerak")

    # Parse JSON
    try:
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw).strip()
        words = json.loads(raw)
        if not isinstance(words, list):
            raise ValueError("Not a list")
        # Validate/clean
        result = [
            {"word": str(w.get("word", "")).strip(),
             "translation": str(w.get("translation", "")).strip()}
            for w in words
            if w.get("word") and w.get("translation")
        ]
        return {"words": result}
    except Exception:
        raise HTTPException(500, "AI javobi noto'g'ri formatda")


# ── Groq call ─────────────────────────────────────────────────────────────────
def _call_groq(history: list[dict]) -> str:
    if not _groq:
        return "AI xizmati ulangmagan. GROQ_API_KEY ni .env ga qo'shing."
    try:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history
        resp = _groq.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            temperature=0.6,
            max_tokens=1200,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        return f"AI xatolik: {str(e)}"
