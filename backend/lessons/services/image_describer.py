"""Explain an extracted figure to a blind / low-vision learner.

Given the raw bytes of a figure pulled out of a teacher's PDF, ask a local
vision model (Ollama) what *concept* the figure teaches — not where things
sit on the page, but the science it is there to show.

This is purely additive. If Ollama is temporarily unavailable, extraction keeps
the figure and its caption or visible-text fallback. Publishing retries
narrations that are blank or only that caption, using the saved image file.

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
from config.groq_client import generate as groq_generate

logger = logging.getLogger(__name__)

_SKIP = "SKIP"
_MAX_NARRATION_SENTENCES = 6
_MAX_VISIBLE_TEXT = 400
_MAX_NEARBY_TEXT = 600

# A web address in a caption is the picture's source credit ("Figure 3.
# Greenhouse Gas Effect: https://www.flickr.com/..."). It says nothing about
# what the picture shows, and read aloud it is a string of letters.
_LINK = re.compile(r"\s*(?:https?://|www\.)\S+", re.IGNORECASE)
# "Figure 3." / "Table 2:" -- the label a printed caption opens with.
_CAPTION_LABEL = re.compile(
    r"\s*(?:figure|fig\.?|table|diagram|illustration)\s*\d+[a-z]?\s*[.:]",
    re.IGNORECASE,
)
# A printed caption is a line or two; a narration is longer.
_MAX_CAPTION_WORDS = 30


def caption_without_links(text: str) -> str:
    """The caption with its source links removed."""
    cleaned = " ".join(_LINK.sub("", text or "").split())
    return cleaned.rstrip(" :;,-–—")


def is_caption_only(text: str) -> bool:
    """True when a figure's narration is just its printed caption.

    That is what extraction keeps when no model described the figure, and it
    is not a narration: a learner who cannot see the figure hears its label
    and a source link, nothing about what it shows.
    """
    cleaned = caption_without_links(text)
    return bool(
        cleaned
        and _CAPTION_LABEL.match(cleaned)
        and len(cleaned.split()) <= _MAX_CAPTION_WORDS
    )
_CACHE_VERSION = "3"
_CACHE_SKIP = "__SKIP__"
# How far under a figure its own description may start: about two lines.
_MAX_DESCRIPTION_GAP = 40.0
_cache_lock = threading.Lock()
_reachability_lock = threading.Lock()
_reachable_until = 0.0


def _cfg(name: str, default):
    return getattr(settings, name, default)


def _base_url() -> str:
    return _cfg("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")


def _service_reachable() -> bool:
    """Cache successful checks briefly; failures remain immediately retryable."""
    if _cfg("LLM_PROVIDER", "ollama") == "groq":
        return bool(_cfg("GROQ_API_KEY", ""))
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


def _nearby_blocks(blocks, *, page_number, bbox=None, limit=_MAX_NEARBY_TEXT, siblings=None):
    """The lesson text blocks printed around this figure, in reading order.

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
        return []

    def top(item):
        box = item.get("bbox") or (0, 0, 0, 0)
        return float(box[1])

    def middle(box):
        return (float(box[1]) + float(box[3])) / 2

    # A page carrying two figures gave each of them the whole page, so a food
    # web was handed the water cycle's paragraph and the water cycle the food
    # web's. Each block belongs to whichever figure on the page it sits
    # nearest, and a page with one figure is unaffected.
    others = [
        item for item in (siblings or [])
        if item.get("page_number") == page_number and item.get("bbox") and item.get("bbox") != bbox
    ]
    if others and bbox:
        mine = middle(bbox)
        on_page = [
            item for item in on_page
            if all(
                abs(top(item) - mine) <= abs(top(item) - middle(other["bbox"]))
                for other in others
            )
        ]
        if not on_page:
            return []

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
    return kept


def _block_text(blocks, limit=_MAX_NEARBY_TEXT):
    return "\n".join(" ".join((item.get("text") or "").split()) for item in blocks)[:limit]


def nearby_lesson_text(
    blocks, *, page_number, bbox=None, limit=_MAX_NEARBY_TEXT, fallback="", siblings=None,
):
    """The lesson text printed around this figure, in reading order."""
    kept = _nearby_blocks(
        blocks, page_number=page_number, bbox=bbox, limit=limit, siblings=siblings,
    )
    return _block_text(kept, limit) if kept else fallback


