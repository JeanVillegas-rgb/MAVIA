import asyncio
import hashlib
import os
import subprocess
from pathlib import Path

from django.conf import settings

from lessons.models import LearningMaterial


class AudioGenerationError(RuntimeError):
    pass


def _powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _playlist_text_by_order(material: LearningMaterial) -> dict[int, str]:
    generated_json = material.generated_json or {}
    return {
        int(item.get("order")): (item.get("content") or "").strip()
        for item in generated_json.get("narration_script", [])
        if item.get("order") is not None and (item.get("content") or "").strip()
    }


def _synthesize_text_to_mp3_with_edge(text: str, output_path: Path, timeout: int = 180) -> None:
    text = (text or "").strip()
    if not text:
        raise AudioGenerationError("Playlist item has no narration text.")

    try:
        import edge_tts
    except ImportError as exc:
        raise AudioGenerationError("edge-tts is not installed.") from exc

    output_path.parent.mkdir(parents=True, exist_ok=True)
    voice = os.getenv("EDGE_TTS_VOICE", "en-US-AriaNeural")

    async def _save():
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(str(output_path))

    try:
        asyncio.run(asyncio.wait_for(_save(), timeout=timeout))
    except Exception as exc:
        raise AudioGenerationError(f"Edge TTS failed: {exc}") from exc

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise AudioGenerationError("Edge TTS did not create an audio file.")


