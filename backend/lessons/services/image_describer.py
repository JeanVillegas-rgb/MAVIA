"""Explain an extracted figure to a blind / low-vision learner.

Given the raw bytes of a figure pulled out of a teacher's PDF, ask a local
vision model (Ollama) what *concept* the figure teaches — not where things
sit on the page, but the science it is there to show.

This is purely additive. If Ollama isn't running, the model isn't a vision
model, it times out, or it can't make sense of the figure, every entry point
returns "" and the caller keeps its existing fallback (the figure's caption
or the text visible inside it). Nothing in the pipeline breaks when the
model is absent.

Point it at a different Ollama vision model with IMAGE_DESCRIPTION_MODEL
(llava, moondream, qwen2-vl, llama3.2-vision …), or turn it off entirely
with IMAGE_DESCRIPTION_ENABLED=False.
"""

from __future__ import annotations

import base64
import logging
from functools import lru_cache

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

_SKIP = "SKIP"
_MAX_VISIBLE_TEXT = 400
_MAX_NEARBY_TEXT = 600


def _cfg(name: str, default):
    return getattr(settings, name, default)


def _base_url() -> str:
    return _cfg("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")


@lru_cache(maxsize=1)
def _service_reachable() -> bool:
    """One cheap check per process — don't hammer a dead endpoint once per
    figure. Tests call reset_reachability_cache() between cases."""
    try:
        response = requests.get(f"{_base_url()}/api/tags", timeout=2)
        return response.status_code == 200
    except requests.RequestException:
        return False


def reset_reachability_cache() -> None:
    _service_reachable.cache_clear()


def build_prompt(
    *,
    lesson_title: str = "",
    nearby_text: str = "",
    caption: str = "",
    visible_text: str = "",
) -> str:
    parts = [
        "You are writing spoken audio description of a figure for a blind "
        "student who is following a science lesson by listening.",
    ]
    if lesson_title:
        parts.append(f'The lesson is titled "{lesson_title}".')
    if caption:
        parts.append(f'The figure caption reads: "{caption.strip()}".')
    if visible_text:
        parts.append(
            f'Text printed inside the figure: "{visible_text.strip()[:_MAX_VISIBLE_TEXT]}".'
        )
    if nearby_text:
        parts.append(
            f'Lesson text near the figure: "{nearby_text.strip()[:_MAX_NEARBY_TEXT]}".'
        )
    parts.append(
        "Explain what this figure TEACHES — the concept, process, structure, "
        "relationship, cause and effect, or fact it exists to show. Do NOT "
        "describe the visual layout: no colours, arrows, shapes, positions, "
        "or labels like \"diagram\", \"chart\", \"graph\", or \"photo\". Give "
        "the student the understanding a sighted classmate would take from "
        "looking at it. Write 2 to 4 plain sentences meant to be heard aloud. "
        f'If the figure is decorative, a logo, or too unclear to explain, '
        f'reply with exactly "{_SKIP}" and nothing else.'
    )
    return " ".join(parts)


def _looks_like_skip(text: str) -> bool:
    return text.strip().upper().strip(".!\"' ") == _SKIP


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
    if not _service_reachable():
        return ""

    payload = {
        "model": _cfg("IMAGE_DESCRIPTION_MODEL", "gemma3:4b"),
        "prompt": build_prompt(
            lesson_title=lesson_title,
            nearby_text=nearby_text,
            caption=caption,
            visible_text=visible_text,
        ),
        "images": [base64.b64encode(image_bytes).decode("ascii")],
        "stream": False,
        "options": {"temperature": 0.2},
    }
    try:
        response = requests.post(
            f"{_base_url()}/api/generate",
            json=payload,
            timeout=int(_cfg("IMAGE_DESCRIPTION_TIMEOUT", 120)),
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

    if not text or _looks_like_skip(text):
        return ""
    return text