def lesson_text_around(
    blocks, *, page_number, bbox=None, limit=_MAX_NEARBY_TEXT, siblings=None,
):
    """Split that text into what the lesson has said and what it will say.

    A figure is usually printed above the passage that explains it, so a
    description that explains the concept in full says it first and the lesson
    then says it again moments later. The student hears the same thing twice
    and the figure's own contribution -- what it actually looks like -- is
    crowded out.

    Position is what tells the two apart, and the distinction only exists
    because the figure's box is known. A figure with nothing below it is
    explained in full, as it must be: nothing follows to do the explaining.
    """
    kept = _nearby_blocks(
        blocks, page_number=page_number, bbox=bbox, limit=limit, siblings=siblings,
    )

    def top(item):
        return float((item.get("bbox") or (0, 0, 0, 0))[1])

    if kept and bbox:
        figure_top, figure_bottom = float(bbox[1]), float(bbox[3])
        # A block straddling the figure's own band is counted as already said:
        # treating it as upcoming would suppress the description on the
        # strength of a caption or a stray line beside the graphic.
        before = [item for item in kept if top(item) < figure_bottom]
        after = [item for item in kept if top(item) >= figure_bottom]
        if figure_top == figure_bottom:  # a zero-height box tells us nothing
            before, after = kept, []
    else:
        before, after = kept, []

    # A figure at the foot of a page, or alone on one, is explained overleaf.
    # Each side is filled from the neighbouring page only when the figure's own
    # page has nothing there, so a figure already sitting with its passage is
    # never given another page's as well.
    if not before:
        before = _page_blocks(blocks, page_number - 1, limit, tail=True)
    if not after:
        after = _page_blocks(blocks, page_number + 1, limit, tail=False)
    return {"before": _block_text(before, limit), "after": _block_text(after, limit)}


def passage_directly_below(blocks, *, page_number, bbox):
    """The text block printed right under a figure, or ``None``.

    Where a PDF puts a figure's own description: in the figure's column,
    starting within a couple of lines of its bottom edge. A printed caption in
    between ("Figure 1: ...") is stepped over.
    """
    if not bbox:
        return None
    left, _top, right, bottom = (float(value) for value in bbox)

    def box(item):
        return [float(value) for value in (item.get("bbox") or (0, 0, 0, 0))]

    def shares_column(item):
        x0, _, x1, _ = box(item)
        overlap = min(right, x1) - max(left, x0)
        return overlap > 0.5 * min(right - left, x1 - x0)

    below = sorted(
        (
            item for item in blocks
            if item.get("page") == page_number
            and (item.get("text") or "").strip()
            and box(item)[1] >= bottom - 2
            and shares_column(item)
        ),
        key=lambda item: box(item)[1],
    )
    edge = bottom
    for item in below:
        if box(item)[1] - edge > _MAX_DESCRIPTION_GAP:
            return None
        if _CAPTION_LABEL.match(item.get("text") or ""):
            edge = box(item)[3]
            continue
        return item
    return None


_SENTENCE_END = re.compile(r"[.!?][\"'”’)\]]*$")


def _block_box(item):
    return [float(value) for value in (item.get("bbox") or (0, 0, 0, 0))]


def _in_column(item, left, right):
    x0, _, x1, _ = _block_box(item)
    return min(right, x1) - max(left, x0) > 0.5 * min(right - left, x1 - x0)


def _joins_paragraph(earlier_text, later_text):
    """One paragraph split over two text blocks: no full stop, or a lower-case restart."""
    earlier, later = (earlier_text or "").strip(), (later_text or "").strip()
    return bool(earlier and later) and (
        not _SENTENCE_END.search(earlier) or later[:1].islower()
    )


def passage_blocks_below(blocks, *, page_number, bbox):
    """The paragraph printed right under a figure, as its text blocks (maybe none).

    PyMuPDF can split one paragraph into several blocks. Taking only the first
    left the rest of a printed description in the lesson, where the learner
    heard it after the figure had already said it.
    """
    first = passage_directly_below(blocks, page_number=page_number, bbox=bbox)
    if first is None:
        return []
    left, _top, right, _bottom = (float(value) for value in bbox)
    following = sorted(
        (
            item for item in blocks
            if item.get("page") == page_number
            and (item.get("text") or "").strip()
            and _block_box(item)[1] > _block_box(first)[1]
            and _in_column(item, left, right)
        ),
        key=lambda item: _block_box(item)[1],
    )
    passage = [first]
    for item in following:
        if _block_box(item)[1] - _block_box(passage[-1])[3] > _MAX_DESCRIPTION_GAP:
            break
        if not _joins_paragraph(passage[-1].get("text"), item.get("text")):
            break
        passage.append(item)
    return passage