def _synthesize_text_to_wav_with_windows(text: str, output_path: Path, timeout: int = 120) -> None:
    text = (text or "").strip()
    if not text:
        raise AudioGenerationError("Playlist item has no narration text.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    text_path = output_path.with_suffix(".txt")
    text_path.write_text(text, encoding="utf-8")

    command = (
        "Add-Type -AssemblyName System.Speech; "
        f"$text = Get-Content -LiteralPath {_powershell_quote(str(text_path))} -Raw; "
        "$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$speaker.SetOutputToWaveFile({_powershell_quote(str(output_path))}); "
        "$speaker.Speak($text); "
        "$speaker.Dispose();"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    finally:
        text_path.unlink(missing_ok=True)

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "Windows speech synthesis failed.").strip()
        raise AudioGenerationError(detail)
    if not output_path.exists() or output_path.stat().st_size == 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        suffix = f" Details: {detail}" if detail else ""
        raise AudioGenerationError(f"Windows speech synthesis did not create an audio file.{suffix}")


def synthesize_text_to_audio(text: str, output_path_without_suffix: Path) -> Path:
    provider = os.getenv("AUDIO_TTS_PROVIDER", "edge").strip().lower()
    errors = []

    if provider in {"edge", "edge-tts", "auto"}:
        mp3_path = output_path_without_suffix.with_suffix(".mp3")
        try:
            _synthesize_text_to_mp3_with_edge(text, mp3_path)
            return mp3_path
        except AudioGenerationError as exc:
            errors.append(str(exc))
            if provider in {"edge", "edge-tts"}:
                raise

    wav_path = output_path_without_suffix.with_suffix(".wav")
    try:
        _synthesize_text_to_wav_with_windows(text, wav_path)
        return wav_path
    except AudioGenerationError as exc:
        errors.append(str(exc))
        raise AudioGenerationError("Audio generation failed. " + " | ".join(errors)) from exc


def synthesize_text_to_wav(text: str, output_path: Path, timeout: int = 120) -> None:
    _synthesize_text_to_wav_with_windows(text, output_path, timeout=timeout)


def _audio_file_exists(relative_path: str) -> bool:
    if not relative_path:
        return False
    path = Path(settings.MEDIA_ROOT) / relative_path
    return path.exists() and path.is_file() and path.stat().st_size > 0


def remove_missing_audio_urls(material: LearningMaterial, save: bool = False) -> dict:
    generated_json = material.generated_json or {}
    playlist = generated_json.get("lesson_playlist") or []
    if not playlist:
        return generated_json

    changed = False
    cleaned_playlist = []
    for item in playlist:
        updated_item = dict(item)
        audio_file = updated_item.get("audio_file") or ""
        audio_url = updated_item.get("audio_url") or ""
        if not audio_file and audio_url.startswith(settings.MEDIA_URL):
            audio_file = audio_url.removeprefix(settings.MEDIA_URL)

        if audio_url and not _audio_file_exists(audio_file):
            updated_item.pop("audio_url", None)
            updated_item.pop("audio_file", None)
            updated_item["audio_status"] = "missing"
            changed = True
        cleaned_playlist.append(updated_item)

    if changed:
        generated_json = {
            **generated_json,
            "lesson_playlist": cleaned_playlist,
            "lesson_audio_generated": False,
            "audio_playlist_generated": False,
        }
        if save:
            material.generated_json = generated_json
            material.save(update_fields=["generated_json"])
    return generated_json


def mark_material_audio_stale(material: LearningMaterial, scope: str = "all") -> None:
    generated_json = material.generated_json or {}
    if not generated_json:
        return
    generated_json["lesson_audio_generated"] = False
    generated_json["audio_playlist_generated"] = False
    material.generated_json = generated_json
    material.save(update_fields=["generated_json"])


def generate_material_audio_playlist(material: LearningMaterial, scope: str = "lessons") -> dict:
    generated_json = material.generated_json or {}
    lesson_playlist = generated_json.get("lesson_playlist") or []
    if not lesson_playlist:
        raise AudioGenerationError("This material has no lesson playlist to synthesize.")

    narration_by_order = _playlist_text_by_order(material)
    audio_dir = Path(settings.MEDIA_ROOT) / "audio_lessons" / f"material_{material.id}"
    updated_playlist = []
    generated_count = 0

    for item in lesson_playlist:
        updated_item = dict(item)
        narration_order = updated_item.get("narration_item_order")
        if narration_order is None:
            # Not sourced from this material's narration script (e.g. an
            # existing practice-question audio track) — keep it untouched
            # instead of dropping it from the playlist.
            updated_playlist.append(updated_item)
            continue
        text = narration_by_order.get(int(narration_order)) or ""
        if not text:
            continue
        position = len(updated_playlist)
        audio_path, created = cached_audio(text, audio_dir)
        relative_path = audio_path.relative_to(settings.MEDIA_ROOT).as_posix()
        updated_item["order"] = position
        updated_item["audio_status"] = "generated"
        updated_item["audio_url"] = f"{settings.MEDIA_URL}{relative_path}"
        updated_item["audio_file"] = relative_path
        updated_playlist.append(updated_item)
        generated_count += int(created)

    if not updated_playlist:
        raise AudioGenerationError(
            "No narration text is available. Add lesson text or a teacher image description first."
        )

    generated_json["lesson_playlist"] = updated_playlist
    generated_json["lesson_audio_generated"] = True
    generated_json["audio_playlist_generated"] = True
    material.generated_json = generated_json
    material.save(update_fields=["generated_json"])

    return {
        "generated_count": generated_count,
        "lesson_playlist": updated_playlist,
        "scope": "lessons",
    }


def cached_audio(text, directory):
    """Reuse audio only for the same narration and configured voice/provider."""
    provider = os.getenv("AUDIO_TTS_PROVIDER", "edge").strip().lower()
    voice = os.getenv("EDGE_TTS_VOICE", "en-US-AriaNeural")
    digest = hashlib.sha256(f"{provider}|{voice}|{text}".encode()).hexdigest()
    base = directory / digest
    suffixes = (".mp3",) if provider in {"edge", "edge-tts"} else (".mp3", ".wav") if provider == "auto" else (".wav",)
    for suffix in suffixes:
        candidate = base.with_suffix(suffix)
        if candidate.is_file() and candidate.stat().st_size:
            return candidate, False
    return synthesize_text_to_audio(text, base), True


def generate_version_audio(material):
    """Prepare audio for the two active source/generated versions; omit Extras."""
    from course.models import LessonVariant
    generated = 0
    for row in LessonVariant.objects.filter(
        learning_object__material=material,
        learning_object__represented_by__isnull=True,
        variant__in=("SIMPLIFIED", "ELABORATED"),
    ):
        if not row.narration.strip():
            raise AudioGenerationError(f"{row.variant.title()} version {row.id} has no narration.")
        path, created = cached_audio(row.narration, Path(settings.MEDIA_ROOT) / "audio_versions")
        url = f"{settings.MEDIA_URL}{path.relative_to(settings.MEDIA_ROOT).as_posix()}"
        LessonVariant.objects.filter(pk=row.pk, narration=row.narration).update(audio_url=url)
        generated += int(created)
    return {"generated_count": generated}
