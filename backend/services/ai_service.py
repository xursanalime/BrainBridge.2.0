"""
AI service for sentence checking — BrainBridge v3.1
Priority: Groq (llama-3.3-70b) → OpenAI (gpt-4o-mini) → smart fallback

Response schema:
{
  "correct":               bool,
  "praise":                str | None,     # O'zbek maqtov (to'g'ri bo'lsa)
  "error_type":            str | None,     # "grammar" | "usage" | "meaning"
  "error_label":           str | None,     # O'zbek: "Grammatik xato" | "Ishlatish xatosi" | "Ma'no xatosi"
  "explanation":           str | None,     # O'zbek tushuntirish (xato bo'lsa)
  "examples":              list[str],      # 5 ta to'g'ri misol (inglizcha)
  "example_translations":  list[str],      # 5 misolning o'zbekcha tarjimasi
  "corrected":             str | None,     # Tuzatilgan variant
  "sentence_uz":           str | None,     # User yozgan gapning o'zbek tarjimasi
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
        print(f"[AI] Groq initialized ✓")
    else:
        print("[AI] GROQ_API_KEY not set — will use fallback")
except ImportError:
    print("[AI] groq package not installed")

# ── Fallback: OpenAI ─────────────────────────────────────────────────────────
_openai = None
try:
    from openai import OpenAI
    _oai_key = os.getenv("OPENAI_API_KEY", "")
    if _oai_key:
        _openai = OpenAI(api_key=_oai_key)
        print(f"[AI] OpenAI initialized ✓")
except ImportError:
    pass


# ── Error type labels ────────────────────────────────────────────────────────
_ERROR_LABELS = {
    "grammar": "Grammatik xato",
    "usage":   "Ishlatish xatosi",
    "meaning": "Ma'no xatosi",
}


# ── Prompt ───────────────────────────────────────────────────────────────────
def _build_prompt(word: str, translation: str, sentence: str) -> str:
    prompt = (
        "You are a strict but encouraging English teacher checking sentences written by Uzbek learners.\n\n"
        f"Target word: {word} (Uzbek: {translation})\n"
        f"Student sentence: {sentence}\n\n"
        "Check carefully:\n"
        "1. Grammar - is the sentence grammatically correct English?\n"
        f"2. Usage - is {word} used in the correct form and context?\n"
        "3. Meaning - does the sentence make real-world logical sense?\n\n"
        f"Generate EXACTLY 5 natural example sentences using {word} correctly.\n"
        "Each example must have a different structure: statement, question, negative, past tense, future tense.\n\n"
        "Respond with ONLY valid JSON (no markdown, no code fences):\n"
        "{\n"
        '  "correct": true or false,\n'
        '  "praise": "Short warm Uzbek praise if correct, null if wrong",\n'
        '  "error_type": "grammar or usage or meaning, null if correct",\n'
        '  "explanation": "2-4 Uzbek sentences explaining the exact mistake. null if correct.",\n'
        '  "examples": ["example1", "example2", "example3", "example4", "example5"],\n'
        '  "example_translations": ["uzb1", "uzb2", "uzb3", "uzb4", "uzb5"],\n'
        '  "corrected": "Corrected student sentence if wrong, null if correct",\n'
        '  "sentence_uz": "Uzbek translation of exactly what the student wrote"\n'
        "}\n\n"
        "RULES:\n"
        "- explanation and praise MUST be in Uzbek language\n"
        "- sentence_uz MUST translate exactly what the student wrote\n"
        "- All 5 examples must be natural real-world correct English\n"
        "- Never be harsh, always encourage the student"
    )
    return prompt


def _call_ai(client, model: str, prompt: str) -> dict | None:
    """Make API call and parse JSON response."""
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=900,
        )
        raw = response.choices[0].message.content.strip()
        # Strip markdown fences if present
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        raw = raw.strip()
        data = json.loads(raw)

        # Ensure exactly 5 examples
        examples = data.get("examples", [])
        translations = data.get("example_translations", [])
        # Pad to 5 if fewer returned
        while len(examples) < 5:
            examples.append(f"She uses the word '{prompt[:10]}' every day.")
        while len(translations) < 5:
            translations.append("")

        return {
            "correct":              bool(data.get("correct", False)),
            "praise":               data.get("praise"),
            "error_type":           data.get("error_type"),
            "error_label":          _ERROR_LABELS.get(data.get("error_type", ""), None),
            "explanation":          data.get("explanation"),
            "examples":             examples[:5],
            "example_translations": translations[:5],
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
            print(f"[AI] Groq ✓  correct={result['correct']}")
            return result

    # 2. Try OpenAI
    if _openai:
        result = _call_ai(_openai, "gpt-4o-mini", prompt)
        if result:
            print(f"[AI] OpenAI ✓  correct={result['correct']}")
            return result

    # 3. Smart rule-based fallback
    print("[AI] Using smart fallback")
    return _smart_fallback(word, translation, sentence)


# ── Smart fallback ───────────────────────────────────────────────────────────

_COMMON_NOUNS = {
    "table", "chair", "book", "house", "car", "dog", "cat", "water", "food",
    "school", "work", "friend", "family", "city", "country", "day", "time",
    "year", "month", "week", "hand", "eye", "face", "door", "window", "room",
    "phone", "computer", "street", "park", "store", "market", "office",
}
_COMMON_VERBS = {
    "run", "eat", "sleep", "study", "work", "go", "come", "see", "know",
    "think", "make", "read", "write", "speak", "listen", "walk", "talk",
    "help", "love", "play", "sit", "stand", "finish", "start", "learn",
    "teach", "travel", "kick", "hit", "jump", "fly", "drive", "swim",
}


def _smart_fallback(word: str, translation: str, sentence: str) -> dict:
    s = sentence.strip()
    s_lower = s.lower()
    w = word.lower()

    if not s:
        return _make_error(word, translation, "usage",
                           "Gap bo'sh. Iltimos, so'zni ishlatib to'liq inglizcha gap tuzing.")

    # Word presence check
    stems = {w, w + "s", w + "ed", w + "ing", w.rstrip("e") + "ing",
              w.rstrip("e") + "ed", w + "er", w + "est", w + "ly",
              w + "tion", w + "ness", w + "ment"}
    word_found = any(
        stem in s_lower.split() or
        f" {stem} " in f" {s_lower} " or
        s_lower.startswith(stem + " ") or
        s_lower.endswith(" " + stem)
        for stem in stems
    )

    if not word_found:
        return _make_error(word, translation, "usage",
                           f'Gapda "{word}" so\'zi (yoki uning shakli) ishlatilmagan. '
                           f'So\'zni to\'g\'ri o\'rinda qo\'llang.')

    if len(s.split()) < 3:
        return _make_error(word, translation, "grammar",
                           "Gap juda qisqa. Kamida 3-4 so'zdan iborat to'liq gap yozing.")

    # Noun used as verb detection
    if w in _COMMON_NOUNS:
        verb_patterns = [f"i {w} ", f"i {w}.", f"to {w} ", f"can {w}",
                         f"will {w}", f"must {w}", f"should {w}"]
        if any(pat in f" {s_lower} " for pat in verb_patterns):
            examples = _generate_5_examples(word, translation, is_noun=True)
            return {
                "correct": False,
                "praise": None,
                "error_type": "meaning",
                "error_label": "Ma'no xatosi",
                "explanation": (
                    f'"{word}" — bu ot (noun), ya\'ni "{translation}" degan ma\'noni anglatadi. '
                    f'Uni fe\'l (verb) sifatida ishlatish to\'g\'ri emas. '
                    f'Quyidagi to\'g\'ri misollarga qarang:'
                ),
                "examples": examples["examples"],
                "example_translations": examples["translations"],
                "corrected": examples["corrected"],
                "sentence_uz": f'[Bu gap ma\'nosiz: "{word}" ot, fe\'l emas]',
            }

    # All good
    examples = _generate_5_examples(word, translation, is_noun=w in _COMMON_NOUNS)
    return {
        "correct": True,
        "praise": f'Zo\'r! "{word}" so\'zini to\'g\'ri va mazmunli ishlatibsiz. Shunday davom eting! 🎉',
        "error_type": None,
        "error_label": None,
        "explanation": None,
        "examples": examples["examples"],
        "example_translations": examples["translations"],
        "corrected": None,
        "sentence_uz": f'"{translation}" so\'zidan foydalanib gap tuzgansiz.',
    }


def _make_error(word: str, translation: str, error_type: str, explanation: str) -> dict:
    examples = _generate_5_examples(word, translation, is_noun=word.lower() in _COMMON_NOUNS)
    return {
        "correct": False,
        "praise": None,
        "error_type": error_type,
        "error_label": _ERROR_LABELS.get(error_type, "Xato"),
        "explanation": explanation,
        "examples": examples["examples"],
        "example_translations": examples["translations"],
        "corrected": examples["corrected"],
        "sentence_uz": None,
    }


def _generate_5_examples(word: str, translation: str, is_noun: bool = False) -> dict:
    """Generate 5 varied example sentences for a word."""
    w = word.lower()
    uz = translation.split(",")[0].strip()

    if is_noun:
        return {
            "examples": [
                f"There is a {w} in the room.",
                f"She bought a new {w} yesterday.",
                f"I need to fix the {w} in my office.",
                f"Have you seen my {w}? I can't find it.",
                f"The old {w} was replaced with a modern one.",
            ],
            "translations": [
                f"Xonada bir {uz} bor.",
                f"U kecha yangi {uz} sotib oldi.",
                f"Men ofisimdagi {uz}ni ta'mirlashim kerak.",
                f"{uz.capitalize()}imni ko'rdingizmi? Topa olmayapman.",
                f"Eski {uz} zamonaviysi bilan almashtirildi.",
            ],
            "corrected": f"I have a {w} in my room.",
        }
    else:
        return {
            "examples": [
                f"She tries to {w} every morning.",
                f"It is important to {w} regularly.",
                f"He didn't {w} yesterday because he was tired.",
                f"Can you {w} with me after school?",
                f"They have been learning how to {w} for months.",
            ],
            "translations": [
                f"U har kuni ertalab {uz}ga harakat qiladi.",
                f"Muntazam {uz} muhimdir.",
                f"U kecha charchagani uchun {uz}madi.",
                f"Maktabdan keyin men bilan {uz}a olasizmi?",
                f"Ular oylar davomida qanday {uz}ishni o'rganmoqda.",
            ],
            "corrected": f"I try to {w} every day.",
        }
