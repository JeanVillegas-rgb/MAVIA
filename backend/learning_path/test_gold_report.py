"""The gold report's v4 measures: reachability, closure precision, rules."""

from types import SimpleNamespace

from django.test import SimpleTestCase

from .services.gold import covered_links, gold_report, order_only_decisions


def concept(id, key, order):
    return SimpleNamespace(id=id, key=key, order=order, title=key, kind="text", members=())


def decision(before, after, verdict, rule=None):
    row = {"prerequisite": before, "dependent": after, "verdict": verdict}
    if rule:
        row["evidence"] = {"rule": rule}
    return row


class GoldReportTests(SimpleTestCase):
    def setUp(self):
        self.matter, self.solid, self.comparing, self.figure = (
            concept(1, "matter", 0), concept(2, "solid", 1),
            concept(3, "comparing", 2), concept(4, "figure", 3),
        )
        self.concepts = [self.matter, self.solid, self.comparing, self.figure]
        self.data = {
            "topic_id": 999,
            "required": [["matter", "solid"], ["solid", "comparing"]],
            "parallel": [],
            "structural": [],
            "forbidden": [["figure", "matter"]],
            "expected_order": ["matter", "solid", "comparing", "figure"],
        }

    def test_pending_required_edges_are_reachable(self):
        report = gold_report(self.data, self.concepts, [
            decision(self.matter, self.solid, "accepted", "containment"),
            decision(self.solid, self.comparing, "pending", "reference"),
        ])

        self.assertEqual(report["unreachable"], [])
        self.assertEqual(report["reachable_count"], 2)

    def test_a_required_edge_with_no_decision_is_unreachable(self):
        report = gold_report(self.data, self.concepts, [
            decision(self.matter, self.solid, "accepted", "containment"),
        ])

        self.assertEqual(report["unreachable"], [["solid", "comparing"]])
        self.assertEqual(report["reachable_count"], 1)

    def test_an_edge_implied_by_the_required_chain_is_correct(self):
        report = gold_report(self.data, self.concepts, [
            decision(self.matter, self.solid, "accepted", "containment"),
            decision(self.matter, self.comparing, "accepted", "definition"),
        ])

        self.assertEqual(report["accepted_precision"], 1.0)

    def test_an_explicitly_forbidden_edge_is_reported(self):
        report = gold_report(self.data, self.concepts, [
            decision(self.figure, self.matter, "accepted", "definition"),
        ])

        self.assertEqual(report["forbidden_accepted"], [["figure", "matter"]])
        self.assertEqual(report["accepted_precision"], 0.0)

    def test_accepted_links_are_counted_by_rule(self):
        report = gold_report(self.data, self.concepts, [
            decision(self.matter, self.solid, "accepted", "containment"),
            decision(self.solid, self.comparing, "accepted"),
        ])

        self.assertEqual(report["accepted_by_rule"], {"containment": 1, "none": 1})

    def test_nothing_accepted_has_no_precision(self):
        self.assertIsNone(gold_report(self.data, self.concepts, [])["accepted_precision"])

    def test_kendall_tau_is_one_in_order_and_minus_one_reversed(self):
        from .services.gold import kendall_tau

        self.assertEqual(kendall_tau(["a", "b", "c"], ["a", "b", "c"]), 1.0)
        self.assertEqual(kendall_tau(["c", "b", "a"], ["a", "b", "c"]), -1.0)
        self.assertIsNone(kendall_tau(["a"], ["a", "b"]))

    def test_the_report_carries_kendall_tau(self):
        report = gold_report(self.data, self.concepts, [])

        self.assertEqual(report["kendall_tau"], 1.0)


class ClueMeasureTests(SimpleTestCase):
    def setUp(self):
        from .testing import concept as make_concept

        self.concepts = [
            make_concept(1, "Stamen", "The anther makes pollen grains. " * 8, key="stamen"),
            make_concept(2, "Pollination", "Pollen travels from an anther to a stigma.", key="pollination"),
            make_concept(3, "Weather", "Clouds bring heavy rain showers today. " * 8, key="weather"),
        ]
        self.data = {"required": [["stamen", "pollination"], ["stamen", "weather"]]}
        self.calibration = {"related_cutoff": 0.1, "meaning_cutoff": 0.3}

    def test_clue_accuracy_counts_votes_on_the_keys_links(self):
        from unittest.mock import patch

        from .services.gold import clue_accuracy
        from .testing import word_vectors

        with patch("learning_path.services.embeddings.embed", word_vectors):
            counts = clue_accuracy(self.data, self.concepts, self.calibration)

        self.assertEqual(counts["terms"]["right"], 1)
        self.assertEqual(counts["terms"]["wrong"], 0)

    def test_gate_loss_lists_key_links_the_gate_blocks(self):
        from unittest.mock import patch

        from .services.gold import gate_loss
        from .testing import word_vectors

        with patch("learning_path.services.embeddings.embed", word_vectors):
            lost = gate_loss(self.data, self.concepts, self.calibration)

        self.assertEqual(lost, [["stamen", "weather"]])


class CoveredTests(SimpleTestCase):
    def setUp(self):
        self.matter, self.solid, self.comparing, self.unkeyed = (
            concept(1, "matter", 0), concept(2, "solid", 1),
            concept(3, "comparing", 2), concept(4, None, 3),
        )
        self.concepts = [self.matter, self.solid, self.comparing, self.unkeyed]
        self.data = {"topic_id": 999, "required": [["matter", "solid"], ["matter", "comparing"]],
                     "parallel": [], "structural": [], "forbidden": [],
                     "expected_order": ["matter", "solid", "comparing"]}

    def test_a_chain_of_accepted_links_covers_a_required_link(self):
        decisions = [decision(self.matter, self.solid, "accepted"), decision(self.solid, self.comparing, "accepted")]

        self.assertEqual(covered_links(self.data, self.concepts, decisions), [["matter", "solid"], ["matter", "comparing"]])

    def test_a_chain_through_an_unkeyed_concept_still_covers(self):
        decisions = [decision(self.matter, self.unkeyed, "accepted"), decision(self.unkeyed, self.comparing, "accepted")]

        self.assertEqual(covered_links(self.data, self.concepts, decisions), [["matter", "comparing"]])

    def test_pending_links_cover_nothing(self):
        decisions = [decision(self.matter, self.solid, "pending")]

        self.assertEqual(covered_links(self.data, self.concepts, decisions), [])

    def test_the_report_carries_the_covered_count(self):
        report = gold_report(self.data, self.concepts, [decision(self.matter, self.solid, "accepted")])

        self.assertEqual(report["covered_count"], 1)

    def test_the_order_only_baseline_links_each_concept_to_the_one_before(self):
        rows = order_only_decisions(self.concepts)

        self.assertEqual([(row["prerequisite"].id, row["dependent"].id) for row in rows], [(1, 2), (2, 3), (3, 4)])
        self.assertEqual({row["verdict"] for row in rows}, {"accepted"})
