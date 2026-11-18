"""Stored PDFs leave with their rows.

Measured 2026-09-17: media/learning_materials held about 390 files for 4 live
materials -- rows deleted by cascade (a course or topic) never removed their
files, and tests wrote into the real media folder.
"""

from io import StringIO
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import TestCase

from .models import CourseGroup, LearningMaterial


class MediaCleanupTests(TestCase):
    def _material(self, course):
        material = LearningMaterial(course=course, title="Doc")
        material.pdf_file.save("doc.pdf", ContentFile(b"%PDF-1.4"), save=False)
        material.save()
        return material

    def test_tests_do_not_write_into_the_project_media_folder(self):
        self.assertNotEqual(Path(settings.MEDIA_ROOT), Path(settings.BASE_DIR) / "media")

    def test_a_cascade_delete_removes_the_file(self):
        course = CourseGroup.objects.create(title="Science")
        material = self._material(course)
        path = Path(material.pdf_file.path)
        self.assertTrue(path.exists())

        with self.captureOnCommitCallbacks(execute=True):
            course.delete()

        self.assertFalse(path.exists())

    def test_orphans_are_listed_and_only_deleted_on_request(self):
        course = CourseGroup.objects.create(title="Science")
        kept = self._material(course)
        orphan = Path(settings.MEDIA_ROOT) / "learning_materials" / "orphan.pdf"
        orphan.parent.mkdir(parents=True, exist_ok=True)
        orphan.write_bytes(b"%PDF-1.4")

        out = StringIO()
        call_command("clean_orphan_media", stdout=out)
        self.assertIn("orphan.pdf", out.getvalue())
        self.assertTrue(orphan.exists())

        call_command("clean_orphan_media", "--delete", stdout=StringIO())
        self.assertFalse(orphan.exists())
        self.assertTrue(Path(kept.pdf_file.path).exists())
