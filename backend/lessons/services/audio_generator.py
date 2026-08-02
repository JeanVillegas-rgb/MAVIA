import asyncio
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
    narration_by_order = {
        int(item.get("order")): item.get("content", "")
        for item in generated_json.get("narration_script", [])
        if item.get("order") is not None
    }
    return narration_by_order


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


_DIFFICULTY_ORDER = {"easy": 0, "medium": 1, "hard": 2}


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
    has_missing_lesson_audio = False
    has_missing_question_audio = False
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
            if updated_item.get("type") == "practice_question":
                has_missing_question_audio = True
            else:
                has_missing_lesson_audio = True
        cleaned_playlist.append(updated_item)

    if changed:
        generated_json = {**generated_json, "lesson_playlist": cleaned_playlist}
        if has_missing_lesson_audio:
            generated_json["lesson_audio_generated"] = False
        if has_missing_question_audio:
            generated_json["question_audio_generated"] = False
        generated_json["audio_playlist_generated"] = False
        if save:
            material.generated_json = generated_json
            material.save(update_fields=["generated_json"])
    return generated_json


def mark_material_audio_stale(material: LearningMaterial, scope: str = "all") -> None:
    generated_json = material.generated_json or {}
    if not generated_json:
        return
    if scope in {"all", "lessons"}:
        generated_json["lesson_audio_generated"] = False
    if scope in {"all", "questions"}:
        generated_json["question_audio_generated"] = False
    generated_json["audio_playlist_generated"] = False
    material.generated_json = generated_json
    material.save(update_fields=["generated_json"])


def _question_narration_text(index: int, question) -> str:
    """Read out one practice question (choices included, answer withheld)."""
    if question.question_format == "TF":
        return f"Question {index}. True or False. {question.question_text}"
    lines = [f"Question {index}. {question.question_text}"]
    for letter, text in (question.choices or {}).items():
        lines.append(f"{letter}. {text}")
    return "\n".join(lines)


def generate_material_audio_playlist(material: LearningMaterial) -> dict:
    generated_json = material.generated_json or {}
    # question tracks are rebuilt from the DB each run — drop stored ones
    playlist = [
        item for item in (generated_json.get("lesson_playlist") or [])
        if item.get("type") not in {"practice_questions", "practice_question"}
    ]
    if not playlist:
        raise AudioGenerationError("This material has no lesson playlist to synthesize.")

    narration_by_order = _playlist_text_by_order(material)
    # playlist items and learning objects share the confirmed snapshot order,
    # so they line up by position
    nodes = list(material.learning_objects.all().order_by("order", "id"))
    audio_dir = Path(settings.MEDIA_ROOT) / "audio_lessons" / f"material_{material.id}"
    updated_playlist = []
    generated_count = 0

    for index, item in enumerate(playlist):
        updated_item = dict(item)
        narration_order = updated_item.get("narration_item_order")
        text = narration_by_order.get(int(narration_order)) if narration_order is not None else ""
        position = len(updated_playlist)
        audio_path_without_suffix = audio_dir / f"playlist_item_{position + 1}"

        audio_path = synthesize_text_to_audio(text, audio_path_without_suffix)
        relative_path = audio_path.relative_to(settings.MEDIA_ROOT).as_posix()
        updated_item["order"] = position
        updated_item["audio_status"] = "generated"
        updated_item["audio_url"] = f"{settings.MEDIA_URL}{relative_path}"
        updated_item["audio_file"] = relative_path
        updated_playlist.append(updated_item)
        generated_count += 1

        node = nodes[index] if index < len(nodes) else None
        if node is None:
            continue
        questions = sorted(
            node.generated_questions.all(),
            key=lambda q: (_DIFFICULTY_ORDER.get(q.difficulty, 3), q.id),
        )
        for question_index, question in enumerate(questions, start=1):
            question_path = synthesize_text_to_audio(
                _question_narration_text(question_index, question),
                audio_dir / f"question_{question.id}",
            )
            question_relative = question_path.relative_to(settings.MEDIA_ROOT).as_posix()
            updated_playlist.append(
                {
                    "order": len(updated_playlist),
                    "title": f"Question {question_index} ({question.difficulty}) — {node.title}",
                    "type": "practice_question",
                    "node_id": node.id,
                    "question_id": question.id,
                    "audio_status": "generated",
                    "audio_url": f"{settings.MEDIA_URL}{question_relative}",
                    "audio_file": question_relative,
                }
            )
            generated_count += 1

    generated_json["lesson_playlist"] = updated_playlist
    generated_json["audio_playlist_generated"] = True
    material.generated_json = generated_json
    material.save(update_fields=["generated_json"])

    return {
        "generated_count": generated_count,
        "lesson_playlist": updated_playlist,
    }


