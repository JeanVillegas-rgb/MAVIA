"""Record a hand-check's answers as teacher decisions on prerequisite links.

    python manage.py import_hand_check docs/learning_path_hand_check_2026-09-14.json

"yes" becomes an approved link and "no" a rejected one; "unsure" is left
undecided. Decisions are keyed by concept (group) id, so answers about a
concept that has since been regrouped or deleted are reported and skipped
rather than attached to the wrong concept.
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from learning_path.models import ConceptPrerequisite
from lessons.models import LearningObjectGroup, OutlineNode

STATUS_BY_ANSWER = {
    "yes": ConceptPrerequisite.Status.APPROVED,
    "no": ConceptPrerequisite.Status.REJECTED,
}


class Command(BaseCommand):
    help = "Import a prerequisite hand-check as teacher decisions."

    def add_arguments(self, parser):
        parser.add_argument("path", help="Hand-check JSON exported from the review page.")
        parser.add_argument("--dry-run", action="store_true", help="Report without writing.")

    def handle(self, *args, path, dry_run=False, **options):
        try:
            record = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CommandError(f"Could not read {path}: {exc}") from exc

        node_id = record.get("topic", {}).get("outline_node_id")
        node = OutlineNode.objects.filter(pk=node_id).first()
        if node is None:
            raise CommandError(f"Topic {node_id} does not exist in this database.")

        groups = set(
            LearningObjectGroup.objects.filter(outline_node=node).values_list("id", flat=True)
        )
        written, skipped_unsure, missing = 0, 0, []
        now = timezone.now()
        for edge in record.get("edges", []):
            status = STATUS_BY_ANSWER.get(edge.get("answer"))
            if status is None:
                skipped_unsure += 1
                continue
            pair = (edge["prerequisite_group_id"], edge["dependent_group_id"])
            if pair[0] not in groups or pair[1] not in groups:
                missing.append(f'{edge["prerequisite_title"]} -> {edge["dependent_title"]}')
                continue
            if not dry_run:
                ConceptPrerequisite.objects.update_or_create(
                    prerequisite_id=pair[0],
                    dependent_id=pair[1],
                    defaults={
                        "outline_node": node,
                        "status": status,
                        "source": ConceptPrerequisite.Source.TEACHER,
                        "decided_at": now,
                    },
                )
            written += 1

        verb = "Would record" if dry_run else "Recorded"
        self.stdout.write(
            f"{verb} {written} teacher decision(s) for “{node.title}”; "
            f"{skipped_unsure} unsure left undecided."
        )
        for pair in missing:
            self.stdout.write(self.style.WARNING(f"  skipped, concept no longer exists: {pair}"))
