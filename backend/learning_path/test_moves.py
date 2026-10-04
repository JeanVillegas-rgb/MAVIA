"""Moved lessons for the v7 direction test (v7 spec section 8)."""

from django.test import SimpleTestCase

from .services.criteria import ACCEPTED, PENDING
from .services.gold import move_report, vote_accuracy
from .services.moves import apply_move, load_moves
from .testing import concept, member


def lesson():
    return [
        concept(1, "Matter", member("Matter takes up space.", order=0), key="matter"),
        concept(2, "Solid", member("A solid keeps its shape.", order=1),
                member("A solid is hard.", material_id=2, order=0), key="solid"),
        concept(3, "Summary", member("Matter can be solid.", order=2), key="summary"),
    ]


def orders(concepts):
    return {item.key: [(chunk.material_id, chunk.order) for chunk in item.members] for item in concepts}


class ApplyMoveTests(SimpleTestCase):
    def test_a_concept_moved_to_the_start_comes_first_everywhere(self):
        moved = apply_move(lesson(), {"concepts": ["summary"], "place": "start", "target": None})

        self.assertEqual([item.key for item in moved], ["summary", "matter", "solid"])
        self.assertEqual([item.order for item in moved], [0, 1, 2])
        self.assertLess(orders(moved)["summary"][0][1], 0)

    def test_a_concept_moved_before_a_target_lands_just_before_it(self):
        moved = apply_move(lesson(), {"concepts": ["summary"], "place": "before", "target": "solid"})

        self.assertEqual([item.key for item in moved], ["matter", "summary", "solid"])
        summary_order = orders(moved)["summary"][0][1]
        self.assertTrue(0 < summary_order < 1)

    def test_a_concept_moved_after_a_target_lands_just_after_it(self):
        moved = apply_move(lesson(), {"concepts": ["matter"], "place": "after", "target": "solid"})

        self.assertEqual([item.key for item in moved], ["solid", "matter", "summary"])
        self.assertTrue(1 < orders(moved)["matter"][0][1] < 2)

    def test_a_pdf_without_the_target_leaves_the_concept_in_place(self):
        moved = apply_move(lesson(), {"concepts": ["solid"], "place": "before", "target": "matter"})

        self.assertIn((2, 0), orders(moved)["solid"])

    def test_the_original_lesson_is_untouched(self):
        original = lesson()

        apply_move(original, {"concepts": ["summary"], "place": "start", "target": None})

        self.assertEqual(orders(original)["summary"], [(1, 2)])

    def test_an_unknown_concept_or_target_is_refused(self):
        with self.assertRaises(ValueError):
            apply_move(lesson(), {"concepts": ["gas"], "place": "start", "target": None})
        with self.assertRaises(ValueError):
            apply_move(lesson(), {"concepts": ["summary"], "place": "before", "target": "gas"})

    def test_the_fixture_holds_the_nine_approved_moves(self):
        moves = load_moves()

        self.assertEqual([move["number"] for move in moves], list(range(1, 10)))
        self.assertEqual({move["topic"] for move in moves if move["set"] == "design"}, {340, 357})
        self.assertEqual([move["number"] for move in load_moves(348)], [8, 9])


class MoveReportTests(SimpleTestCase):
    def test_it_scores_the_moved_concepts_links_and_wrong_links_elsewhere(self):
        concepts = lesson()
        matter, solid, summary = concepts
        data = {"topic_id": 1, "required": [["matter", "summary"], ["solid", "summary"], ["matter", "solid"]]}
        decisions = [
            {"prerequisite": matter, "dependent": summary, "verdict": ACCEPTED},
            {"prerequisite": summary, "dependent": solid, "verdict": ACCEPTED},
            {"prerequisite": solid, "dependent": matter, "verdict": ACCEPTED},
        ]

        report = move_report(data, concepts, decisions, {"number": 1, "topic": 1, "concepts": ["summary"]})

        self.assertEqual(report["links"], 2)
        self.assertEqual(report["right_way"], [["matter", "summary"]])
        self.assertEqual(report["wrong_way"], [["solid", "summary"]])
        self.assertEqual(report["wrong_way_elsewhere"], [["solid", "matter"]])

    def test_a_suggested_link_counts_as_pending(self):
        concepts = lesson()
        matter, _, summary = concepts
        data = {"topic_id": 1, "required": [["matter", "summary"]]}
        decisions = [{"prerequisite": matter, "dependent": summary, "verdict": PENDING}]

        report = move_report(data, concepts, decisions, {"number": 1, "topic": 1, "concepts": ["summary"]})

        self.assertEqual(report["pending"], [["matter", "summary"]])
        self.assertEqual(report["missing"], [])


class VoteAccuracyTests(SimpleTestCase):
    def test_each_vote_is_scored_against_the_keys_direction(self):
        """Solid is taught before Matter; the key says Matter comes first."""
        concepts = [
            concept(1, "Solid", member("A solid is matter with a fixed shape.", order=0),
                    member("Solid matter keeps its own volume.", order=1),
                    member("Ice is solid matter when it is frozen.", order=2), key="solid"),
            concept(2, "Matter", member("Matter is everything that takes up space.", order=3), key="matter"),
            concept(3, "Liquid", member("A liquid is matter that flows freely.", order=4),
                    member("Liquid matter fills the bottom of a cup.", order=5), key="liquid"),
        ]

        counts = vote_accuracy({"required": [["matter", "solid"]]}, concepts)

        self.assertEqual(counts["hierarchy"], {"right": 1, "wrong": 0, "silent": 0})
        self.assertEqual(counts["order"], {"right": 0, "wrong": 1, "silent": 0})
        self.assertEqual(counts["reference"], {"right": 1, "wrong": 0, "silent": 0})
