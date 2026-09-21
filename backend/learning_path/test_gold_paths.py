"""Acceptance: the two real lessons produce the teacher's learning paths.

Runs the real sentence encoder over real lesson text, so it is skipped when the
semantic models are not installed. Everything else in the learning-path suite
uses deterministic stand-ins; this is the one test that says whether the
criteria work on actual content.

The gold standard has known gaps: four edges on topic 79 that the criteria do
not currently accept (two come through as pending, teacher-approvable; two are
not proposed at all). Those gaps are recorded in each fixture's
``known_missing`` list rather than hidden by loosening the assertions here --
see the "Known gaps" section of ``docs/learning_path_revision_2026-09-17.md``
for the reasons and the amendment history.
"""

import json
import unittest

from django.test import SimpleTestCase

from lessons.services.semantic_grouping import SemanticUnavailable, runtime

from .services import criteria
from .services.gold import gold_report, load_gold


class GoldPathTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        try:
            cls.engine = runtime()
        except SemanticUnavailable as exc:
            raise unittest.SkipTest(f"Semantic models unavailable: {exc}")

    def _report(self, topic_id):
        data, concepts = load_gold(topic_id)
        return gold_report(data, concepts, criteria.decide_pairs(concepts, self.engine))

    def _assert_gold(self, report):
        # The full report is shown only when an assertion fails.
        details = f"\nFull report:\n{json.dumps(report, indent=2)}"
        self.assertEqual(
            report["unexpected_missing"], [],
            "required edges not accepted and not in known_missing" + details,
        )
        self.assertEqual(
            report["gaps_closed"], [],
            "a known_missing edge is now accepted; update known_missing in the gold_map and gold_topic JSON"
            + details,
        )
        self.assertEqual(report["forbidden_accepted"], [], "forbidden edges accepted" + details)
        self.assertTrue(report["order_matches"], f"order was {report['order']}" + details)

    def test_solid_liquid_and_gas(self):
        self._assert_gold(self._report(62))

    def test_reproduction_among_flowering_plants(self):
        self._assert_gold(self._report(79))
