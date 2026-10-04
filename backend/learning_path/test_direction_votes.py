"""v7: the block x concept matrix and the three direction votes (v7 spec sections 4-6)."""

from django.test import SimpleTestCase

from .services.concept_text import prepare
from .services.direction_votes import (
    build_block_matrix, cast_votes, centre_order_vote, count_votes, hierarchy_vote, reference_vote,
)
from .services.fusion import ACCEPTED, PENDING
from .testing import concept, member


def three_states():
    """Solid is taught before Matter (the wrong way round); Liquid comes last."""
    return [
        concept(1, "Solid",
                member("A solid is matter with a fixed shape.", order=0),
                member("Solid matter keeps its own volume.", order=1),
                member("Ice is solid matter when it is frozen.", order=2)),
        concept(2, "Matter", member("Matter is everything that takes up space.", order=3)),
        concept(3, "Liquid",
                member("A liquid is matter that flows freely.", order=4),
                member("Liquid matter fills the bottom of a cup.", order=5)),
    ]


class BlockMatrixTests(SimpleTestCase):
    def test_a_concept_is_present_in_its_own_blocks_and_where_its_name_appears(self):
        matrix = build_block_matrix(prepare(three_states()), {})

        self.assertEqual(len(matrix.blocks), 6)
        self.assertEqual(matrix.present[1], (True, True, True, False, False, False))
        self.assertEqual(matrix.present[2], (True,) * 6)
        self.assertEqual(matrix.present[3], (False, False, False, False, True, True))

    def test_two_owned_terms_make_a_concept_present_without_its_name(self):
        lesson = [concept(1, "Stamen", member("The anther makes pollen.", order=0)),
                  concept(2, "Pollination", member("Pollen moves from the anther to the stigma.", order=1))]

        matrix = build_block_matrix(prepare(lesson), {"anther": 1, "pollen": 1})

        self.assertEqual(matrix.present[1], (True, True))

    def test_one_owned_term_is_not_enough(self):
        lesson = [concept(1, "Stamen", member("The anther makes pollen.", order=0)),
                  concept(2, "Pollination", member("Pollen moves from the anther to the stigma.", order=1))]

        matrix = build_block_matrix(prepare(lesson), {"anther": 1})

        self.assertEqual(matrix.present[1], (True, False))

    def test_a_title_that_is_a_sentence_names_nothing(self):
        lesson = [concept(1, "Picking is the simplest method of all the ways to separate things by hand",
                          member("Picking removes big pieces by hand.", order=0)),
                  concept(2, "Sieving", member("Sieving keeps big pieces and picking is slower.", order=1))]

        matrix = build_block_matrix(prepare(lesson), {})

        self.assertEqual(matrix.present[1], (True, False))

    def test_counts_shared_blocks_and_own_blocks(self):
        matrix = build_block_matrix(prepare(three_states()), {})

        self.assertEqual(matrix.count(2), 6)
        self.assertEqual(matrix.together(1, 2), 3)
        self.assertEqual(matrix.own_blocks(3), [4, 5])


def matrix_and_texts(lesson, owners=None):
    texts = prepare(lesson)
    return build_block_matrix(texts, owners or {}), texts


