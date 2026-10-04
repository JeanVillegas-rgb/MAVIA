from django.core.management.base import BaseCommand, CommandError
from lessons.models import LearningObject
from lessons.services.semantic_grouping import FINGERPRINT, SemanticRuntime, SemanticUnavailable


class Command(BaseCommand):
    help = "Load pinned semantic models and cache content embeddings; does not change groups."

    def add_arguments(self, parser):
        parser.add_argument("--download", action="store_true", help="Permit downloading public weights; text stays local.")

    def handle(self, *args, **options):
        try:
            self.stdout.write("Loading pinned models on CPU...", ending="\n")
            engine = SemanticRuntime(download=options["download"])
            texts = list(LearningObject.objects.filter(kind="text").values_list("content", flat=True))
            supported = [text for text in texts if engine.supports(text)]
            if supported:
                engine.embeddings(supported)
            self.stdout.write(f"Cached {len(supported)} embeddings; {len(texts) - len(supported)} empty/long objects skipped.")
            self.stdout.write(f"Model fingerprint: {FINGERPRINT}")
        except SemanticUnavailable as exc:
            raise CommandError(str(exc)) from exc
