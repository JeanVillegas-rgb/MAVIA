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

    def test_solid_liquid_and_gas_as_the_pipeline_groups_it_today(self):
        """The same lesson, in the shape a real publish actually derives.

        Topics 62 and 79 above hold the teacher's own grouping and an older
        upload's text. Both differences hide failures the teacher sees: measured
        on 2026-09-22, the teacher's 7 concepts and the pipeline's 14 carry the
        same lesson, and only the 14-concept shape accepts ``Solid -> Gas``,
        which the gold map forbids. With 7 concepts the diagram-description
        vocabulary ("drawn", "spaced", "dots") sits in 3 of them and
        ``REF_MAX_DF_RATIO`` drops it; with 14 it sits in 3 of 14 and survives
        to carry a full reference vote at ``ref_forward`` 0.0369.

        So this fixture freezes ``concepts_for_topic`` output for the live
        topic 152 -- the pipeline's own concepts, members, headings and order --
        and scores it against the same teacher map topic 62 uses. See
        ``docs/learning_path_revision_2026-09-17.md``.
        """
        self._assert_gold(self._report(152))
