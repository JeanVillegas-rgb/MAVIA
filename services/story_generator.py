from __future__ import annotations

import re
from dataclasses import dataclass

from django.conf import settings

from lessons.models import Lesson, LessonPage


@dataclass
class StoryChapter:
    order: int
    title: str
    narrative: str


OPENING_TEMPLATES = [
    "Welcome, curious explorer. Tonight's science story begins with {title}.",
    "Settle in and imagine we're opening a book of wonders: {title}.",
    "Picture a quiet laboratory at dusk. Our journey into {title} is about to begin.",
]

TRANSITIONS = [
    "As the story unfolds,",
    "Moving deeper into our adventure,",
    "The narrative turns a new page, and",
    "Listen closely now, because",
]

IMAGE_BRIDGE = [
    "Pause for a moment and picture this:",
    "In your mind's eye, see this scene:",
    "The lesson shows us something remarkable:",
]


def _split_into_sections(text: str, max_chars: int = 900) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        return []

    sections: list[str] = []
    current = ""

    for paragraph in paragraphs:
        if len(current) + len(paragraph) + 2 <= max_chars:
            current = f"{current}\n\n{paragraph}".strip() if current else paragraph
        else:
            if current:
                sections.append(current)
            if len(paragraph) <= max_chars:
                current = paragraph
            else:
                sentences = re.split(r"(?<=[.!?])\s+", paragraph)
                chunk = ""
                for sentence in sentences:
                    if len(chunk) + len(sentence) + 1 <= max_chars:
                        chunk = f"{chunk} {sentence}".strip()
                    else:
                        if chunk:
                            sections.append(chunk)
                        chunk = sentence
                current = chunk

    if current:
        sections.append(current)

    return sections


def _infer_chapter_title(section_text: str, index: int) -> str:
    first_line = section_text.splitlines()[0].strip()
    if len(first_line) <= 70 and first_line.endswith((":", "?", ".")):
        return first_line.rstrip(".:")
    words = re.findall(r"[A-Za-z0-9]+", first_line)
    snippet = " ".join(words[:8])
    if snippet:
        return snippet
    return f"Chapter {index}"


def _openai_story_chapters(lesson: Lesson, pages: list[LessonPage]) -> list[StoryChapter] | None:
    if not settings.OPENAI_API_KEY:
        return None

    try:
        from openai import OpenAI
    except ImportError:
        return None

    chunks = []
    for page in pages:
        image_notes = [
            f"[Visual: {img.interpretation}]"
            for img in page.images.all()
            if img.interpretation
        ]
        chunk = f"Page {page.page_number}:\n{page.raw_text}"
        if image_notes:
            chunk += "\n" + "\n".join(image_notes)
        chunks.append(chunk)

    source_material = "\n\n".join(chunks)[:12000]
    client = OpenAI(api_key=settings.OPENAI_API_KEY)

    prompt = (
        f"Transform this science lesson titled '{lesson.title}' into a story-like audiobook script. "
        "Write 3-8 chapters with engaging narrator language suitable for listening. "
        "Weave image descriptions naturally into the narrative. "
        "Use clear section headers like 'Chapter 1: ...'. "
        "Keep facts accurate but make it feel like a guided adventure.\n\n"
        f"{source_material}"
    )

    response = client.chat.completions.create(
        model=settings.OPENAI_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You write educational audiobook scripts for middle-school science learners."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        max_tokens=3500,
    )

    script = response.choices[0].message.content.strip()
    return _parse_chapters_from_script(script)


def _parse_chapters_from_script(script: str) -> list[StoryChapter]:
    pattern = re.compile(
        r"(?:^|\n)(?:Chapter\s+(\d+)\s*[:\-.]\s*(.+?)|#{1,3}\s*(.+?))\n([\s\S]*?)(?=\n(?:Chapter|\#)|\Z)",
        re.IGNORECASE,
    )
    chapters: list[StoryChapter] = []
    for match in pattern.finditer(script):
        order = int(match.group(1) or len(chapters) + 1)
        title = (match.group(2) or match.group(3) or f"Chapter {order}").strip()
        body = match.group(4).strip()
        if body:
            chapters.append(StoryChapter(order=order, title=title, narrative=body))

    if chapters:
        return chapters

    sections = _split_into_sections(script, max_chars=1100)
    return [
        StoryChapter(
            order=index,
            title=_infer_chapter_title(section, index),
            narrative=section,
        )
        for index, section in enumerate(sections, start=1)
    ]


def _template_story_chapters(lesson: Lesson, pages: list[LessonPage]) -> list[StoryChapter]:
    sections: list[str] = []
    opening = OPENING_TEMPLATES[0].format(title=lesson.title)
    sections.append(opening)

    for page in pages:
        if not page.raw_text and not page.images.exists():
            continue

        transition = TRANSITIONS[len(sections) % len(TRANSITIONS)]
        page_section = f"{transition} on page {page.page_number} of our science tale."

        if page.raw_text:
            page_section += f"\n\n{page.raw_text}"

        for image in page.images.all():
            bridge = IMAGE_BRIDGE[image.order % len(IMAGE_BRIDGE)]
            visual = image.interpretation or image.caption or "a supporting illustration"
            page_section += f"\n\n{bridge} {visual}"

        sections.extend(_split_into_sections(page_section, max_chars=850))

    closing = (
        "And so our science story draws to a close for now. "
        "Carry these ideas with you, and keep wondering about the world around you."
    )
    sections.append(closing)

    return [
        StoryChapter(
            order=index,
            title=_infer_chapter_title(section, index),
            narrative=section,
        )
        for index, section in enumerate(sections, start=1)
        if section.strip()
    ]


def generate_story_chapters(lesson: Lesson, pages: list[LessonPage]) -> list[StoryChapter]:
    chapters = _openai_story_chapters(lesson, pages)
    if not chapters:
        chapters = _template_story_chapters(lesson, pages)

    if chapters:
        lesson.story_intro = chapters[0].narrative.split("\n\n")[0][:500]
        lesson.save(update_fields=["story_intro"])

    return chapters
