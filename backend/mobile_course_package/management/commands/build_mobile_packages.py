"""Build the audio course package of every published topic ahead of time.

The first open of a topic after a (re)publish builds its package, which loads
language models and can take a while; without this, the first student to open
it waits. Run it after publishing, or before a demo:

    python manage.py build_mobile_packages            # only missing or stale packages
    python manage.py build_mobile_packages --force    # rebuild all of them
"""

import time

from django.core.management.base import BaseCommand

from lessons.models import OutlineNode
from mobile_course_package.models import TopicPackage
from mobile_course_package.services import build_course_package


class Command(BaseCommand):
    help = "Build the mobile audio course package of every published topic."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Rebuild packages that are already up to date.")

    def handle(self, *args, force=False, **options):
        topics = OutlineNode.objects.filter(parent__isnull=False, published=True, published_at__isnull=False)
        built = skipped = 0
        for topic in topics.order_by("course_id", "order", "id"):
            package = TopicPackage.objects.filter(topic=topic).first()
            if not force and package is not None and package.published_at == topic.published_at:
                skipped += 1
                continue
            started = time.monotonic()
            package = build_course_package(topic)
            if package is None:
                self.stdout.write(self.style.WARNING(f"  {topic.title}: no published learning path, skipped"))
                continue
            built += 1
            self.stdout.write(
                f"  {topic.title}: {len(package.steps)} steps, {len(package.answer_key)} questions "
                f"({time.monotonic() - started:.1f}s)"
            )
        self.stdout.write(self.style.SUCCESS(f"Built {built} package(s); {skipped} already up to date."))