def _has_questions(material: LearningMaterial) -> bool:
    return any(node.generated_questions.exists() for node in material.learning_objects.all())


def _sync_audio_flags(generated_json: dict, material: LearningMaterial) -> None:
    lesson_ready = bool(generated_json.get("lesson_audio_generated"))
    question_ready = bool(generated_json.get("question_audio_generated"))
    generated_json["audio_playlist_generated"] = lesson_ready and (question_ready or not _has_questions(material))


def generate_material_audio_playlist(material: LearningMaterial, scope: str = "all") -> dict:
    scope = (scope or "all").strip().lower()
    if scope not in {"all", "lessons", "questions"}:
        raise AudioGenerationError("Audio scope must be lessons, questions, or all.")

    generated_json = material.generated_json or {}
    existing_playlist = generated_json.get("lesson_playlist") or []
    lesson_playlist = [
        item for item in existing_playlist
        if item.get("type") not in {"practice_questions", "practice_question"}
    ]
    existing_question_playlist = [
        item for item in existing_playlist
        if item.get("type") == "practice_question"
    ]
    if not lesson_playlist:
        raise AudioGenerationError("This material has no lesson playlist to synthesize.")
    if scope == "questions" and not _has_questions(material):
        raise AudioGenerationError("Generate practice questions before generating question audio.")

    narration_by_order = _playlist_text_by_order(material)
    nodes = list(material.learning_objects.all().order_by("order", "id"))
    audio_dir = Path(settings.MEDIA_ROOT) / "audio_lessons" / f"material_{material.id}"
    updated_playlist = []
    generated_count = 0

    for item in lesson_playlist:
        updated_item = dict(item)
        position = len(updated_playlist)
        if scope in {"all", "lessons"}:
            narration_order = updated_item.get("narration_item_order")
            text = narration_by_order.get(int(narration_order)) if narration_order is not None else ""
            audio_path = synthesize_text_to_audio(text, audio_dir / f"playlist_item_{position + 1}")
            relative_path = audio_path.relative_to(settings.MEDIA_ROOT).as_posix()
            updated_item["audio_status"] = "generated"
            updated_item["audio_url"] = f"{settings.MEDIA_URL}{relative_path}"
            updated_item["audio_file"] = relative_path
            generated_count += 1
        updated_item["order"] = position
        updated_playlist.append(updated_item)

    if scope == "lessons":
        for item in existing_question_playlist:
            updated_item = dict(item)
            updated_item["order"] = len(updated_playlist)
            updated_playlist.append(updated_item)
    else:
        for node in nodes:
            questions = sorted(
                node.generated_questions.all(),
                key=lambda q: (_DIFFICULTY_ORDER.get(q.difficulty, 3), q.id),
            )
            for question_index, question in enumerate(questions, start=1):
                question_path = synthesize_text_to_audio(
                    _question_narration_text(question_index, question),
                    audio_dir / f"question_{question.id}",
                )
                question_relative = question_path.relative_to(settings.MEDIA_ROOT).as_posix()
                updated_playlist.append(
                    {
                        "order": len(updated_playlist),
                        "title": f"Question {question_index} ({question.difficulty}) - {node.title}",
                        "type": "practice_question",
                        "node_id": node.id,
                        "question_id": question.id,
                        "audio_status": "generated",
                        "audio_url": f"{settings.MEDIA_URL}{question_relative}",
                        "audio_file": question_relative,
                    }
                )
                generated_count += 1

    generated_json["lesson_playlist"] = updated_playlist
    if scope in {"all", "lessons"}:
        generated_json["lesson_audio_generated"] = True
    if scope in {"all", "questions"}:
        generated_json["question_audio_generated"] = True
    _sync_audio_flags(generated_json, material)
    material.generated_json = generated_json
    material.save(update_fields=["generated_json"])

    return {
        "generated_count": generated_count,
        "lesson_playlist": updated_playlist,
        "scope": scope,
    }
