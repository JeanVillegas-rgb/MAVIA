from __future__ import annotations

import logging
import threading

from django.db import close_old_connections
from django.utils import timezone

from lessons.models import Lesson, OutlineNode
from lessons.services.audio_generator import create_script_modules, synthesize_audio_for_lesson
from lessons.services.image_interpreter import interpret_page_images
from lessons.services.pdf_processor import extract_pdf_content, persist_extracted_pages
from lessons.services.story_generator import generate_story_chapters

logger = logging.getLogger(__name__)


def _set_progress(lesson_id: int, progress: int) -> None:
    Lesson.objects.filter(id=lesson_id).update(progress=progress)


def _sync_node_status(lesson: Lesson, node_status: str) -> None:
    if lesson.outline_node_id:
        OutlineNode.objects.filter(id=lesson.outline_node_id).update(status=node_status)


def process_lesson_script(lesson_id: int) -> None:
    lesson = Lesson.objects.select_related("outline_node", "course").get(id=lesson_id)
    lesson.status = Lesson.Status.PROCESSING
    lesson.progress = 5
    lesson.error_message = ""
    lesson.script_approved_at = None
    lesson.published_at = None
    lesson.save(
        update_fields=[
            "status",
            "progress",
            "error_message",
            "script_approved_at",
            "published_at",
        ]
    )
    _sync_node_status(lesson, OutlineNode.NodeStatus.IN_PROGRESS)

    try:
        lesson.pages.all().delete()
        lesson.audio_modules.all().delete()

        _set_progress(lesson_id, 15)
        extracted = extract_pdf_content(lesson.pdf_file.path)
        pages = persist_extracted_pages(lesson, extracted)

        _set_progress(lesson_id, 45)
        interpret_page_images(pages)

        _set_progress(lesson_id, 70)
        chapters = generate_story_chapters(lesson, pages)
        create_script_modules(lesson.id, chapters)

        lesson.status = Lesson.Status.SCRIPT_REVIEW
        lesson.progress = 100
        lesson.save(update_fields=["status", "progress"])
        _sync_node_status(lesson, OutlineNode.NodeStatus.SCRIPT_REVIEW)
    except Exception as exc:
        logger.exception("Failed to generate script for lesson %s", lesson_id)
        lesson.status = Lesson.Status.FAILED
        lesson.error_message = str(exc)
        lesson.save(update_fields=["status", "error_message"])
        _sync_node_status(lesson, OutlineNode.NodeStatus.EMPTY)


def process_lesson_audio(lesson_id: int) -> None:
    lesson = Lesson.objects.select_related("outline_node").get(id=lesson_id)
    lesson.status = Lesson.Status.AUDIO_GENERATING
    lesson.progress = 10
    lesson.error_message = ""
    lesson.save(update_fields=["status", "progress", "error_message"])

    try:
        _set_progress(lesson_id, 40)
        synthesize_audio_for_lesson(lesson_id)

        lesson.status = Lesson.Status.AUDIO_REVIEW
        lesson.progress = 100
        lesson.save(update_fields=["status", "progress"])
        _sync_node_status(lesson, OutlineNode.NodeStatus.AUDIO_REVIEW)
    except Exception as exc:
        logger.exception("Failed to generate audio for lesson %s", lesson_id)
        lesson.status = Lesson.Status.FAILED
        lesson.error_message = str(exc)
        lesson.save(update_fields=["status", "error_message"])


def start_script_processing(lesson_id: int) -> None:
    def runner() -> None:
        close_old_connections()
        try:
            process_lesson_script(lesson_id)
        finally:
            close_old_connections()

    threading.Thread(target=runner, daemon=True).start()


def start_audio_processing(lesson_id: int) -> None:
    def runner() -> None:
        close_old_connections()
        try:
            process_lesson_audio(lesson_id)
        finally:
            close_old_connections()

    threading.Thread(target=runner, daemon=True).start()


def approve_script(lesson: Lesson) -> None:
    lesson.status = Lesson.Status.SCRIPT_APPROVED
    lesson.script_approved_at = timezone.now()
    lesson.save(update_fields=["status", "script_approved_at"])
    start_audio_processing(lesson.id)


def publish_lesson(lesson: Lesson) -> None:
    lesson.status = Lesson.Status.PUBLISHED
    lesson.published_at = timezone.now()
    lesson.save(update_fields=["status", "published_at"])
    _sync_node_status(lesson, OutlineNode.NodeStatus.PUBLISHED)
