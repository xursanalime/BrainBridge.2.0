import random
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy.orm import Session
from sqlalchemy import func

from models import Word, User

# Box → review interval (days)
BOX_INTERVALS = {0: 0, 1: 1, 2: 3, 3: 7, 4: 14, 5: 30}
BOX_LABELS    = {0: "Yangi", 1: "1-bosqich", 2: "2-bosqich",
                 3: "3-bosqich", 4: "4-bosqich", 5: "O'rganildi"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now():
    return datetime.now(timezone.utc)


def _serialize(w: Word) -> dict:
    return {
        "id":          w.id,
        "word":        w.word,
        "translation": w.translation,
        "box":         w.box,
        "box_label":   BOX_LABELS[w.box],
        "next_review": w.next_review.isoformat(),
        "created_at":  w.created_at.isoformat(),
    }


# ── Add words ─────────────────────────────────────────────────────────────────

def parse_input(raw: str) -> List[tuple]:
    """
    Parse lines like:
      hello - salom
      hello-salom
    Returns list of (word, translation).
    """
    pairs = []
    for line in raw.strip().splitlines()[:300]:
        line = line.strip()
        if not line:
            continue
        # Support both "word - translation" and "word-translation"
        if " - " in line:
            parts = line.split(" - ", 1)
        elif "-" in line:
            parts = line.split("-", 1)
        else:
            continue
        word  = parts[0].strip().lower()
        trans = parts[1].strip().lower()
        if word and trans:
            pairs.append((word, trans))
    return pairs


def add_words(db: Session, user_id: int, raw: str) -> dict:
    pairs = parse_input(raw)
    if not pairs:
        raise ValueError("Hech qanday to'g'ri format topilmadi. Format: inglizcha - o'zbekcha")

    added = updated = skipped = 0
    for word, translation in pairs:
        existing = db.query(Word).filter(
            Word.user_id == user_id,
            Word.word == word
        ).first()

        if existing:
            # Update translation if changed
            if existing.translation != translation:
                existing.translation = translation
                updated += 1
            else:
                skipped += 1
        else:
            db.add(Word(
                user_id=user_id,
                word=word,
                translation=translation,
                box=0,
                next_review=_now()
            ))
            added += 1

    db.commit()
    return {"total": len(pairs), "added": added, "updated": updated, "skipped": skipped}


# ── Word management ───────────────────────────────────────────────────────────

def get_words(db: Session, user_id: int, box: Optional[int] = None,
              search: str = "", sort: str = "date") -> List[dict]:
    q = db.query(Word).filter(Word.user_id == user_id)
    if box is not None:
        q = q.filter(Word.box == box)
    if search:
        s = search.lower()
        q = q.filter(
            (Word.word.ilike(f"%{s}%")) | (Word.translation.ilike(f"%{s}%"))
        )
    if sort == "box":
        q = q.order_by(Word.box.asc(), Word.created_at.desc())
    else:
        q = q.order_by(Word.created_at.desc())
    return [_serialize(w) for w in q.all()]


def get_due_words(db: Session, user_id: int) -> List[Word]:
    return (
        db.query(Word)
        .filter(Word.user_id == user_id, Word.next_review <= _now())
        .order_by(Word.box.asc(), Word.next_review.asc())
        .all()
    )


def delete_word(db: Session, user_id: int, word_id: int):
    w = db.query(Word).filter(Word.id == word_id, Word.user_id == user_id).first()
    if not w:
        raise ValueError("So'z topilmadi.")
    db.delete(w)
    db.commit()


def update_word(db: Session, user_id: int, word_id: int,
                word: Optional[str] = None, translation: Optional[str] = None) -> dict:
    w = db.query(Word).filter(Word.id == word_id, Word.user_id == user_id).first()
    if not w:
        raise ValueError("So'z topilmadi.")
    if word:
        w.word = word.strip().lower()
    if translation:
        w.translation = translation.strip().lower()
    db.commit()
    return _serialize(w)


# ── Leitner ───────────────────────────────────────────────────────────────────

def advance(db: Session, user_id: int, word_id: int, correct: bool) -> dict:
    """
    Update ONE word's box:
    - correct → box + 1 (max 5)
    - wrong   → box = 1
    """
    w = db.query(Word).filter(Word.id == word_id, Word.user_id == user_id).first()
    if not w:
        raise ValueError("So'z topilmadi.")

    w.box = min(w.box + 1, 5) if correct else 1
    w.next_review = _now() + timedelta(days=BOX_INTERVALS[w.box])

    # Update streak
    user = db.query(User).filter(User.id == user_id).first()
    today = _now().date()
    if user.last_study:
        last = user.last_study.date()
        if last < today:
            user.streak = (user.streak or 0) + 1 if (today - last).days == 1 else 1
    else:
        user.streak = 1
    user.last_study = _now()

    db.commit()
    return _serialize(w)


# ── Tests ─────────────────────────────────────────────────────────────────────

def get_write_test(db: Session, user_id: int, word_id: int) -> dict:
    w = db.query(Word).filter(Word.id == word_id, Word.user_id == user_id).first()
    if not w:
        raise ValueError("So'z topilmadi.")
    return {"id": w.id, "word": w.word, "box": w.box, "box_label": BOX_LABELS[w.box]}


def submit_write(db: Session, user_id: int, word_id: int, answer: str) -> dict:
    """Writing test: user sees UZ (translation), types EN (word)."""
    w = db.query(Word).filter(Word.id == word_id, Word.user_id == user_id).first()
    if not w:
        raise ValueError("So'z topilmadi.")

    correct = answer.strip().lower() == w.word.strip().lower()
    result = advance(db, user_id, word_id, correct)
    result["correct"]  = correct
    result["expected"] = w.word          # EN word is the answer
    result["given"]    = answer.strip().lower()
    return result


def get_quiz(db: Session, user_id: int, word_id: int, mode: str = "uz2en") -> dict:
    """
    mode='uz2en': show UZ word, choose EN translation (default)
    mode='en2uz': show EN word, choose UZ translation
    """
    w = db.query(Word).filter(Word.id == word_id, Word.user_id == user_id).first()
    if not w:
        raise ValueError("So'z topilmadi.")

    if mode == "en2uz":
        # Show English, ask for Uzbek → options are Uzbek translations
        prompt = w.word
        correct = w.translation
        others = (
            db.query(Word.translation)
            .filter(Word.user_id == user_id, Word.id != word_id)
            .order_by(func.random()).limit(20).all()
        )
        pool = [r[0] for r in others if r[0] != correct]
        fallback = ["yugurmoq", "yozmoq", "o'qimoq", "bermoq", "olmoq",
                    "aytmoq", "ko'rmoq", "bormoq", "kelmoq", "bilmoq"]
    else:
        # Show Uzbek, ask for English → options are English words
        prompt = w.translation
        correct = w.word
        others = (
            db.query(Word.word)
            .filter(Word.user_id == user_id, Word.id != word_id)
            .order_by(func.random()).limit(20).all()
        )
        pool = [r[0] for r in others if r[0] != correct]
        fallback = ["run", "jump", "speak", "write", "read", "make",
                    "take", "give", "find", "hold", "keep", "move"]

    for f in fallback:
        if f not in pool and f != correct:
            pool.append(f)

    distractors = random.sample(pool, min(3, len(pool)))
    options = distractors + [correct]
    random.shuffle(options)

    return {
        "id":      w.id,
        "prompt":  prompt,
        "correct": correct,
        "options": options,
        "mode":    mode,
        "word":    w.word,
        "translation": w.translation,
    }


def submit_quiz(db: Session, user_id: int, word_id: int, chosen: str, mode: str = "uz2en") -> dict:
    w = db.query(Word).filter(Word.id == word_id, Word.user_id == user_id).first()
    if not w:
        raise ValueError("So'z topilmadi.")

    if mode == "en2uz":
        expected = w.translation
    else:
        expected = w.word

    correct = chosen.strip().lower() == expected.strip().lower()
    result = advance(db, user_id, word_id, correct)
    result["correct"]  = correct
    result["chosen"]   = chosen
    result["expected"] = expected
    return result


# ── Stats ─────────────────────────────────────────────────────────────────────

def get_stats(db: Session, user_id: int) -> dict:
    total    = db.query(Word).filter(Word.user_id == user_id).count()
    due      = db.query(Word).filter(Word.user_id == user_id, Word.next_review <= _now()).count()
    mastered = db.query(Word).filter(Word.user_id == user_id, Word.box == 5).count()
    user     = db.query(User).filter(User.id == user_id).first()

    box_dist = {}
    for b in range(6):
        box_dist[b] = db.query(Word).filter(Word.user_id == user_id, Word.box == b).count()

    return {
        "total":        total,
        "due":          due,
        "mastered":     mastered,
        "streak":       user.streak or 0,
        "box_dist":     box_dist,
        "box_labels":   BOX_LABELS,
    }