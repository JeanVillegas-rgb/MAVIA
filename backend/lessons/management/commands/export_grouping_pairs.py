import hashlib
import itertools
import json
from pathlib import Path
import random

from django.core.management.base import BaseCommand, CommandError
from lessons.models import LearningObject
from lessons.services.learning_resource_linker import normalize_learning_object_title


class Command(BaseCommand):
    help = "Export unlabeled real-PDF pairs for human equivalence judgments. Never infer gold labels from groups."

    def add_arguments(self, parser):
        parser.add_argument("--output", required=True)
        parser.add_argument("--limit", type=int, default=200)
        parser.add_argument("--course", type=int)
        parser.add_argument("--seed", type=int, default=42)

    def handle(self, *args, **options):
        if options["limit"] < 1:
            raise CommandError("limit must be positive")
        query = LearningObject.objects.filter(kind="text").exclude(content="").select_related("material").order_by("id")
        if options["course"]:
            query = query.filter(material__course_id=options["course"])
        objects = list(query)
        # Deterministic reservoirs cover same-title hard cases, other same-topic
        # pairs, and cross-topic negatives. Titles stratify sampling, not grouping.
        rng = random.Random(options["seed"])
        buckets, seen = {key: [] for key in range(3)}, {key: 0 for key in range(3)}
        for a, b in itertools.combinations(objects, 2):
            if a.material_id == b.material_id or a.material.course_id != b.material.course_id:
                continue
            same_topic = a.material.outline_node_id == b.material.outline_node_id
            same_title = normalize_learning_object_title(a.title) == normalize_learning_object_title(b.title)
            bucket = 0 if same_topic and same_title else 1 if same_topic else 2
            seen[bucket] += 1
            if len(buckets[bucket]) < options["limit"]:
                buckets[bucket].append((a, b))
            else:
                index = rng.randrange(seen[bucket])
                if index < options["limit"]:
                    buckets[bucket][index] = (a, b)
        pairs = []
        while len(pairs) < options["limit"] and any(buckets.values()):
            for key in buckets:
                if buckets[key] and len(pairs) < options["limit"]:
                    pairs.append(buckets[key].pop())
        output = Path(options["output"])
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            with output.open("x", encoding="utf-8") as stream:
                for a, b in pairs:
                    def payload(item):
                        return {"metadata_id": str(item.metadata_id), "title": item.title,
                                "content": item.content, "material_id": item.material_id,
                                "source": item.material.pdf_file.name, "course_id": item.material.course_id,
                                "topic_id": item.material.outline_node_id, "kind": item.kind,
                                "section_title": item.section_title, "order": item.order}
                    pair_id = hashlib.sha256(f"{a.metadata_id}:{b.metadata_id}".encode()).hexdigest()[:16]
                    stream.write(json.dumps({"pair_id": pair_id, "left": payload(a), "right": payload(b),
                                             "label": "", "split": "", "reviewed_by": "", "notes": ""}, ensure_ascii=False) + "\n")
        except FileExistsError as exc:
            raise CommandError("Output exists; choose a new filename to preserve labels.") from exc
        self.stdout.write(f"Exported {len(pairs)} unlabeled pairs to {output}. No groups changed.")
