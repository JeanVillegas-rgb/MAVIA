"""Weights, confidence, verdicts and the stored calibration (spec section 7)."""

import json
import math
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from .services.calibration import DEFAULTS, load_calibration
from .services.fusion import (
    ACCEPTED, DECIDING_CLUES, DISAGREE, NONE_SHARED, PARALLEL, PENDING,
    Decision, PairFacts, confidence, learn_weights, reference_verdict,
)


def votes(name=0, terms=0, meaning=0, heading=0, order=0, parallel=False):
    return {"name": name, "terms": terms, "meaning": meaning, "heading": heading, "order": order, "parallel": parallel}


class LearnWeightTests(SimpleTestCase):
    def test_a_clue_that_agrees_with_the_others_weighs_more(self):
        rows = [votes(1, 1, 1)] * 9 + [votes(-1, 1, 1)]

        weights, agreement = learn_weights(rows)

        self.assertAlmostEqual(agreement["terms"], 1.0)
        self.assertAlmostEqual(weights["terms"], math.log(0.95 / 0.05))
        self.assertGreater(weights["terms"], weights["name"])

    def test_a_clue_no_better_than_chance_weighs_nothing(self):
        rows = [votes(1, 1, 1), votes(-1, 1, 1)]

        self.assertEqual(learn_weights(rows)[0]["name"], 0.0)


class ReferenceVerdictTests(SimpleTestCase):
    """Spec section 3: the text decides whether a link exists, the order which way."""

    def test_the_later_concept_referring_to_the_earlier_is_accepted_in_order(self):
        decision = reference_verdict(PairFacts(later_uses_earlier_terms=0.5))

        self.assertEqual(decision, Decision(ACCEPTED, 1, "pdf_order", ()))

    def test_a_name_reference_alone_is_enough(self):
        self.assertEqual(reference_verdict(PairFacts(later_names_earlier=0.2)).verdict, ACCEPTED)

    def test_siblings_get_no_link_even_when_they_refer_to_each_other(self):
        facts = PairFacts(later_names_earlier=0.5, later_uses_earlier_terms=1.0, parallel=True)

        self.assertEqual(reference_verdict(facts).verdict, PARALLEL)

    def test_silent_text_makes_no_link(self):
        self.assertEqual(reference_verdict(PairFacts()).verdict, PARALLEL)

    def test_silent_text_with_files_agreeing_is_only_a_suggestion(self):
        """v5 amendment 2: a process told by the files' order, not by shared words."""
        self.assertEqual(reference_verdict(PairFacts(pdf_agreement=1)), Decision(PENDING, 1, "pdf_agreement", ()))

    def test_siblings_get_no_suggestion_from_the_files_either(self):
        self.assertEqual(reference_verdict(PairFacts(pdf_agreement=1, parallel=True)).verdict, PARALLEL)

    def test_a_heading_decides_the_direction_even_against_the_order(self):
        facts = PairFacts(earlier_names_later=0.5, heading=-1)

        self.assertEqual(reference_verdict(facts), Decision(ACCEPTED, -1, "heading", ()))

    def test_the_earlier_naming_the_later_more_is_a_suggestion_in_order(self):
        """An overview names its parts; the order was right 14 times to 4 (spec section 1)."""
        facts = PairFacts(later_uses_earlier_terms=0.5, earlier_names_later=0.4, later_names_earlier=0.1)

        self.assertEqual(reference_verdict(facts), Decision(PENDING, 1, "pdf_order", ("reverse_name",)))

    def test_only_the_earlier_referring_is_a_suggestion_in_order(self):
        facts = PairFacts(earlier_uses_later_terms=0.5)

        self.assertEqual(reference_verdict(facts), Decision(PENDING, 1, "pdf_order", ("backward_only",)))

    def test_a_figure_follows_the_text_its_description_refers_to(self):
        """Extraction puts a page's figure first, so its position means nothing."""
        facts = PairFacts(earlier_uses_later_terms=0.6, earlier_is_figure=True)

        self.assertEqual(
            reference_verdict(facts),
            Decision(PENDING, -1, "figure", ("figure", "backward_only")),
        )

    def test_a_figure_placed_later_that_refers_back_is_still_only_suggested(self):
        facts = PairFacts(later_uses_earlier_terms=0.6, later_is_figure=True)

        self.assertEqual(reference_verdict(facts), Decision(PENDING, 1, "figure", ("figure",)))

    def test_a_figure_whose_description_refers_to_nothing_follows_the_order(self):
        facts = PairFacts(later_uses_earlier_terms=0.6, earlier_is_figure=True)

        self.assertEqual(reference_verdict(facts), Decision(PENDING, 1, "pdf_order", ("figure",)))

    def test_two_figures_are_suggested_in_order(self):
        facts = PairFacts(later_uses_earlier_terms=0.6, earlier_is_figure=True, later_is_figure=True)

        self.assertEqual(reference_verdict(facts), Decision(PENDING, 1, "pdf_order", ("figure",)))

    def test_concepts_from_different_files_follow_the_name_when_it_points(self):
        facts = PairFacts(later_uses_earlier_terms=0.5, earlier_names_later=0.3, pdf_order=NONE_SHARED)

        self.assertEqual(
            reference_verdict(facts),
            Decision(PENDING, -1, "name", ("reverse_name", "no_shared_pdf")),
        )

    def test_concepts_from_different_files_otherwise_follow_the_merged_order(self):
        facts = PairFacts(later_uses_earlier_terms=0.5, pdf_order=NONE_SHARED)

        self.assertEqual(reference_verdict(facts), Decision(PENDING, 1, "merged_order", ("no_shared_pdf",)))

    def test_files_disagreeing_on_the_order_is_a_suggestion(self):
        facts = PairFacts(later_uses_earlier_terms=0.5, pdf_order=DISAGREE)

        self.assertEqual(reference_verdict(facts), Decision(PENDING, 1, "merged_order", ("pdfs_disagree",)))

    def test_confidence_can_count_only_the_deciding_clues(self):
        votes = {"name": 1, "terms": 1, "meaning": -1, "heading": 0, "order": 0}

        self.assertAlmostEqual(confidence(votes, 1), 2 / 3)
        self.assertAlmostEqual(confidence(votes, 1, DECIDING_CLUES), 1.0)

    def test_confidence_is_the_share_of_voting_clues_that_agree(self):
        self.assertAlmostEqual(confidence(votes(name=1, terms=1, meaning=-1, order=1), 1), 0.75)
        self.assertEqual(confidence(votes(), 1), 0.0)


