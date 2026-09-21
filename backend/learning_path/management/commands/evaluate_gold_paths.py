"""Print the gold report for both lessons; ``--grid`` sweeps the criteria constants.

The grid is how the constants in ``criteria.py`` were chosen: the combination
with no forbidden edges and the most required edges across both lessons, ties
going to the listed defaults. Its output is pasted into
``docs/learning_path_revision_2026-09-17.md``.
"""

import itertools
import json

from django.core.management.base import BaseCommand

from learning_path.services import criteria
from learning_path.services.gold import gold_report, load_gold
from lessons.services.semantic_grouping import runtime

TOPICS = (62, 79)
GRID = {
    "REF_MAX_DF_RATIO": (0.2, 0.34, 0.5),
    "REF_MARGIN": (0.0, 0.05, 0.1, 0.2),
    "PHRASE_COSINE": (0.7, 0.8, 0.9),
    "MIN_IOL_MARGIN": (0.1, 0.25),
}


class Command(BaseCommand):
    help = "Report derived learning paths against the gold standard."

    def add_arguments(self, parser):
        parser.add_argument("--grid", action="store_true")

    def _reports(self, engine):
        reports = []
        for topic_id in TOPICS:
            data, concepts = load_gold(topic_id)
            reports.append(gold_report(data, concepts, criteria.decide_pairs(concepts, engine)))
        return reports

    def handle(self, *args, grid=False, **options):
        engine = runtime()
        if not grid:
            self.stdout.write(json.dumps(self._reports(engine), indent=2))
            return

        names = [name for name in GRID if hasattr(criteria, name)]
        defaults = {name: getattr(criteria, name) for name in names}
        rows = []
        try:
            for values in itertools.product(*(GRID[name] for name in names)):
                for name, value in zip(names, values):
                    setattr(criteria, name, value)
                reports = self._reports(engine)
                rows.append({
                    "settings": dict(zip(names, values)),
                    "required_hits": sum(
                        len(data["required"]) - len(report["missing_required"])
                        for data, report in zip((load_gold(t)[0] for t in TOPICS), reports)
                    ),
                    "forbidden": sum(len(report["forbidden_accepted"]) for report in reports),
                    "orders_match": all(report["order_matches"] for report in reports),
                    "missing": {report["topic"]: report["missing_required"] for report in reports},
                })
        finally:
            for name, value in defaults.items():
                setattr(criteria, name, value)
        rows.sort(key=lambda row: (row["forbidden"], -row["required_hits"], not row["orders_match"]))
        self.stdout.write(json.dumps(rows[:15], indent=2))
