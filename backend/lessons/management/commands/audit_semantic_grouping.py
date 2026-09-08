import json
from pathlib import Path
from time import perf_counter

from django.core.management.base import BaseCommand, CommandError
from lessons.models import LearningObject
from lessons.services.learning_resource_linker import _legacy_match_decision
from lessons.services.semantic_grouping import semantic_decision, FINGERPRINT


class Command(BaseCommand):
    help = "Read-only shadow comparison on confirmed objects; never saves group or review changes."

    def add_arguments(self, parser):
        parser.add_argument("--output", required=True)
        parser.add_argument("--limit", type=int, default=50)
        parser.add_argument("--course", type=int)

    def handle(self, *args, **options):
        if options["limit"] < 1:
            raise CommandError("limit must be positive")
        objects = LearningObject.objects.filter(kind="text", material__generated_json__learning_objects_confirmed=True).select_related("material").order_by("id")
        if options["course"]:
            objects = objects.filter(material__course_id=options["course"])
        def brief(decision):
            return None if decision is None else {
                "candidate_metadata_id": str(decision["candidate"].metadata_id),
                "candidate_title": decision["candidate"].title,
                "candidate_content": decision["candidate"].content,
                "candidate_group_id": decision["candidate"].group_id,
                "confidence": decision["confidence"], "evidence": decision["evidence"],
            }
        rows, started = [], perf_counter()
        try:
            for item in objects[:options["limit"]]:
                args = (item.material, item.title, item.content, item.kind, item.order, item.section_title, item.id)
                lexical = _legacy_match_decision(*args)
                semantic = semantic_decision(*args)
                rows.append({"metadata_id": str(item.metadata_id), "title": item.title,
                             "content": item.content, "current_group_id": item.group_id,
                             "lexical": brief(lexical), "semantic": brief(semantic)})
                self.stdout.write(f"Compared object {item.id} ({len(rows)})")
            report = {"model_fingerprint": FINGERPRINT, "read_only": True,
                      "elapsed_seconds": round(perf_counter()-started, 3), "objects": rows}
            path = Path(options["output"])
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("x", encoding="utf-8") as stream:
                json.dump(report, stream, indent=2, ensure_ascii=False)
        except (OSError, RuntimeError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"Compared {len(rows)} objects; no groups changed. Report: {path}")
