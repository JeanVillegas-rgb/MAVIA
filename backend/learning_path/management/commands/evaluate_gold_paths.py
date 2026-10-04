"""Print the gold report for frozen topics, with the switches the evaluation needs.

--rule RULE          three-votes (v7) or reference-order (v6); default criteria.DEFAULT_RULE
--moves              also score each approved move of the topic (fixtures/direction_moves.json)
--by-vote            add each v7 vote's right/wrong/silent count on the key's links
--final-check        required to score 341-348 (v7 lock) or 351/353/365 (v6.1 final check)
--without CLUE       silence one clue (v6 ablation)
--build-on-latest    Kahn ties prefer the concept building on the latest step (off by default)
--meaning-matches N  average the N best matches in the meaning clue (size check)
--by-clue            add each v6 clue's right/wrong count on the key's links
--baseline order     link each concept to the one before it (no text read)
"""

import json

from django.core.management.base import BaseCommand, CommandError

from learning_path.services import clues, criteria
from learning_path.services.calibration import load_calibration
from learning_path.services.fusion import CLUES
from learning_path.services.gold import (
    clue_accuracy, gate_loss, gold_report, load_gold, move_report, order_only_decisions, vote_accuracy,
)
from learning_path.services.moves import apply_move, load_moves

DESIGN_TOPICS = ["340", "357"]
FINAL_CHECK_TOPICS = {"341", "343", "347", "348", "351", "353", "365"}


class Command(BaseCommand):
    help = "Report derived learning paths against the gold standard."

    def add_arguments(self, parser):
        parser.add_argument("--topics", nargs="*", default=DESIGN_TOPICS)
        parser.add_argument("--rule", choices=criteria.RULES)
        parser.add_argument("--moves", action="store_true")
        parser.add_argument("--by-vote", action="store_true")
        parser.add_argument("--final-check", action="store_true")
        parser.add_argument("--baseline", choices=["order"])
        parser.add_argument("--without", choices=CLUES)
        parser.add_argument("--build-on-latest", action="store_true")
        parser.add_argument("--meaning-matches", type=int, default=1)
        parser.add_argument("--by-clue", action="store_true")

    def handle(self, *args, topics, rule, moves, by_vote, final_check, without, build_on_latest,
               meaning_matches, by_clue, baseline, **options):
        held_back = sorted(FINAL_CHECK_TOPICS & {str(topic) for topic in topics})
        if held_back and not final_check:
            raise CommandError(
                f"{', '.join(held_back)} belong to the final check and are scored once, after the "
                "freeze (v7 spec section 8). Pass --final-check to score them."
            )
        rule = rule or criteria.DEFAULT_RULE
        calibration = load_calibration()
        previous_matches = clues.MEANING_MATCHES
        clues.MEANING_MATCHES = meaning_matches

        def decide(concepts):
            if baseline == "order":
                return order_only_decisions(concepts)
            return criteria.decide_pairs(
                concepts, calibration=calibration, without=(without,) if without else (), rule=rule,
            )

        try:
            reports = []
            for topic_id in topics:
                data, concepts = load_gold(topic_id)
                report = gold_report(data, concepts, decide(concepts), build_on_latest=build_on_latest)
                report["gate_loss"] = gate_loss(data, concepts, calibration)
                if by_clue:
                    report["clue_accuracy"] = clue_accuracy(data, concepts, calibration)
                if by_vote:
                    report["vote_accuracy"] = vote_accuracy(data, concepts)
                if moves:
                    report["moves"] = []
                    for move in load_moves(topic_id):
                        moved = apply_move(concepts, move)
                        report["moves"].append(move_report(data, moved, decide(moved), move))
                reports.append(report)
        finally:
            clues.MEANING_MATCHES = previous_matches
        self.stdout.write(json.dumps({
            "calibration": calibration["source"],
            "rule": rule,
            "without": without,
            "baseline": baseline,
            "build_on_latest": build_on_latest,
            "meaning_matches": meaning_matches,
            "reports": reports,
        }, indent=2))
