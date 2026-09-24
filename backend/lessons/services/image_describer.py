"""Explain an extracted figure to a blind / low-vision learner.

Given the raw bytes of a figure pulled out of a teacher's PDF, ask a local
vision model (Ollama) what *concept* the figure teaches — not where things
sit on the page, but the science it is there to show.

This is purely additive. If Ollama is temporarily unavailable, extraction keeps
the figure and its caption or visible-text fallback. Confirmation and publishing
retry blank narrations using the saved image file.

Point it at a different Ollama vision model with IMAGE_DESCRIPTION_MODEL
(llava, moondream, qwen2-vl, llama3.2-vision …), or turn it off entirely
with IMAGE_DESCRIPTION_ENABLED=False.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import re
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

_SKIP = "SKIP"
_MAX_NARRATION_SENTENCES = 6
_MAX_VISIBLE_TEXT = 400
_MAX_NEARBY_TEXT = 600
_CACHE_VERSION = "3"
_CACHE_SKIP = "__SKIP__"
_cache_lock = threading.Lock()
_reachability_lock = threading.Lock()
_reachable_until = 0.0


def _cfg(name: str, default):
    return getattr(settings, name, default)


def _base_url() -> str:
    return _cfg("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")


def _service_reachable() -> bool:
    """Cache successful checks briefly; failures remain immediately retryable."""
    global _reachable_until
    now = time.monotonic()
    with _reachability_lock:
        if now < _reachable_until:
            return True
    try:
        response = requests.get(f"{_base_url()}/api/tags", timeout=2)
        reachable = response.status_code == 200
        if reachable:
            ttl = max(0, int(_cfg("IMAGE_DESCRIPTION_REACHABILITY_TTL", 15)))
            with _reachability_lock:
                _reachable_until = time.monotonic() + ttl
        return reachable
    except requests.RequestException:
        return False


def _cache_path() -> Path:
    configured = _cfg(
        "IMAGE_DESCRIPTION_CACHE_PATH",
        Path(settings.BASE_DIR) / "image_description_cache" / "descriptions.sqlite3",
    )
    return Path(configured)


def _cache_key(image_bytes: bytes, prompt: str, model: str) -> str:
    digest = hashlib.sha256()
    for value in (_CACHE_VERSION.encode(), model.encode(), prompt.encode(), image_bytes):
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
    return digest.hexdigest()


def _cached_description(key: str) -> str | None:
    if not _cfg("IMAGE_DESCRIPTION_CACHE_ENABLED", True):
        return None
    try:
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _cache_lock, sqlite3.connect(path, timeout=5) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS descriptions "
                "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            row = connection.execute(
                "SELECT value FROM descriptions WHERE key = ?", (key,)
            ).fetchone()
        return row[0] if row else None
    except (OSError, sqlite3.Error) as exc:
        logger.warning("figure description cache read failed: %s", exc)
        return None


def _store_cached_description(key: str, value: str) -> None:
    if not _cfg("IMAGE_DESCRIPTION_CACHE_ENABLED", True):
        return
    try:
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _cache_lock, sqlite3.connect(path, timeout=5) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS descriptions "
                "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT OR REPLACE INTO descriptions (key, value) VALUES (?, ?)",
                (key, value),
            )
    except (OSError, sqlite3.Error) as exc:
        logger.warning("figure description cache write failed: %s", exc)


def reset_reachability_cache() -> None:
    """Clear local test/runtime caches without changing extracted lesson data."""
    global _reachable_until
    with _reachability_lock:
        _reachable_until = 0.0
    try:
        path = _cache_path()
        if not path.exists():
            return
        with _cache_lock, sqlite3.connect(path, timeout=5) as connection:
            connection.execute("DELETE FROM descriptions")
    except (OSError, sqlite3.Error):
        return


def nearby_lesson_text(blocks, *, page_number, bbox=None, limit=_MAX_NEARBY_TEXT, fallback=""):
    """The lesson text printed around this figure, nearest first.

    What used to fill this slot was the document's opening, whatever page the
    figure was on. On a one-page handout that is the text beside the figure by
    accident; on anything longer the model was shown page one and told it was
    looking at the text near a figure five pages away.

    The budget is spent on the closest blocks, because a figure sits with the
    passage it illustrates, but what is kept is then put back into reading
    order: handing the model a page bottom-up would be a new way of confusing
    it. A page holding no text at all keeps whatever the caller already had.
    """
    on_page = [item for item in blocks if item.get("page") == page_number and (item.get("text") or "").strip()]
    if not on_page:
        return fallback

    def top(item):
        box = item.get("bbox") or (0, 0, 0, 0)
        return float(box[1])

    if bbox:
        centre = (float(bbox[1]) + float(bbox[3])) / 2
        ordered = sorted(on_page, key=lambda item: (abs(top(item) - centre), top(item)))
    else:
        ordered = sorted(on_page, key=top)

    kept, used = [], 0
    for item in ordered:
        text = " ".join((item.get("text") or "").split())
        if not text:
            continue
        # The first block is taken whatever its length: a figure whose only
        # neighbour is one long paragraph would otherwise get no context.
        if kept and used + len(text) + 1 > limit:
            continue
        kept.append(item)
        used += len(text) + 1
    kept.sort(key=top)
    return "\n".join(" ".join((item.get("text") or "").split()) for item in kept)[:limit]


def build_prompt(
    *,
    lesson_title: str = "",
    nearby_text: str = "",
    caption: str = "",
    visible_text: str = "",
) -> str:
    """Role, Task, Context, Format -- four labelled sections, not one paragraph.

    The lesson text around a figure has to be passed in: without it a small
    vision model cannot tell which of several possible ideas the figure is
    there to teach. Run together with the instructions, though, it read as
    material to reproduce, and the model paraphrased the lesson back instead
    of describing the figure. Separating the sections marks that text plainly
    as background and leaves the instructions unambiguous. Empty values are
    omitted, so no label is ever printed without a value behind it.
    """
    sections = [
        "ROLE:\n"
        "You are writing spoken audio description of a figure for a blind "
        "student who is following a science lesson by listening.",

        "TASK:\n"
        "Say what this figure TEACHES by saying what it shows and how the "
        "things in it differ from one another. Give the student the "
        "understanding a sighted classmate would take from looking at it.\n"
        "Report the visual facts that carry the meaning: how things are "
        "arranged, how closely or widely they are spaced, how they are "
        "grouped or ordered, how many there are, how large they are beside "
        "each other, and the direction of any change.\n"
        "Never mention colours. Do not say what colour anything is, not even "
        "to tell two things apart: a student who is listening gains nothing "
        "from it. Tell them apart by what they are, or by where they come in "
        "the figure -- the first, the second, the third.\n"
        "Do NOT name the artwork or its decoration either: no arrows, and "
        "never the words \"diagram\", \"chart\", \"graph\", \"figure\" or "
        "\"photo\".",
    ]

    context_lines = []
    if lesson_title:
        context_lines.append(f'Lesson title: "{lesson_title}"')
    if caption:
        context_lines.append(f'Figure caption: "{caption.strip()}"')
    if visible_text:
        context_lines.append(
            f'Text printed inside the figure: "{visible_text.strip()[:_MAX_VISIBLE_TEXT]}"'
        )
    if nearby_text:
        context_lines.append(
            f'Lesson text near the figure: "{nearby_text.strip()[:_MAX_NEARBY_TEXT]}"'
        )
    if context_lines:
        heading = (
            "CONTEXT (background only — this is what the student has already "
            "been told. Use it to work out what the figure is for. Do NOT "
            "repeat, restate, summarise or paraphrase any of it back."
        )
        if nearby_text:
            # A small model obeys an instruction about what to write far more
            # reliably than one about what to leave out, so the ban is paired
            # with the job it leaves behind: the lesson has the idea in words
            # already, and the visual specifics are what it cannot carry.
            heading += (
                " Where it already explains an idea in words, do not explain "
                "it again -- give the visual specifics those words leave out."
            )
        sections.append(heading + "):\n" + "\n".join(context_lines))

    sections.append(
        "FORMAT:\n"
        "Start immediately with the content of the figure. Write NO preamble "
        "and no meta-sentence: do not greet, do not say what you are about to "
        "do, do not mention the student, the teacher, the lesson, yourself or "
        "the word description. Never begin with phrases such as \"Okay\", "
        "\"Sure\", \"Here is\", \"Here's a description\" or \"Let's describe\". "
        "The very first word must be part of the explanation itself.\n"
        "Write one natural spoken paragraph of 2 to 4 plain sentences. Use 2 "
        "sentences for one simple idea and 3 to 4 for a moderate comparison, "
        "relationship, or short process. Go beyond 4 sentences only for a "
        "genuinely complex table or multi-step figure, and never write more "
        "than 6. Do not add detail merely to make the narration longer.\n"
        f'If the figure is decorative, a logo, or too unclear to explain, '
        f'reply with exactly "{_SKIP}" and nothing else.'
    )
    return "\n\n".join(sections)


# A disobedient model still opens with chatter addressed to the teacher or the
# student instead of the lesson: measured live, every stored description began
# with one such sentence, which was then spoken aloud and made unrelated
# figures score alike. The prompt above is the primary mechanism; everything
# below is the deterministic safety net applied after generation.
#
# The net is built to under-reach rather than over-reach, because what it
# removes a blind student never hears. Two kinds of opener are told apart:
#
#   * an *interjection* -- "Okay,", "Sure," -- which is evidence of chatter
#     but is not chatter by itself. "Right after heating, the particles move
#     faster." and "Great differences in spacing separate the three states."
#     are lesson content, so the five ambiguous words below count only when
#     punctuation closes them off; "okay" and "alright" never open a sentence
#     about science and need no such guard.
#   * a *meta phrase* -- "Here's a description...", "Let's describe...",
#     "I will explain..." -- which says what the model is about to do and
#     carries no lesson content at all.
#
# Only a meta phrase is ever deleted. An interjection alone leaves the text
# untouched unless it *is* the whole sentence ("Okay.").
_LEADING_INTERJECTION = re.compile(
    r"^(?:(?:okay|ok|alright)\b"
    r"|(?:right|great|sure|certainly|of\s+course)\b(?=\s*[,.!;:]))"
    r"[\s,.!;:—–-]*",
    re.I,
)

_META_OPENERS = (
    # "Here's" and "Here is" both: the apostrophe form carries no space.
    re.compile(r"^(?:here|this)\s*(?:is|'s|’s)\s+(?:a|an|the|my)?\s*"
               r"(?:spoken\s+|audio\s+|short\s+|brief\s+|natural\s+)*"
               r"(?:description|narration|explanation|summary|paragraph)\b", re.I),
    re.compile(r"^let(?:'s|’s| us)\s+(?:describe|explain|take|look|go|break)\b", re.I),
    re.compile(r"^(?:i|i'll|i will|i can|we|we'll|we will)\s+(?:am\s+)?"
               r"(?:going to\s+)?(?:now\s+)?"
               r"(?:describe|explain|write|give|provide|do|help)\b", re.I),
    re.compile(r"^(?:the\s+)?(?:following|below)\s+is\b", re.I),
    re.compile(r"^as (?:requested|asked)\b", re.I),
)


def _leading_interjection_end(sentence: str) -> int:
    """How much of this sentence is a leading interjection; 0 when none is."""
    match = _LEADING_INTERJECTION.match(sentence)
    return match.end() if match else 0


def _is_meta(clause: str) -> bool:
    """Does this clause say what the model is about to do, rather than teach?"""
    return any(pattern.search(clause) for pattern in _META_OPENERS)


def _strip_model_chatter(text: str) -> str:
    """Drop a leading preamble; a clean description is returned unchanged."""
    cleaned = " ".join((text or "").split()).strip()
    if not cleaned:
        return ""
    sentences = _spoken_sentences(cleaned)
    first = sentences[0].lstrip("*_#“\"' ").strip()
    rest = sentences[1:]
    body = first[_leading_interjection_end(first):].strip()

    if not body:
        # The interjection was the whole sentence -- a bare "Okay.".
        return _strip_model_chatter(" ".join(rest)) if rest else cleaned

    # A preamble often ends in a colon rather than a full stop, so the clause
    # before the colon is tested on its own. The search is confined to the
    # FIRST sentence and to that clause: a colon later in the description, or
    # one introducing a list ("three states: solid, liquid and gas"), belongs
    # to the lesson, and cutting at it mangles what the student hears.
    head, separator, tail = body.partition(":")
    if separator and tail.strip() and _is_meta(head):
        return _strip_model_chatter(" ".join([tail.strip(), *rest]))

    if rest and _is_meta(body):
        return _strip_model_chatter(" ".join(rest))

    # Either this sentence carries lesson content or there is nothing else to
    # fall back on. Leave the text exactly as the model wrote it.
    return cleaned


def _looks_like_skip(text: str) -> bool:
    """SKIP, even behind a leading interjection the model could not resist."""
    cleaned = " ".join((text or "").split()).strip().lstrip("*_#“\"' ").strip()
    cleaned = cleaned[_leading_interjection_end(cleaned):].strip()
    return cleaned.upper().strip(".!\"' ") == _SKIP


def _spoken_sentences(text: str) -> list[str]:
    """Return complete prose sentences while preserving their punctuation."""
    cleaned = " ".join((text or "").split()).strip()
    if not cleaned:
        return []
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", cleaned)
        if sentence.strip()
    ]
    return sentences or [cleaned]


def _cap_narration_length(text: str) -> str:
    """Honor the spoken-audio ceiling even if the model overruns it."""
    return " ".join(_spoken_sentences(text)[:_MAX_NARRATION_SENTENCES]).strip()


def describe_image_for_lesson(
    image_bytes: bytes | None,
    *,
    lesson_title: str = "",
    nearby_text: str = "",
    caption: str = "",
    visible_text: str = "",
) -> str:
    """A spoken explanation of what the figure teaches, or "" if unavailable."""
    if not image_bytes:
        return ""
    if not _cfg("IMAGE_DESCRIPTION_ENABLED", True):
        return ""

    model = _cfg("IMAGE_DESCRIPTION_MODEL", "gemma3:4b")
    prompt = build_prompt(
        lesson_title=lesson_title,
        nearby_text=nearby_text,
        caption=caption,
        visible_text=visible_text,
    )
    cache_key = _cache_key(image_bytes, prompt, model)
    cached = _cached_description(cache_key)
    if cached is not None:
        return "" if cached == _CACHE_SKIP else cached

    if not _service_reachable():
        return ""

    payload = {
        "model": model,
        "prompt": prompt,
        "images": [base64.b64encode(image_bytes).decode("ascii")],
        "stream": False,
        "options": {
            "temperature": 0.2,
            # Enough room for six concise spoken sentences without allowing an
            # unexpectedly verbose response to run indefinitely.
            "num_predict": 512,
        },
    }
    try:
        response = requests.post(
            f"{_base_url()}/api/generate",
            json=payload,
            timeout=int(_cfg("IMAGE_DESCRIPTION_TIMEOUT", 300)),
        )
        response.raise_for_status()
        text = (response.json().get("response") or "").strip()
    except (requests.RequestException, ValueError) as exc:
        logger.warning(
            "figure description failed (%s): %s",
            payload["model"],
            exc,
        )
        return ""

    if not text:
        return ""
    # Stripped before the SKIP test: a model that prefixes its refusal --
    # "Okay, SKIP." -- was otherwise stored and spoken as a description.
    text = _strip_model_chatter(text)
    if not text:
        return ""
    if _looks_like_skip(text):
        _store_cached_description(cache_key, _CACHE_SKIP)
        return ""
    text = _cap_narration_length(text)
    if not text:
        return ""
    _store_cached_description(cache_key, text)
    return text


def _learning_object_image_bytes(learning_object) -> bytes | None:
    """Read a saved image without allowing paths outside MEDIA_ROOT."""
    image_url = str(learning_object.image_url or "").strip()
    if not image_url:
        return None
    url_path = urlparse(image_url).path
    media_url = str(settings.MEDIA_URL or "/media/")
    if not url_path.startswith(media_url):
        return None
    relative = url_path[len(media_url):].lstrip("/\\")
    media_root = Path(settings.MEDIA_ROOT).resolve()
    candidate = (media_root / relative).resolve()
    if not candidate.is_relative_to(media_root) or not candidate.is_file():
        return None
    return candidate.read_bytes()


def populate_missing_image_descriptions(material) -> dict:
    """Retry blank image narrations using images already stored by the backend."""
    from lessons.models import LearningObject

    images = [item for item in material.learning_objects.filter(
        kind=LearningObject.Kind.IMAGE,
    ).order_by("order", "id") if not (item.content or "").strip()]
    logger.info("Image narration retry: material=%s blank_images=%s", material.id, len(images))
    generated_ids = []
    errors = []
    for learning_object in images:
        try:
            image_bytes = _learning_object_image_bytes(learning_object)
        except OSError as exc:
            errors.append({"learning_object_id": learning_object.id, "detail": str(exc)})
            continue
        if not image_bytes:
            errors.append({
                "learning_object_id": learning_object.id,
                "detail": "The saved image file is unavailable on the backend.",
            })
            continue
        description = describe_image_for_lesson(
            image_bytes,
            lesson_title=(material.outline_node.title if material.outline_node_id else material.title),
            nearby_text=(material.extracted_text or "")[:_MAX_NEARBY_TEXT],
            caption=learning_object.title,
        )
        if not description:
            errors.append({
                "learning_object_id": learning_object.id,
                "detail": "Gemma did not return an image narration. Confirm Ollama is running and retry.",
            })
            continue
        learning_object.content = description
        learning_object.save(update_fields=["content"])
        generated_ids.append(learning_object.id)
        logger.info(
            "Image narration generated: material=%s learning_object=%s model=%s",
            material.id,
            learning_object.id,
            _cfg("IMAGE_DESCRIPTION_MODEL", "gemma3:4b"),
        )
    return {
        "generated_count": len(generated_ids),
        "generated_learning_object_ids": generated_ids,
        "errors": errors,
    }