def passage_blocks_above(blocks, *, page_number, bbox):
    """The paragraph printed right above a figure, as its text blocks (maybe none)."""
    if not bbox:
        return []
    left, top, right, _bottom = (float(value) for value in bbox)
    above = sorted(
        (
            item for item in blocks
            if item.get("page") == page_number
            and (item.get("text") or "").strip()
            and _block_box(item)[3] <= top + 2
            and _in_column(item, left, right)
        ),
        key=lambda item: _block_box(item)[3],
        reverse=True,
    )
    edge = top
    passage = []
    for item in above:
        if edge - _block_box(item)[3] > _MAX_DESCRIPTION_GAP:
            break
        if not passage and _CAPTION_LABEL.match(item.get("text") or ""):
            # A caption printed over the figure ("Table 1: ...") is stepped over.
            edge = _block_box(item)[1]
            continue
        if passage and not _joins_paragraph(item.get("text"), passage[0].get("text")):
            break
        passage.insert(0, item)
        edge = _block_box(item)[1]
    return passage


def passage_text(passage_blocks):
    return " ".join(" ".join((item.get("text") or "").split()) for item in passage_blocks)


def redundant_printed_passages(blocks, *, page_number, bbox, narration):
    """The printed paragraphs next to a figure that say what its narration says.

    Returns ``[{"where", "blocks", "text", "score"}]`` for the passage below and
    the one above that pass the check, the passage below first. Scored with the
    same TF-IDF cosine as the upload-time printed-description check, so both
    paths agree on what counts as the same explanation.
    """
    from .content_generator import (
        _MIN_PRINTED_DESCRIPTION_WORDS,
        _PRINTED_DESCRIPTION_SIMILARITY,
        _adjacent_learning_object_similarity,
    )

    if not narration or not bbox:
        return []
    found = []
    for where, passage in (
        ("below", passage_blocks_below(blocks, page_number=page_number, bbox=bbox)),
        ("above", passage_blocks_above(blocks, page_number=page_number, bbox=bbox)),
    ):
        text = passage_text(passage)
        if len(text.split()) < _MIN_PRINTED_DESCRIPTION_WORDS:
            continue
        score = _adjacent_learning_object_similarity({"content": narration}, {"content": text})
        # Logged every time so a change of vision model that drifts the
        # scores towards the threshold shows up before descriptions are missed.
        logger.info(
            "Printed-passage check: page=%s where=%s words=%s score=%.3f threshold=%.2f",
            page_number, where, len(text.split()), score, _PRINTED_DESCRIPTION_SIMILARITY,
        )
        if score >= _PRINTED_DESCRIPTION_SIMILARITY:
            found.append({"where": where, "blocks": passage, "text": text, "score": score})
    return found


def spoken_table(visible_text):
    """Read a table's own text row by row, for when no narration exists yet.

    "Property | Solid | Liquid | Gas" over "Shape | Definite | Not definite |
    Not definite" is spoken "Shape: Solid, Definite. Liquid, Not definite. Gas,
    Not definite." Without it, a table whose text left the lesson (so it is
    not read twice) said nothing at all until the model narrated it.
    """
    rows = [
        [cell.strip() for cell in line.split("|")]
        for line in (visible_text or "").splitlines()
        if line.strip()
    ]
    rows = [row for row in rows if any(row)]
    if len(rows) < 2 or len(rows[0]) < 2:
        return ". ".join(" ".join(cell for cell in row if cell) for row in rows)
    header = rows[0]
    sentences = []
    for row in rows[1:]:
        label = row[0] or "Row"
        if len(row) != len(header):
            # PDF extraction merged or lost a cell ("Not definite Not
            # definite" with the Gas column gone). Pairing values with column
            # names would now name them wrongly; say them in order instead.
            values = ", ".join(value for value in row[1:] if value)
            if values:
                sentences.append(f"{label}: {values}.")
            continue
        pairs = [
            f"{header[column] or 'Column ' + str(column + 1)}, {value}"
            for column, value in enumerate(row[1:], start=1)
            if value and column < len(header)
        ]
        if pairs:
            sentences.append(f"{label}: " + ". ".join(pairs) + ".")
    return " ".join(sentences)


