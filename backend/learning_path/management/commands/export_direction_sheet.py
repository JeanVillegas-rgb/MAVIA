"""Write a topic's v7 direction votes as a CSV that Excel recalculates.

    python manage.py export_direction_sheet 12 --output topic12.csv     # a live topic
    python manage.py export_direction_sheet --gold 340 --output 340.csv # a frozen gold topic
"""

import csv

from django.core.management.base import BaseCommand, CommandError

from learning_path.services.concept_units import concepts_for_topic
from learning_path.services.direction_sheet import direction_sheet_rows
from learning_path.services.gold import load_gold
from lessons.models import OutlineNode


class Command(BaseCommand):
    help = "Write a topic's direction votes as a spreadsheet with live formulas."

    def add_arguments(self, parser):
        parser.add_argument("topic_id", nargs="?", type=int, help="Outline topic.")
        parser.add_argument("--gold", help="A frozen gold topic instead, e.g. 340.")
        parser.add_argument("--output", required=True, help="Where to write the CSV.")

    def handle(self, *args, topic_id=None, gold=None, output, **options):
        if gold:
            _, concepts = load_gold(gold)
        elif topic_id is not None:
            node = OutlineNode.objects.filter(pk=topic_id).first()
            if node is None:
                raise CommandError(f"Topic {topic_id} does not exist.")
            concepts = concepts_for_topic(node)
        else:
            raise CommandError("Give a topic id or --gold.")
        # utf-8-sig so Excel reads the titles' accents correctly.
        with open(output, "w", newline="", encoding="utf-8-sig") as handle:
            csv.writer(handle).writerows(direction_sheet_rows(concepts))
        self.stdout.write(f"Wrote {output}")
