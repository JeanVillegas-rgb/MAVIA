"""Export a topic's concepts *as the pipeline actually built them*.

``export_gold_concepts`` writes the teacher's ideal grouping: one fixture
concept per entry of a hand-written map. That is the right shape for asking
"do the criteria reproduce the teacher's path", and the wrong shape for asking
"does the path the learner gets contain a forbidden edge", because the two
differ. Measured on topic 152 (2026-09-22): the teacher's 7 concepts and the
pipeline's 14 carry the same lesson, and only the 14-concept shape accepts
``Solid -> Gas`` -- with 7 concepts the diagram-description words sit in 3 of
them and ``REF_MAX_DF_RATIO`` drops them, with 14 they sit in 3 of 14 and
survive. A fixture built from the teacher's grouping therefore cannot see the
failure the teacher sees.

So this command freezes ``concepts_for_topic`` output -- the same concepts,
members, text, headings and order the publish pipeline derives its path from --
and labels each with the gold key it belongs to, taken from a map file. Several
concepts may carry one key (the pipeline split a concept the teacher would
join), and a concept the teacher's map does not mention carries ``null``: it
still takes part in the derivation, because it changes document frequencies and
the order, but no edge is scored against it.
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from lessons.models import OutlineNode
from learning_path.services.concept_units import concepts_for_topic


class Command(BaseCommand):
    help = "Freeze a topic's derived concepts as a gold fixture (live grouping, live text)."

    def add_arguments(self, parser):
        parser.add_argument("topic_id", type=int)
        parser.add_argument("map_path", help="gold map: required edges, parallel sets, and concept keys")
        parser.add_argument("out_path")

    def handle(self, *args, topic_id, map_path, out_path, **options):
        spec = json.loads(Path(map_path).read_text(encoding="utf-8"))
        try:
            node = OutlineNode.objects.get(id=topic_id)
        except OutlineNode.DoesNotExist:
            raise CommandError(f"No outline node {topic_id}.")

        # {concept id: gold key}. A concept absent from the map exports as null.
        keys = {int(cid): key for cid, key in spec["concept_keys"].items()}
        concepts = list(concepts_for_topic(node))
        unknown = sorted(set(keys) - {concept.id for concept in concepts})
        if unknown:
            raise CommandError(f"concept_keys names concepts this topic does not have: {unknown}")

        rows = []
        for concept in concepts:
            rows.append({
                "key": keys.get(concept.id),
                "concept_id": concept.id,
                "title": concept.title,
                "section_title": concept.section_title,
                "kind": concept.kind,
                "members": [
                    {
                        "title": member.title,
                        "section_title": member.section_title or "",
                        "content": member.content or "",
                        "material_id": member.material_id,
                        "order": member.order,
                    }
                    for member in concept.members
                ],
            })

        output = {name: spec[name] for name in ("topic_id", "required", "parallel", "structural", "expected_order")}
        output["known_missing"] = spec.get("known_missing", [])
        output["concepts"] = rows
        Path(out_path).write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
        labelled = sum(1 for row in rows if row["key"])
        self.stdout.write(self.style.SUCCESS(
            f"Wrote {len(rows)} concepts ({labelled} labelled) to {out_path}"
        ))