class VoteTests(SimpleTestCase):
    def test_the_broader_concept_wins_the_hierarchy_vote(self):
        matrix, (solid, matter, _) = matrix_and_texts(three_states())

        vote, record = hierarchy_vote(matter, solid, matrix)

        self.assertEqual(vote, 1)
        self.assertEqual(record["from"], "subsumption")
        self.assertEqual((record["p_a_given_b"], record["p_b_given_a"]), (1.0, 0.5))

    def test_the_hierarchy_vote_abstains_below_three_blocks(self):
        matrix, (_, matter, liquid) = matrix_and_texts(three_states())

        vote, record = hierarchy_vote(matter, liquid, matrix)

        self.assertEqual(vote, 0)
        self.assertIsNone(record["p_a_given_b"])

    def test_a_heading_naming_the_other_decides_the_hierarchy_vote(self):
        lesson = [concept(1, "Seeds", member("Seeds grow into plants.", order=0)),
                  concept(2, "Germination", member("A seed sprouts when it is wet.", order=1, section_title="Seeds"))]
        matrix, (seeds, germination) = matrix_and_texts(lesson)

        vote, record = hierarchy_vote(seeds, germination, matrix)

        self.assertEqual((vote, record["from"]), (1, "heading"))

    def test_the_order_vote_reads_each_concepts_teaching_centre(self):
        matrix, (solid, matter, _) = matrix_and_texts(three_states())

        vote, record = centre_order_vote(solid, matter, matrix)

        self.assertEqual(vote, 1)
        self.assertEqual(record["centres"], {"1": [1, 3]})

    def test_the_order_vote_abstains_when_the_pdfs_disagree(self):
        lesson = [concept(1, "Solid", member("A solid keeps its shape.", material_id=1, order=0),
                          member("A solid keeps its shape.", material_id=2, order=5)),
                  concept(2, "Liquid", member("A liquid flows.", material_id=1, order=1),
                          member("A liquid flows.", material_id=2, order=2))]
        matrix, (solid, liquid) = matrix_and_texts(lesson)

        self.assertEqual(centre_order_vote(solid, liquid, matrix)[0], 0)

    def test_the_order_vote_abstains_without_a_shared_pdf(self):
        lesson = [concept(1, "Solid", member("A solid keeps its shape.", material_id=1, order=0)),
                  concept(2, "Liquid", member("A liquid flows.", material_id=2, order=0))]
        matrix, (solid, liquid) = matrix_and_texts(lesson)

        self.assertEqual(centre_order_vote(solid, liquid, matrix), (0, {"centres": {}}))

    def test_the_concept_the_other_refers_to_wins_the_reference_vote(self):
        matrix, (solid, matter, _) = matrix_and_texts(three_states())

        vote, record = reference_vote(matter, solid, matrix)

        self.assertEqual(vote, 1)
        self.assertEqual(record, {"a_refers_b": 0.0, "b_refers_a": 1.0})

    def test_a_concept_mentioning_nothing_makes_every_vote_abstain(self):
        lesson = [concept(1, "Picking is the simplest method of all the ways to separate things by hand",
                          member("Picking removes big pieces by hand.", order=0)),
                  concept(2, "Evaporation leaves the dissolved salt behind when the water dries up",
                          member("Heat the salty water until it is gone.", order=0, material_id=2))]
        matrix, (picking, evaporation) = matrix_and_texts(lesson)

        votes, _ = cast_votes(picking, evaporation, matrix)

        self.assertEqual(votes, {"hierarchy": 0, "order": 0, "reference": 0})


class CountVotesTests(SimpleTestCase):
    """The verdict table, spec section 6."""

    def test_votes_that_agree_are_accepted(self):
        decision = count_votes({"hierarchy": 0, "order": 1, "reference": 1})

        self.assertEqual((decision.verdict, decision.direction, decision.direction_from), (ACCEPTED, 1, "votes_agree"))

    def test_two_votes_outvote_the_order(self):
        decision = count_votes({"hierarchy": -1, "order": 1, "reference": -1})

        self.assertEqual((decision.verdict, decision.direction, decision.direction_from), (ACCEPTED, -1, "outvoted_order"))

    def test_the_order_and_one_vote_outvote_the_third(self):
        decision = count_votes({"hierarchy": 1, "order": 1, "reference": -1})

        self.assertEqual((decision.verdict, decision.direction_from), (ACCEPTED, "majority"))

    def test_the_order_alone_accepts_when_nothing_disagrees(self):
        decision = count_votes({"hierarchy": 0, "order": -1, "reference": 0})

        self.assertEqual((decision.verdict, decision.direction, decision.direction_from), (ACCEPTED, -1, "order_only"))

    def test_another_vote_alone_is_only_a_suggestion(self):
        decision = count_votes({"hierarchy": 0, "order": 0, "reference": -1})

        self.assertEqual((decision.verdict, decision.direction, decision.direction_from), (PENDING, -1, "reference"))

    def test_two_votes_that_disagree_go_to_the_teacher_in_the_lessons_order(self):
        decision = count_votes({"hierarchy": 1, "order": -1, "reference": 0})

        self.assertEqual((decision.verdict, decision.direction, decision.direction_from), (PENDING, -1, "contested"))

    def test_no_votes_go_to_the_teacher_in_the_topics_order(self):
        decision = count_votes({"hierarchy": 0, "order": 0, "reference": 0})

        self.assertEqual((decision.verdict, decision.direction, decision.direction_from), (PENDING, 1, "merged_order"))
