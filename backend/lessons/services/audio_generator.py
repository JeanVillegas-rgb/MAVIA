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


def synthesize_text_to_wav(text: str, output_path: Path, timeout: int = 120) -> None:
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


_DIFFICULTY_ORDER = {"easy": 0, "medium": 1, "hard": 2}


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
        audio_path = audio_dir / f"playlist_item_{position + 1}.wav"

        synthesize_text_to_wav(text, audio_path)
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
            question_path = audio_dir / f"question_{question.id}.wav"
            synthesize_text_to_wav(
                _question_narration_text(question_index, question), question_path)
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
