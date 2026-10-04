"""Learn the learning path's clue weights and cutoffs from frozen topics.

Topics are gold fixtures (fixtures/gold_topic_<id>.json), so a run can be
repeated exactly. No answer key is read: weights come from how often each clue
agrees with the others, cutoffs from pairs of topics in different subjects.
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from learning_path.services.calibration import CALIBRATION, calibrate
from learning_path.services.concept_text import material_positions, prepare
from learning_path.services.embeddings import embed
from learning_path.services.gold import load_gold


class Command(BaseCommand):
    help = "Learn clue weights and similarity cutoffs; write learning_path/calibration/weights.json."

    def add_arguments(self, parser):
        parser.add_argument("--topics", nargs="+", type=int, required=True, help="topics whose pairs teach the weights")
        parser.add_argument("--unrelated", nargs="+", required=True, help="topics from different subjects, e.g. 340:357")
        parser.add_argument("--out", default=str(CALIBRATION))

    def handle(self, *args, topics, unrelated, out, **options):
        pairs = []
        for pair in unrelated:
            try:
                first, second = (int(part) for part in pair.split(":"))
            except ValueError:
                raise CommandError(f"--unrelated takes topic pairs like 340:357, not {pair!r}")
            pairs.append((first, second))

        texts, positions = {}, {}
        for topic in sorted(set(topics) | {topic for pair in pairs for topic in pair}):
            _, concepts = load_gold(topic)
            texts[topic] = prepare(concepts, embed=embed)
            positions[topic] = material_positions(concepts)

        result = calibrate(texts, positions, topics, pairs)
        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"Wrote {path}: {json.dumps(result['weights'])}"))
