import json
from pathlib import Path
from time import perf_counter
from django.core.management.base import BaseCommand, CommandError
from lessons.services.grouping_evaluation import read_labeled_pairs, evaluate
from lessons.services.semantic_grouping import runtime


class Command(BaseCommand):
    help = "Compare STS and TF-IDF on labeled development/test pairs; does not alter groups or enable auto."

    def add_arguments(self, parser):
        parser.add_argument("--pairs", required=True)
        parser.add_argument("--output", required=True)

    def handle(self, *args, **options):
        try:
            rows = read_labeled_pairs(options["pairs"])
            started = perf_counter()
            report = evaluate(rows, runtime())
            report["elapsed_seconds"] = round(perf_counter() - started, 3)
            output = Path(options["output"])
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("x", encoding="utf-8") as stream:
                json.dump(report, stream, indent=2, ensure_ascii=False)
            self.stdout.write(json.dumps(report["validation"], indent=2))
            self.stdout.write(f"Report: {output}. Auto remains disabled pending group-level review.")
        except (ValueError, OSError, RuntimeError, KeyError) as exc:
            raise CommandError(str(exc)) from exc
