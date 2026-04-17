"""Sentence writing module routes — AI-powered Leitner sentence building."""
import json
from datetime import datetime, timezone, timedelta
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from db import get_db
from models import SentenceProgress, UserSentence, Word
from routes.auth import current_user
from services.ai_service import check_sentence

router = APIRouter(prefix="/sentences", tags=["sentences"])

# ── Interval map (days) for sentence Leitner boxes ─────────────────────────
BOX_INTERVALS = {1: 0, 2: 3, 3: 7, 4: 14, 5: 30}
MAX_BOX = 5


def _now():
    return datetime.now(timezone.utc)


def _next_review(box: int) -> datetime:
    days = BOX_INTERVALS.get(box, 0)
    return _now() + timedelta(days=days)


def _get_or_create_progress(db: Session, user_id: int, word_id: int) -> SentenceProgress:
    """Get existing SentenceProgress or create one at box=1."""
    sp = db.query(SentenceProgress).filter_by(user_id=user_id, word_id=word_id).first()
    if not sp:
        sp = SentenceProgress(
            user_id=user_id,
            word_id=word_id,
            sentence_box=1,
            sentences_done=0,
            next_review=_now(),
        )
        db.add(sp)
        db.commit()
        db.refresh(sp)
    return sp


def _serialize_sp(sp: SentenceProgress, word: Word) -> dict:
    return {
        "word_id":       sp.word_id,
        "word":          word.word,
        "translation":   word.translation,
        "sentence_box":  sp.sentence_box,
        "sentences_done": sp.sentences_done,
        "next_review":   sp.next_review.isoformat() if sp.next_review else None,
    }


# ── Schemas ─────────────────────────────────────────────────────────────────

class CheckIn(BaseModel):
    word_id:         int
    sentence:        str
    sentence_number: int   # 1 or 2


class SkipIn(BaseModel):
    word_id: int


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/stats")
def sentence_stats(db: Session = Depends(get_db), user=Depends(current_user)):
    """Return per-box counts and due count for sentence module."""
    all_sp = db.query(SentenceProgress).filter_by(user_id=user.id).all()
    box_dist = {i: 0 for i in range(1, 6)}
    due = 0
    now = _now()
    for sp in all_sp:
        b = max(1, min(sp.sentence_box, 5))
        box_dist[b] = box_dist.get(b, 0) + 1
        if sp.next_review:
            nr = sp.next_review.replace(tzinfo=None) if sp.next_review.tzinfo else sp.next_review
            nw = now.replace(tzinfo=None)
            if nr <= nw:
                due += 1
    return {
        "total":    len(all_sp),
        "due":      due,
        "boxes":    box_dist,
        "mastered": box_dist.get(5, 0),
    }


@router.get("/box/{box_num}")
def words_in_box(
    box_num: int,
    db: Session = Depends(get_db),
    user=Depends(current_user)
):
    """Get all words in a specific sentence box."""
    if box_num < 1 or box_num > 5:
        raise HTTPException(400, "Box raqami 1-5 oralig'ida bo'lishi kerak")

    rows = (
        db.query(SentenceProgress, Word)
        .join(Word, Word.id == SentenceProgress.word_id)
        .filter(
            SentenceProgress.user_id == user.id,
            SentenceProgress.sentence_box == box_num,
        )
        .all()
    )
    return [_serialize_sp(sp, w) for sp, w in rows]


@router.get("/due")
def due_words(db: Session = Depends(get_db), user=Depends(current_user)):
    """Get words due for sentence review today."""
    now = _now()
    rows = (
        db.query(SentenceProgress, Word)
        .join(Word, Word.id == SentenceProgress.word_id)
        .filter(
            SentenceProgress.user_id == user.id,
            SentenceProgress.next_review <= now,
        )
        .order_by(SentenceProgress.sentence_box)
        .all()
    )
    return [_serialize_sp(sp, w) for sp, w in rows]


@router.post("/init/{word_id}")
def init_word(word_id: int, db: Session = Depends(get_db), user=Depends(current_user)):
    """Initialize sentence tracking for a word (called when word is added)."""
    word = db.query(Word).filter_by(id=word_id, user_id=user.id).first()
    if not word:
        raise HTTPException(404, "So'z topilmadi")
    sp = _get_or_create_progress(db, user.id, word_id)
    return _serialize_sp(sp, word)


