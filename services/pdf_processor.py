from __future__ import annotations

import io
from dataclasses import dataclass

import fitz
from django.core.files.base import ContentFile
from PIL import Image

from lessons.models import Lesson, LessonPage, PageImage


@dataclass
class ExtractedImage:
    page_number: int
    order: int
    image_bytes: bytes
    extension: str
    caption: str


@dataclass
class ExtractedPage:
    page_number: int
    text: str
    images: list[ExtractedImage]


def _clean_text(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    cleaned = []
    for line in lines:
        if not line:
            if cleaned and cleaned[-1] != "":
                cleaned.append("")
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip()


def _guess_caption(page: fitz.Page, image_rect: fitz.Rect) -> str:
    blocks = page.get_text("blocks")
    candidates = []
    for block in blocks:
        if len(block) < 5:
            continue
        x0, y0, x1, y1, text = block[:5]
        text = str(text).strip()
        if not text or len(text) > 180:
            continue
        below = y0 >= image_rect.y1 - 8
        above = y1 <= image_rect.y0 + 8
        if below or above:
            candidates.append(text)
    return candidates[0] if candidates else ""


def extract_pdf_content(pdf_path: str) -> list[ExtractedPage]:
    document = fitz.open(pdf_path)
    pages: list[ExtractedPage] = []

    for index in range(len(document)):
        page = document[index]
        page_number = index + 1
        text = _clean_text(page.get_text("text"))
        images: list[ExtractedImage] = []

        for image_index, image_info in enumerate(page.get_images(full=True)):
            xref = image_info[0]
            try:
                base_image = document.extract_image(xref)
            except Exception:
                continue

            image_bytes = base_image["image"]
            extension = base_image.get("ext", "png")
            if extension == "jpeg":
                extension = "jpg"

            if len(image_bytes) < 2048:
                continue

            try:
                with Image.open(io.BytesIO(image_bytes)) as img:
                    if img.width < 80 or img.height < 80:
                        continue
            except Exception:
                continue

            rects = page.get_image_rects(xref)
            caption = ""
            if rects:
                caption = _guess_caption(page, rects[0])

            images.append(
                ExtractedImage(
                    page_number=page_number,
                    order=image_index,
                    image_bytes=image_bytes,
                    extension=extension,
                    caption=caption,
                )
            )

        pages.append(
            ExtractedPage(
                page_number=page_number,
                text=text,
                images=images,
            )
        )

    document.close()
    return pages


def persist_extracted_pages(lesson: Lesson, pages: list[ExtractedPage]) -> list[LessonPage]:
    saved_pages: list[LessonPage] = []

    for page_data in pages:
        lesson_page = LessonPage.objects.create(
            lesson=lesson,
            page_number=page_data.page_number,
            raw_text=page_data.text,
            image_count=len(page_data.images),
        )

        for image_data in page_data.images:
            filename = (
                f"lesson_{lesson.id}_page_{page_data.page_number}_"
                f"img_{image_data.order}.{image_data.extension}"
            )
            page_image = PageImage(
                page=lesson_page,
                order=image_data.order,
                caption=image_data.caption,
            )
            page_image.image.save(
                filename,
                ContentFile(image_data.image_bytes),
                save=True,
            )

        saved_pages.append(lesson_page)

    return saved_pages
