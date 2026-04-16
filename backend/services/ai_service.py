"""
AI service for sentence checking.
Priority: Groq (llama-3.3-70b) → OpenAI (gpt-4o-mini) → smart fallback

Response schema:
{
  "correct":       bool,
  "praise":        str | None,     # O'zbek maqtov (to'g'ri bo'lsa)
  "error_type":    str | None,     # "grammar" | "usage" | "meaning"
  "explanation":   str | None,     # O'zbek tushuntirish (xato bo'lsa)
  "examples":      list[str],      # 2 ta to'g'ri misol (inglizcha)
  "example_translations": list[str],  # Misollarning o'zbekcha tarjimasi
  "corrected":     str | None,     # Tuzatilgan variant
  "sentence_uz":   str | None,     # User yozgan gapning o'zbek tarjimasi
}
"""
import os
import json
import re

# ── Try Groq first ───────────────────────────────────────────────────────────
_groq = None
try:
    from groq import Groq
    _groq_key = os.getenv("GROQ_API_KEY", "")
    if _groq_key:
        _groq = Groq(api_key=_groq_key)
except ImportError:
    pass

# ── Fallback: OpenAI ─────────────────────────────────────────────────────────
_openai = None
try:
    from openai import OpenAI
    _oai_key = os.getenv("OPENAI_API_KEY", "")
    if _oai_key:
        _openai = OpenAI(api_key=_oai_key)
except ImportError:
    pass


# ── Prompt ───────────────────────────────────────────────────────────────────
def _build_prompt(word: str, translation: str, sentence: str) -> str:
    return f"""You are a strict but friendly English teacher checking sentences written by Uzbek learners.

Target word: "{word}" (O'zbek tarjima: {translation})
Student's sentence: "{sentence}"

Your job — check ALL THREE:
1. Grammar: Is the sentence grammatically correct English?
2. Usage: Is "{word}" used in the correct grammatical form and context?
3. Meaning (IMPORTANT): Does the sentence make logical, real-world sense? For nouns like "table", you cannot say "I table my goals" — that is semantically wrong. For verbs, adjectives, adverbs, check that the word is used in a meaningful way.

Respond with ONLY a valid JSON object, no markdown, no code fences:
{{
  "correct": true or false,
  "praise": "Warm Uzbek praise if correct, else null. Example: Ajoyib! Gapingiz to'g'ri va mazmunli!",
  "error_type": "grammar or usage or meaning, else null",
  "explanation": "2-3 sentence Uzbek explanation if wrong. Mention exactly what is wrong. null if correct.",
  "examples": ["Natural, meaningful English example sentence 1", "Natural, meaningful English example sentence 2"],
  "example_translations": ["O'zbekcha tarjima 1", "O'zbekcha tarjima 2"],
  "corrected": "Corrected version of the student's sentence if wrong, otherwise null",
  "sentence_uz": "O'zbekcha tarjima of the student's sentence: '{sentence}'"
}}

STRICT rules:
- Semantically wrong = correct:false, error_type:"meaning"
- Both examples MUST make real, natural sense in English
- sentence_uz MUST be the Uzbek translation of exactly what the student wrote
- explanation and praise MUST be in Uzbek
- Never be harsh; always be encouraging"""


def _call_ai(client, model: str, prompt: str) -> dict | None:
    """Make API call and parse JSON response."""
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=600,
        )
        raw = response.choices[0].message.content.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)
        return {
            "correct":              bool(data.get("correct", False)),
            "praise":               data.get("praise"),
            "error_type":           data.get("error_type"),
            "explanation":          data.get("explanation"),
            "examples":             data.get("examples", []),
            "example_translations": data.get("example_translations", []),
            "corrected":            data.get("corrected"),
            "sentence_uz":          data.get("sentence_uz"),
        }
    except Exception as e:
        print(f"[AI] Error with {model}: {e}")
        return None


def check_sentence(word: str, translation: str, sentence: str) -> dict:
    """Check sentence using AI (Groq → OpenAI → fallback)."""
    prompt = _build_prompt(word, translation, sentence)

    # 1. Try Groq
    if _groq:
        result = _call_ai(_groq, "llama-3.3-70b-versatile", prompt)
        if result:
            print(f"[AI] Groq ✓")
            return result

    # 2. Try OpenAI
    if _openai:
        result = _call_ai(_openai, "gpt-4o-mini", prompt)
        if result:
            print(f"[AI] OpenAI ✓")
            return result

    # 3. Smart rule-based fallback
    print("[AI] Using fallback")
    return _smart_fallback(word, translation, sentence)


# ── Smart fallback ───────────────────────────────────────────────────────────