class CalibrationFileTests(SimpleTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "weights.json"

    def test_a_missing_file_gives_the_defaults(self):
        calibration = load_calibration(self.path)

        self.assertEqual(calibration["weights"], DEFAULTS["weights"])
        self.assertEqual(calibration["source"], "defaults")

    def test_a_malformed_file_gives_the_defaults_and_a_warning(self):
        self.path.write_text("{not json", encoding="utf-8")

        with self.assertLogs("learning_path.services.calibration", level="WARNING"):
            calibration = load_calibration(self.path)

        self.assertEqual(calibration["related_cutoff"], DEFAULTS["related_cutoff"])

    def test_the_defaults_are_never_shared(self):
        load_calibration(self.path)["weights"]["name"] = 0.0

        self.assertEqual(DEFAULTS["weights"]["name"], 1.0)


class CleanerEdgeVerdictTests(SimpleTestCase):
    """v6.1 spec section 3."""

    def test_a_figure_no_longer_blocks_a_link(self):
        facts = PairFacts(later_uses_earlier_terms=0.6, later_is_figure=True)

        self.assertEqual(reference_verdict(facts, cleaner_edges=True), Decision(ACCEPTED, 1, "pdf_order", ()))

    def test_different_pdfs_no_longer_block_a_link(self):
        facts = PairFacts(later_uses_earlier_terms=0.5, pdf_order=NONE_SHARED)

        self.assertEqual(reference_verdict(facts, cleaner_edges=True), Decision(ACCEPTED, 1, "merged_order", ()))

    def test_a_single_shared_word_is_only_a_suggestion(self):
        facts = PairFacts(later_uses_earlier_word=0.5)

        self.assertEqual(
            reference_verdict(facts, cleaner_edges=True),
            Decision(PENDING, 1, "pdf_order", ("weak_terms",)),
        )

    def test_a_single_word_across_disagreeing_pdfs_follows_the_merged_order(self):
        facts = PairFacts(earlier_uses_later_word=0.5, pdf_order=DISAGREE)

        self.assertEqual(
            reference_verdict(facts, cleaner_edges=True),
            Decision(PENDING, 1, "merged_order", ("weak_terms",)),
        )

    def test_siblings_still_get_no_link_even_with_a_shared_word(self):
        facts = PairFacts(later_uses_earlier_word=0.5, parallel=True)

        self.assertEqual(reference_verdict(facts, cleaner_edges=True).verdict, PARALLEL)

    def test_the_other_contradictions_still_hold(self):
        facts = PairFacts(later_uses_earlier_terms=0.5, pdf_order=DISAGREE)

        self.assertEqual(
            reference_verdict(facts, cleaner_edges=True),
            Decision(PENDING, 1, "merged_order", ("pdfs_disagree",)),
        )

    def test_v6_ignores_single_words(self):
        self.assertEqual(reference_verdict(PairFacts(later_uses_earlier_word=0.5)).verdict, PARALLEL)
