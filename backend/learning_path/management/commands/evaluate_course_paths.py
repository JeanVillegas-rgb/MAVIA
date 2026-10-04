"""Print the course-level gold report for frozen topic pairs (course spec 2026-10-02, section 5).

--pairs A-B ...   topic pairs; default the design pairs
--rule RULE       strict (today) or shortlist (top down); default COURSE_DEFAULT_RULE
--final-check     required to score the final pairs (scored once, after the freeze)
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from learning_path.services import embeddings
from learning_path.services.calibration import load_calibration
from learning_path.services.course_criteria import COURSE_DEFAULT_RULE, COURSE_RULES, decide_course_pairs
from learning_path.services.course_shortlist import TOPIC_TITLE_CUTOFF, topic_similarities
from learning_path.services.gold import FIXTURES, course_gold_report, course_shortlist_hits, load_course_gold

DESIGN_PAIRS = ["340-341", "340-343", "340-347", "343-348", "340-357", "341-357", "343-357", "347-357", "348-357"]
# 2026-10-02 shortlist final check (spent), then the 2026-10-03 closest-match final check (live ids).
FINAL_CHECK_PAIRS = {"351-353", "341-343", "341-347", "347-348", "351-365", "353-365",
                     "2-10", "3-10", "5-7", "13-19", "15-19", "19-27"}


class Command(BaseCommand):
    help = "Report course-level links against the course answer keys."

    def add_arguments(self, parser):
        parser.add_argument("--pairs", nargs="*", default=DESIGN_PAIRS)
        parser.add_argument("--rule", choices=COURSE_RULES)
        parser.add_argument("--final-check", action="store_true")

    def handle(self, *args, pairs, rule, final_check, **options):
        held_back = sorted(FINAL_CHECK_PAIRS & set(pairs))
        if held_back and not final_check:
            raise CommandError(
                f"{', '.join(held_back)} belong to the final check and are scored once, after the "
                "freeze (course spec 2026-10-02 section 5). Pass --final-check to score them."
            )
        rule = rule or COURSE_DEFAULT_RULE
        calibration = load_calibration()
        titles = json.loads((FIXTURES / "course_topic_titles.json").read_text(encoding="utf-8"))["titles"]
        reports = []
        for pair in pairs:
            first, second = pair.split("-")
            data, topics = load_course_gold(first, second)
            pair_titles = [titles[first], titles[second]]
            decisions = decide_course_pairs(topics, calibration=calibration, rule=rule, topic_titles=pair_titles)
            report = course_gold_report(data, topics, decisions)
            similarity = topic_similarities(pair_titles, embeddings.embed)[(0, 1)]
            report.update(
                pair=pair, unrelated=bool(data.get("unrelated")), topic_similarity=similarity,
                compared=similarity >= TOPIC_TITLE_CUTOFF, shortlist=course_shortlist_hits(data, topics, decisions),
            )
            reports.append(report)
        self.stdout.write(json.dumps({"rule": rule, "reports": reports}, indent=2))