def figure_pointer(caption="", title=""):
    """What a figure says when the lesson's own text around it describes it."""
    label = caption_without_links(caption) or (title or "").strip()
    lead = f"{label.rstrip('.')}. " if label else ""
    return f"{lead}This figure is described in the lesson text around it."


def _page_blocks(blocks, page_number, limit, *, tail):
    """One neighbouring page's text, from the end of it or the start."""
    def top(item):
        return float((item.get("bbox") or (0, 0, 0, 0))[1])

    rows = sorted(
        (
            item for item in blocks
            if item.get("page") == page_number and (item.get("text") or "").strip()
        ),
        key=top,
    )
    if not rows:
        return []
    # The page before a figure ends where the figure begins, so its closing
    # text is what leads into it; the page after opens with what follows.
    ordered = list(reversed(rows)) if tail else rows
    kept, used = [], 0
    for item in ordered:
        text = " ".join((item.get("text") or "").split())
        if kept and used + len(text) + 1 > limit:
            break
        kept.append(item)
        used += len(text) + 1
    kept.sort(key=top)
    return kept


def build_prompt(
    *,
    lesson_title: str = "",
    nearby_text: str = "",
    caption: str = "",
    visible_text: str = "",
    nearby_is_fallback: bool = False,
    upcoming_text: str = "",
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
    if upcoming_text:
        context_lines.append(
            "Lesson text printed immediately after the figure, which the student "
            f'is about to hear: "{upcoming_text.strip()[:_MAX_NEARBY_TEXT]}"'
        )
    if context_lines:
        heading = (
            "CONTEXT (background only — this is what the student has already "
            "been told. Use it to work out what the figure is for. Do NOT "
            "repeat, restate, summarise or paraphrase any of it back."
        )
        if nearby_text and not nearby_is_fallback:
            # A small model obeys an instruction about what to write far more
            # reliably than one about what to leave out, so the ban is paired
            # with the job it leaves behind: the lesson has the idea in words
            # already, and the visual specifics are what it cannot carry.
            heading += (
                " Where it already explains an idea in words, do not explain "
                "it again -- give the visual specifics those words leave out."
            )
        if upcoming_text:
            # The lesson teaches this concept in words moments later. A
            # narration that teaches it first makes the student hear it twice
            # and crowds out what only the figure can give them, so the figure
            # introduces what is shown and the lesson keeps the explaining.
            heading += (
                " The lesson explains that last passage immediately after this "
                "figure, so do not explain it yourself: say what is shown and "
                "leave the reason to the lesson."
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
    nearby_is_fallback: bool = False,
    upcoming_text: str = "",
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
        nearby_is_fallback=nearby_is_fallback,
        upcoming_text=upcoming_text,
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
        if _cfg("LLM_PROVIDER", "ollama") == "groq":
            text, _metrics = groq_generate(
                prompt,
                model=model,
                image_bytes=image_bytes,
                temperature=0.2,
                max_tokens=512,
                timeout=int(_cfg("IMAGE_DESCRIPTION_TIMEOUT", 300)),
            )
            text = text.strip()
        else:
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


def _extracted_captions(material) -> dict:
    """``{image_url: caption}`` as extraction recorded them for this material."""
    return {
        item.get("image_url"): item.get("caption") or ""
        for item in (material.generated_json or {}).get("image_descriptions") or []
        if item.get("image_url")
    }


def _extracted_stand_ins(material) -> dict:
    """``{image_url: stand-in}``: what a figure says until narrated (a table's rows)."""
    return {
        item.get("image_url"): item.get("stand_in") or ""
        for item in (material.generated_json or {}).get("image_descriptions") or []
        if item.get("image_url") and item.get("stand_in")
    }


def _still_the_extracted_caption(learning_object, captions, stand_ins=None) -> bool:
    """True when the narration is exactly what extraction fell back to.

    Compared with the caption (or, for a table, the row-by-row reading)
    recorded for this very image, not judged by its shape: a teacher's own
    short narration that happens to open "Figure 2." is theirs and must never
    be overwritten.
    """
    content = learning_object.content or ""
    stand_in = (stand_ins or {}).get(learning_object.image_url, "")
    if stand_in and " ".join(content.split()) == " ".join(stand_in.split()):
        return True
    caption = captions.get(learning_object.image_url, "")
    return bool(caption.strip()) and caption_without_links(content) == caption_without_links(caption)


def narration_pending(learning_object) -> bool:
    """True for a figure with no narration yet: blank, or still only its caption.

    The captions are read once per material and kept on it, so a page listing
    every figure does not re-read the material's record for each one.
    """
    if getattr(learning_object, "kind", "") != "image":
        return False
    material = learning_object.material
    captions = getattr(material, "_extracted_captions_cache", None)
    if captions is None:
        captions = _extracted_captions(material)
        material._extracted_captions_cache = captions
    stand_ins = getattr(material, "_extracted_stand_ins_cache", None)
    if stand_ins is None:
        stand_ins = _extracted_stand_ins(material)
        material._extracted_stand_ins_cache = stand_ins
    return not (learning_object.content or "").strip() or _still_the_extracted_caption(
        learning_object, captions, stand_ins,
    )


def figure_narration_status(material) -> dict:
    """``{"figures": n, "pending": m}``: figures, and those still without narration."""
    from lessons.models import LearningObject

    captions = _extracted_captions(material)
    stand_ins = _extracted_stand_ins(material)
    figures = [
        item for item in material.learning_objects.all()
        if item.kind == LearningObject.Kind.IMAGE
    ]
    pending = [
        item for item in figures
        if not (item.content or "").strip() or _still_the_extracted_caption(item, captions, stand_ins)
    ]
    return {"figures": len(figures), "pending": len(pending)}


def _printed_passages_for(material, image_url, narration):
    """Printed paragraphs beside this figure that its new narration repeats."""
    generated = material.generated_json or {}
    record = next(
        (
            item for item in generated.get("image_descriptions") or []
            if image_url and item.get("image_url") == image_url
        ),
        None,
    )
    if not record or not record.get("bbox"):
        return []
    return redundant_printed_passages(
        generated.get("classified_blocks") or [],
        page_number=record.get("page_number") or record.get("page"),
        bbox=record.get("bbox"),
        narration=narration,
    )


def _passage_still_in_lesson(material, passages):
    """The first passage whose text the lesson still says, or ``None``.

    A teacher may have rewritten or removed that paragraph since upload. Then
    the figure is the only place the explanation is left, and it keeps the
    model's narration rather than pointing at text that is gone.
    """
    from lessons.models import LearningObject

    if not passages:
        return None
    lesson_text = " ".join(
        " ".join((content or "").split()).casefold()
        for content in material.learning_objects.filter(
            kind=LearningObject.Kind.TEXT,
        ).values_list("content", flat=True)
    )
    for passage in passages:
        # The opening of the paragraph is enough to find it, and survives a
        # teacher fixing a typo further down.
        opening = " ".join(passage["text"].split()[:12]).casefold()
        if opening and opening in lesson_text:
            return passage
    return None


def populate_missing_image_descriptions(material) -> dict:
    """Retry image narrations that are blank or still only the printed caption."""
    from lessons.models import LearningObject

    captions = _extracted_captions(material)
    stand_ins = _extracted_stand_ins(material)
    images = [item for item in material.learning_objects.filter(
        kind=LearningObject.Kind.IMAGE,
    ).order_by("order", "id")
        if not (item.content or "").strip() or _still_the_extracted_caption(item, captions, stand_ins)]
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
            # The caption the figure was printed with, when that is all it has;
            # its title alone ("Figure 3") tells the model nothing.
            caption=(
                caption_without_links(captions.get(learning_object.image_url, ""))
                or learning_object.title
            ),
        )
        if not description:
            errors.append({
                "learning_object_id": learning_object.id,
                "detail": (
                    "Groq did not return an image narration. Check the API connection and retry."
                    if _cfg("LLM_PROVIDER", "ollama") == "groq"
                    else "Gemma did not return an image narration. Confirm Ollama is running and retry."
                ),
            })
            continue
        # The same check upload makes: when the author's own paragraph beside
        # the figure already explains it, the learner hears that paragraph, and
        # the figure only announces itself instead of explaining it again.
        explained_by = _passage_still_in_lesson(
            material,
            _printed_passages_for(material, learning_object.image_url, description),
        )
        if explained_by:
            description = figure_pointer(
                captions.get(learning_object.image_url, ""), learning_object.title,
            )
            logger.info(
                "Image narration dropped: material=%s learning_object=%s repeats the %s passage (score %.3f)",
                material.id, learning_object.id, explained_by["where"], explained_by["score"],
            )
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