# Common English word categories for smarter fallback
_COMMON_NOUNS = {
    "table", "chair", "book", "house", "car", "dog", "cat", "water", "food",
    "school", "work", "friend", "family", "city", "country", "day", "time",
    "year", "month", "week", "hand", "eye", "face", "door", "window", "room",
}
_COMMON_VERBS = {
    "run", "eat", "sleep", "study", "work", "go", "come", "see", "know", "think",
    "make", "read", "write", "speak", "listen", "walk", "talk", "help", "love",
    "play", "sit", "stand", "finish", "start", "learn", "teach", "travel",
}


def _smart_fallback(word: str, translation: str, sentence: str) -> dict:
    s = sentence.strip()
    s_lower = s.lower()
    w = word.lower()

    # Blank check
    if not s:
        return _make_error(
            word, translation,
            "usage",
            "Gap bo'sh. Iltimos, so'zni ishlatib to'liq inglizcha gap tuzing.",
        )

    # Word presence — check root forms
    stems = {w, w + "s", w + "ed", w + "ing", w.rstrip("e") + "ing",
              w.rstrip("e") + "ed", w + "er", w + "est", w + "ly"}
    word_found = any(stem in s_lower.split() or
                     f" {stem} " in f" {s_lower} " or
                     s_lower.startswith(stem + " ") or
                     s_lower.endswith(" " + stem)
                     for stem in stems)

    if not word_found:
        return _make_error(
            word, translation,
            "usage",
            f'Gapda "{word}" so\'zi (yoki uning shakli) ishlatilmagan. '
            f'So\'zni to\'g\'ri o\'rinda qo\'llang.',
        )

    # Length check
    if len(s.split()) < 3:
        return _make_error(
            word, translation,
            "grammar",
            "Gap juda qisqa. Kamida 3-4 so'zdan iborat to'liq gap yozing.",
        )

    # Semantic check for nouns used as verbs (common mistake)
    if w in _COMMON_NOUNS:
        verb_patterns = [
            f"i {w} ", f"i {w}.", f"to {w} ", f"can {w}", f"will {w}",
            f"must {w}", f"should {w}", f"let's {w}", f"we {w} ",
        ]
        likely_noun_as_verb = any(pat in f" {s_lower} " for pat in verb_patterns)
        if likely_noun_as_verb:
            noun_ex = _noun_examples(word, translation)
            return {
                "correct": False,
                "praise": None,
                "error_type": "meaning",
                "explanation": (
                    f'"{word}" — bu ot (noun), ya\'ni "{translation}" degan ma\'noni anglatadi. '
                    f'Uni fe\'l (verb) sifatida ishlatish to\'g\'ri emas. '
                    f'Quyidagi misollarga qarang:'
                ),
                "examples": noun_ex["examples"],
                "example_translations": noun_ex["translations"],
                "corrected": noun_ex["corrected"],
                "sentence_uz": f"[Bu gap ma'nosiz: \"{word}\" ot, fe'l emas]",
            }

    # All good
    good = _good_examples(word, translation)
    return {
        "correct": True,
        "praise": f"Zo'r! \"{word}\" so'zini to'g'ri va mazmunli ishlatibsiz. Shunday davom eting!",
        "error_type": None,
        "explanation": None,
        "examples": good["examples"],
        "example_translations": good["translations"],
        "corrected": None,
        "sentence_uz": f"\"{translation}\" so'zidan foydalanib gap tuzgansiz.",
    }


def _make_error(word: str, translation: str, error_type: str, explanation: str) -> dict:
    g = _good_examples(word, translation)
    return {
        "correct": False,
        "praise": None,
        "error_type": error_type,
        "explanation": explanation,
        "examples": g["examples"],
        "example_translations": g["translations"],
        "corrected": g["corrected"],
        "sentence_uz": None,
    }


def _good_examples(word: str, translation: str) -> dict:
    w = word.lower()
    if w in _COMMON_NOUNS:
        return _noun_examples(word, translation)
    # Generic verb example
    return {
        "examples": [
            f"She tries to {w} every morning.",
            f"It is important to {w} regularly.",
        ],
        "translations": [
            f"U har kuni ertalab {translation.split(',')[0].strip()}ga harakat qiladi.",
            f"Muntazam {translation.split(',')[0].strip()} muhimdir.",
        ],
        "corrected": f"I try to {w} every day.",
    }


def _noun_examples(word: str, translation: str) -> dict:
    w = word.lower()
    uz = translation.split(",")[0].strip()
    return {
        "examples": [
            f"There is a {w} in the room.",
            f"She bought a new {w} yesterday.",
        ],
        "translations": [
            f"Xonada bir {uz} bor.",
            f"U kecha yangi {uz} sotib oldi.",
        ],
        "corrected": f"I have a {w} in my room.",
    }