@router.post("/init-all")
def init_all_words(db: Session = Depends(get_db), user=Depends(current_user)):
    """
    Auto-create SentenceProgress for every word the user has
    that doesn't already have one. Called on page load.
    """
    all_words = db.query(Word).filter_by(user_id=user.id).all()
    created = 0
    for word in all_words:
        existing = db.query(SentenceProgress).filter_by(
            user_id=user.id, word_id=word.id
        ).first()
        if not existing:
            sp = SentenceProgress(
                user_id=user.id,
                word_id=word.id,
                sentence_box=1,
                sentences_done=0,
                next_review=_now(),
            )
            db.add(sp)
            created += 1
    if created:
        db.commit()
    return {"initialized": created, "total": len(all_words)}


@router.get("/all")
def all_words_for_session(db: Session = Depends(get_db), user=Depends(current_user)):
    """Get all sentence progress words (for a full session)."""
    rows = (
        db.query(SentenceProgress, Word)
        .join(Word, Word.id == SentenceProgress.word_id)
        .filter(SentenceProgress.user_id == user.id)
        .order_by(SentenceProgress.sentence_box, SentenceProgress.next_review)
        .all()
    )
    return [_serialize_sp(sp, w) for sp, w in rows]



@router.post("/check")
def check_sentence_endpoint(
    body: CheckIn,
    db: Session = Depends(get_db),
    user=Depends(current_user)
):
    """
    AI checks the user's sentence.
    - If correct AND sentence_number==2 → advance box
    - If correct AND sentence_number==1 → keep box, increment sentences_done
    - If wrong → keep box, sentences_done unchanged (user retries)
    """
    word = db.query(Word).filter_by(id=body.word_id, user_id=user.id).first()
    if not word:
        raise HTTPException(404, "So'z topilmadi")

    if not body.sentence.strip():
        raise HTTPException(400, "Gap bo'sh bo'lishi mumkin emas")

    sp = _get_or_create_progress(db, user.id, body.word_id)

    # Call AI
    result = check_sentence(word.word, word.translation, body.sentence.strip())

    # Save sentence to history
    us = UserSentence(
        user_id=user.id,
        word_id=body.word_id,
        sentence_text=body.sentence.strip(),
        is_correct=result["correct"],
        ai_feedback=json.dumps(result, ensure_ascii=False),
        sentence_number=body.sentence_number,
    )
    db.add(us)

    old_box = sp.sentence_box
    advanced = False

    if result["correct"]:
        if body.sentence_number == 1:
            # First sentence done — wait for second
            sp.sentences_done = 1
        else:
            # Both sentences correct → advance box
            new_box = min(sp.sentence_box + 1, MAX_BOX)
            sp.sentence_box   = new_box
            sp.sentences_done = 0
            sp.last_reviewed  = _now()
            sp.next_review    = _next_review(new_box)
            advanced = True
    # If wrong: box stays, sentences_done unchanged, user retries

    db.commit()

    return {
        "correct":              result["correct"],
        "praise":               result.get("praise"),
        "error_type":           result.get("error_type"),
        "error_label":          result.get("error_label"),
        "explanation":          result.get("explanation"),
        "examples":             result.get("examples", []),
        "example_translations": result.get("example_translations", []),
        "corrected":            result.get("corrected"),
        "sentence_uz":          result.get("sentence_uz"),
        "old_box":              old_box,
        "new_box":              sp.sentence_box,
        "advanced":             advanced,
        "sentences_done":       sp.sentences_done,
        "sentence_number":      body.sentence_number,
    }


@router.post("/skip")
def skip_word(body: SkipIn, db: Session = Depends(get_db), user=Depends(current_user)):
    """
    User skips a word → reset to box 1.
    sentences_done also reset to 0.
    """
    word = db.query(Word).filter_by(id=body.word_id, user_id=user.id).first()
    if not word:
        raise HTTPException(404, "So'z topilmadi")

    sp = _get_or_create_progress(db, user.id, body.word_id)
    old_box = sp.sentence_box

    sp.sentence_box   = 1
    sp.sentences_done = 0
    sp.last_reviewed  = _now()
    sp.next_review    = _next_review(1)
    db.commit()

    return {
        "skipped":  True,
        "word":     word.word,
        "old_box":  old_box,
        "new_box":  1,
        "message":  f'"{word.word}" so\'zi 1-qutiga qaytarildi.',
    }
