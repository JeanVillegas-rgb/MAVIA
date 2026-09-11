import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, override_settings

from course.models import LessonVariant
from .models import CourseGroup, LearningMaterial, LearningObject
from .services.audio_generator import generate_version_audio


class TeacherVersionAudioTests(TestCase):
    def test_active_versions_are_cached_and_extras_are_not_synthesized(self):
        course = CourseGroup.objects.create(title="Science")
        material = LearningMaterial.objects.create(course=course, title="Lesson")
        obj = LearningObject.objects.create(material=material, title="Solid", content="Solid keeps shape.")
        simple = LessonVariant.objects.create(learning_object=obj, variant="SIMPLIFIED", narration="Keeps shape.")
        LessonVariant.objects.create(learning_object=obj, variant="ELABORATED", narration="A solid keeps its own shape.")
        extra = LessonVariant.objects.create(learning_object=obj, variant="EXTRA", narration="Unused text.")

        def synthesize(text, base):
            path = base.with_suffix(".mp3")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"test audio")
            return path

        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory), patch.dict("os.environ", {"AUDIO_TTS_PROVIDER": "edge"}), patch("lessons.services.audio_generator.synthesize_text_to_audio", side_effect=synthesize) as synth:
            self.assertEqual(generate_version_audio(material)["generated_count"], 2)
            self.assertEqual(generate_version_audio(material)["generated_count"], 0)
            simple.refresh_from_db()
            old_url = simple.audio_url
            simple.narration = "New teacher wording."
            simple.save()
            self.assertEqual(generate_version_audio(material)["generated_count"], 1)
            simple.refresh_from_db()
            self.assertNotEqual(simple.audio_url, old_url)
            self.assertEqual(synth.call_count, 3)
            extra.refresh_from_db()
            self.assertEqual(extra.audio_url, "")
