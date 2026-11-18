"""List (and with --delete, remove) media files no row refers to."""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from lessons.models import CourseOutline, LearningMaterial

FOLDERS = ("learning_materials", "outlines")


class Command(BaseCommand):
    help = "Find uploaded files that no LearningMaterial or CourseOutline refers to."

    def add_arguments(self, parser):
        parser.add_argument("--delete", action="store_true", help="Delete the orphaned files.")

    def handle(self, *args, delete=False, **options):
        root = Path(settings.MEDIA_ROOT)
        referenced = {
            (root / name).resolve()
            for name in [
                *LearningMaterial.objects.exclude(pdf_file="").values_list("pdf_file", flat=True),
                *CourseOutline.objects.exclude(outline_file="").values_list("outline_file", flat=True),
            ]
        }
        orphans = [
            path for folder in FOLDERS if (root / folder).is_dir()
            for path in sorted((root / folder).iterdir())
            if path.is_file() and path.resolve() not in referenced
        ]
        for path in orphans:
            self.stdout.write(str(path.relative_to(root)))
            if delete:
                path.unlink()
        verb = "Deleted" if delete else "Found"
        self.stdout.write(self.style.SUCCESS(f"{verb} {len(orphans)} orphaned file(s)."))
