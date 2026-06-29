from __future__ import annotations

import asyncio
import subprocess
import tempfile
from pathlib import Path

import edge_tts
from django.conf import settings
from django.core.files.base import ContentFile

from lessons.models import AudioModule
from lessons.services.story_generator import StoryChapter


async def _synthesize_to_file(text: str, output_path: Path) -> None:
    communicate = edge_tts.Communicate(
        text=text,
        voice=settings.TTS_VOICE,
        rate=settings.TTS_RATE,
    )
    await communicate.save(str(output_path))


def _estimate_duration_seconds(text: str) -> float:
    words = len(text.split())
    return max(words / 2.5, 1.0)


def _probe_duration_seconds(audio_path: Path, fallback_text: str) -> float:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(audio_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return float(result.stdout.strip())
    except (FileNotFoundError, subprocess.CalledProcessError, ValueError):
        return _estimate_duration_seconds(fallback_text)


def create_script_modules(lesson_id: int, chapters: list[StoryChapter]) -> list[AudioModule]:
    modules: list[AudioModule] = []
    for chapter in chapters:
        module = AudioModule.objects.create(
            lesson_id=lesson_id,
            order=chapter.order,
            title=chapter.title,
            narrative_text=chapter.narrative,
        )
        modules.append(module)
    return modules


def synthesize_audio_for_lesson(lesson_id: int) -> list[AudioModule]:
    modules = list(AudioModule.objects.filter(lesson_id=lesson_id).order_by("order"))
    for module in modules:
        if module.audio_file:
            module.audio_file.delete(save=False)

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir) / f"module_{module.order}.mp3"
            asyncio.run(_synthesize_to_file(module.narrative_text, temp_path))

            duration = _probe_duration_seconds(temp_path, module.narrative_text)
            filename = f"lesson_{lesson_id}_module_{module.order}.mp3"
            with temp_path.open("rb") as handle:
                module.audio_file.save(filename, ContentFile(handle.read()), save=False)

            module.duration_seconds = duration
            module.save()

    return modules
