"""Acceptance: v6.1 on real lesson text, against the answer keys.

Design set 340/357, final check 351/353/365 (scored once, 2026-10-02), the seen set
341-348 and the retired 62/79/152. Uses the real encoder; skipped when it cannot load.
Floors are measured v6.1 values; see docs/learning-path-v6-1-evaluation-2026-10-02.md.
"""

import json
import unittest

from django.test import SimpleTestCase

from .services import criteria
from .services.embeddings import EncoderUnavailable, load_encoder
from .services.gold import gold_report, load_gold

# Measured v6.1 values (docs/learning-path-v6-1-evaluation/): design-v6-1.json (340, 357),
# retired-v6-1.json (62, 79, 152), final-v6-1.json (351, 353, 365, the single final run) and
# seen-v6-1.json (341-348). They guard against regressions and were not tuned.
REACHABLE_FLOOR = {62: 9, 79: 5, 152: 9, 340: 18, 357: 16, 341: 7, 343: 5, 347: 5, 348: 7,
                   351: 19, 353: 7, 365: 10}
COVERED_FLOOR = {62: 9, 79: 4, 152: 8, 340: 13, 357: 11, 341: 3, 343: 3, 347: 1, 348: 1,
                 351: 10, 353: 2, 365: 2}
TAU_FLOOR = {62: 1.0, 79: 1.0, 152: 0.80, 340: 0.63, 357: 1.0, 341: 1.0, 343: 1.0, 347: 1.0, 348: 1.0,
             351: 1.0, 353: 1.0, 365: 1.0}
# Accepted against the key in the final run, explained in the v6.1 report: on 351 organs the key
# lists as parallel are linked through shared body words; on 353 the four "teamwork" systems,
# which the key keeps parallel, are linked to each other.
KNOWN_FORBIDDEN = {
    351: [["brain", "bones_muscles"], ["heart", "bones_muscles"], ["lungs", "bones_muscles"],
          ["lungs", "liver"], ["stomach", "liver"]],
    353: [["bones_muscles_teamwork", "food_energy_teamwork"], ["brain_teamwork", "food_energy_teamwork"],
          ["brain_teamwork", "heart_lungs_teamwork"], ["food_energy_teamwork", "brain_teamwork"],
          ["food_energy_teamwork", "heart_lungs_teamwork"], ["heart_lungs_teamwork", "brain_teamwork"],
          ["heart_lungs_teamwork", "food_energy_teamwork"]],
}


class GoldPathTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        try:
            load_encoder()
        except EncoderUnavailable as exc:
            raise unittest.SkipTest(f"encoder unavailable: {exc}")

    def _report(self, topic_id):
        data, concepts = load_gold(topic_id)
        return gold_report(data, concepts, criteria.decide_pairs(concepts))

    def _assert_gold(self, topic_id):
        report = self._report(topic_id)
        shown = json.dumps(report, indent=2)
        self.assertEqual(
            sorted(map(tuple, report["forbidden_accepted"])),
            sorted(map(tuple, KNOWN_FORBIDDEN.get(topic_id, []))),
            shown,
        )
        self.assertGreaterEqual(report["reachable_count"], REACHABLE_FLOOR[topic_id], shown)
        self.assertGreaterEqual(report["covered_count"], COVERED_FLOOR[topic_id], shown)
        self.assertGreaterEqual(report["kendall_tau"], TAU_FLOOR[topic_id], shown)

    def test_solid_liquid_and_gas(self):
        self._assert_gold(62)

    def test_reproduction_among_flowering_plants(self):
        self._assert_gold(79)

    def test_solid_liquid_and_gas_as_the_pipeline_groups_it_today(self):
        self._assert_gold(152)

    def test_topic_340_against_the_recommended_arrangement(self):
        self._assert_gold(340)

    def test_topic_357_against_the_recommended_arrangement(self):
        self._assert_gold(357)

    def test_topic_341_grouping_materials(self):
        self._assert_gold(341)

    def test_topic_343_mixtures(self):
        self._assert_gold(343)

    def test_topic_347_changes_in_materials(self):
        self._assert_gold(347)

    def test_topic_348_separating_mixtures(self):
        self._assert_gold(348)

    def test_topic_351_major_organs(self):
        self._assert_gold(351)

    def test_topic_353_organ_systems_at_work(self):
        self._assert_gold(353)

    def test_topic_365_living_things_and_their_environment(self):
        self._assert_gold(365)
