from __future__ import annotations

import base64
import io

from django.conf import settings
from PIL import Image

from lessons.models import LessonPage, PageImage


def _fallback_interpretation(page_image: PageImage) -> str:
    caption = page_image.caption.strip()
    if caption:
        return (
            f"The illustration is labeled \"{caption}\". "
            "It supports the science lesson on this page with a visual example."
        )
    return (
        "A science diagram or illustration appears on this page, "
        "helping visualize the concept being taught."
    )


def _openai_interpret(page_image: PageImage) -> str | None:
    if not settings.OPENAI_API_KEY:
        return None

    try:
        from openai import OpenAI
    except ImportError:
        return None

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    page_text = page_image.page.raw_text[:1200]

    with page_image.image.open("rb") as handle:
        image_bytes = handle.read()

    with Image.open(io.BytesIO(image_bytes)) as img:
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=85)
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")

    caption_hint = page_image.caption or "No caption detected"
    prompt = (
        "You are narrating a science lesson for an audiobook. "
        "Describe this image in 2-3 vivid, story-friendly sentences for a listener. "
        "Connect it to the lesson context when possible. "
        f"Nearby caption: {caption_hint}. "
        f"Page text excerpt: {page_text}"
    )

    response = client.chat.completions.create(
        model=settings.OPENAI_VISION_MODEL,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
                    },
                ],
            }
        ],
        max_tokens=220,
    )
    return response.choices[0].message.content.strip()


def interpret_page_images(pages: list[LessonPage]) -> None:
    for page in pages:
        for page_image in page.images.all():
            interpretation = _openai_interpret(page_image)
            if not interpretation:
                interpretation = _fallback_interpretation(page_image)
            page_image.interpretation = interpretation
            page_image.save(update_fields=["interpretation"])
