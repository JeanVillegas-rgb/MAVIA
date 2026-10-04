"""Snapshot two topics for a course-level answer key, or freeze them as a fixture.

``--snapshot`` writes both topics' concepts with their full text, the input
given to the AI tool that drafts the key. ``--map`` + ``--out`` freeze the
concepts the pipeline derives today, labelled from the map, so the course gold
report runs without the database (the same idea as ``export_live_concepts``).
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from lessons.models import OutlineNode
from learning_path.services.concept_units import concepts_for_topic


class Command(BaseCommand):
    help = "Snapshot or freeze two topics' concepts for the course-level gold report."

    def add_arguments(self, parser):
        parser.add_argument("first_topic", type=int)
        parser.add_argument("second_topic", type=int)
        parser.add_argument("--snapshot")
        parser.add_argument("--map")
        parser.add_argument("--out")

    def handle(self, *args, first_topic, second_topic, snapshot=None, map=None, out=None, **options):
        if not snapshot and not (map and out):
            raise CommandError("Give --snapshot PATH, or --map MAP --out FIXTURE.")
        topics = []
        for topic_id in (first_topic, second_topic):
            node = OutlineNode.objects.filter(pk=topic_id).first()
            if node is None:
                raise CommandError(f"No outline node {topic_id}.")
            topics.append((node, list(concepts_for_topic(node))))

        if snapshot:
            lines = [f"# Topics {first_topic} and {second_topic}: concepts with full text", ""]
            for node, concepts in topics:
                lines += [f"## {node.title} (topic {node.id})", ""]
                for index, concept in enumerate(concepts, start=1):
                    lines.append(f"{index}. {concept.title} (concept {concept.id})")
                    for member in concept.members:
                        lines.append(f"   - [{member.material.title}] {' '.join((member.content or '').split())}")
                    lines.append("")
            Path(snapshot).write_text("\n".join(lines) + "\n", encoding="utf-8")
            self.stdout.write(self.style.SUCCESS(f"Wrote {snapshot}"))

        if map and out:
            spec = json.loads(Path(map).read_text(encoding="utf-8"))
            keys = {int(concept_id): key for concept_id, key in spec["concept_keys"].items()}
            output = {name: spec[name] for name in ("topics", "required", "unrelated")}
            output["_note"] = spec.get("_note", "")
            output["concepts"] = {
                str(node.id): [
                    {
                        "key": keys.get(concept.id),
                        "concept_id": concept.id,
                        "title": concept.title,
                        "section_title": concept.section_title,
                        "kind": concept.kind,
                        "members": [
                            {"title": member.title, "section_title": member.section_title or "",
                             "content": member.content or "", "material_id": member.material_id, "order": member.order}
                            for member in concept.members
                        ],
                    }
                    for concept in concepts
                ]
                for node, concepts in topics
            }
            Path(out).write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
            self.stdout.write(self.style.SUCCESS(f"Wrote {out}"))
