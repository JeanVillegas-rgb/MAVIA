"""Course-level acceptance on real lesson text (docs/course-path-evaluation-2026-09-30.md).

Hard gate: no accepted link between topics of different subjects. The floors
are the measured values after course spec amendment 1; recall is known to be
low and is reported, not hidden. Skipped when the encoder or a fixture is missing.
"""

import json
import unittest

from django.test import SimpleTestCase

from .services.course_criteria import CLOSEST, decide_course_pairs
from .services.embeddings import EncoderUnavailable, load_encoder
from .services.gold import FIXTURES, course_gold_report, load_course_gold

UNRELATED = [(340, 357), (341, 357), (343, 357), (347, 357), (348, 357)]
# (first, second): (reachable floor, accepted-precision floor or None)
RELATED_FLOORS = {(340, 341): (0, None), (340, 343): (2, 0.40), (340, 347): (0, None), (343, 348): (0, None)}


class CourseGoldPathTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        try:
            load_encoder()
        except EncoderUnavailable as exc:
            raise unittest.SkipTest(f"encoder unavailable: {exc}")

    def _report(self, first, second):
        if not (FIXTURES / f"gold_course_{first}_{second}.json").exists():
            self.skipTest(f"fixture gold_course_{first}_{second}.json missing")
        data, topics = load_course_gold(first, second)
        return course_gold_report(data, topics, decide_course_pairs(topics))

    def test_topics_of_different_subjects_get_no_accepted_link(self):
        for first, second in UNRELATED:
            with self.subTest(pair=(first, second)):
                report = self._report(first, second)
                self.assertEqual(report["unrelated_accepted"], 0, json.dumps(report, indent=2))

    def test_related_topics_stay_at_the_measured_floors(self):
        for (first, second), (reach, precision) in RELATED_FLOORS.items():
            with self.subTest(pair=(first, second)):
                report = self._report(first, second)
                details = json.dumps(report, indent=2)
                self.assertGreaterEqual(report["reachable_count"], reach, details)
                if precision is not None:
                    self.assertGreaterEqual(report["accepted_precision"], precision, details)

    def test_the_closest_rule_accepts_nothing_between_different_subjects(self):
        for first, second in UNRELATED:
            with self.subTest(pair=(first, second)):
                if not (FIXTURES / f"gold_course_{first}_{second}.json").exists():
                    self.skipTest(f"fixture gold_course_{first}_{second}.json missing")
                data, topics = load_course_gold(first, second)
                report = course_gold_report(data, topics, decide_course_pairs(topics, rule=CLOSEST))
                self.assertEqual(report["unrelated_accepted"], 0, json.dumps(report, indent=2))
