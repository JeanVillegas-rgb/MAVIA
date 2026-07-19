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


def generate_material_audio_playlist(material: LearningMaterial) -> dict:
    generated_json = material.generated_json or {}
    playlist = generated_json.get("lesson_playlist") or []
    if not playlist:
        raise AudioGenerationError("This material has no lesson playlist to synthesize.")

    narration_by_order = _playlist_text_by_order(material)
    audio_dir = Path(settings.MEDIA_ROOT) / "audio_lessons" / f"material_{material.id}"
    updated_playlist = []
    generated_count = 0

    for item in playlist:
        updated_item = dict(item)
        order = int(updated_item.get("order", len(updated_playlist)))
        narration_order = updated_item.get("narration_item_order")
        text = narration_by_order.get(int(narration_order)) if narration_order is not None else ""
        audio_path = audio_dir / f"playlist_item_{order + 1}.wav"

        synthesize_text_to_wav(text, audio_path)
        relative_path = audio_path.relative_to(settings.MEDIA_ROOT).as_posix()
        updated_item["audio_status"] = "generated"
        updated_item["audio_url"] = f"{settings.MEDIA_URL}{relative_path}"
        updated_item["audio_file"] = relative_path
        updated_playlist.append(updated_item)
        generated_count += 1

    generated_json["lesson_playlist"] = updated_playlist
    generated_json["audio_playlist_generated"] = True
    material.generated_json = generated_json
    material.save(update_fields=["generated_json"])

    return {
        "generated_count": generated_count,
        "lesson_playlist": updated_playlist,
    }
