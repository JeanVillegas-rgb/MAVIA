"""v6 criteria end to end: references, order, contradictions, evidence (v6 spec section 3)."""

import json

from django.test import SimpleTestCase, TestCase

from lessons.models import CourseGroup, LearningMaterial, LearningObject, LearningObjectGroup, OutlineNode

from .services.concept_units import concepts_for_topic
from .services.criteria import ACCEPTED, CLEANER_EDGES, PENDING, REFERENCE_ORDER, THREE_VOTES, crosses_sections, decide_pairs
from .services.embeddings import EncoderUnavailable
from .test_direction_votes import three_states
from .testing import concept, member, word_vectors

CALIBRATION = {
    "weights": {"name": 1.0, "terms": 1.0, "meaning": 1.0, "heading": 1.0, "order": 0.5},
    "related_cutoff": 0.1,
    "meaning_cutoff": 0.3,
    "source": "test",
}


def flower():
    return [
        concept(1, "Stamen", "The anther makes pollen grains. " * 8),
        concept(2, "Pollination", "Pollen travels from an anther to a stigma."),
        concept(3, "Pistil", "The stigma is sticky and holds the style. " * 8),
    ]


def decide(concepts, embed=word_vectors):
    return {
        (row["prerequisite"].id, row["dependent"].id): row
        for row in decide_pairs(concepts, calibration=CALIBRATION, embed=embed, rule="reference-order")
    }


def no_encoder(sentences):
    raise EncoderUnavailable("offline")


class DecisionTests(SimpleTestCase):
    def test_a_later_concept_using_terms_an_earlier_one_explains_is_accepted(self):
        row = decide(flower())[(1, 2)]

        self.assertEqual(row["verdict"], ACCEPTED)
        self.assertEqual(row["evidence"]["rule"], "reference-order")
        self.assertEqual(row["evidence"]["direction_from"], "pdf_order")
        self.assertEqual(row["evidence"]["contradictions"], [])
        self.assertEqual(row["evidence"]["votes"]["terms"], 1)
        self.assertGreater(row["evidence"]["confidence"], 0)

    def test_only_the_earlier_referring_is_a_suggestion_in_order(self):
        """Pollination (earlier) uses the stigma Pistil explains; Pistil never refers back."""
        row = decide(flower())[(2, 3)]

        self.assertEqual(row["verdict"], PENDING)
        self.assertIn("backward_only", row["evidence"]["contradictions"])

    def test_unrelated_concepts_get_no_link(self):
        decided = decide([concept(1, "Stamen", "The anther makes pollen grains."),
                          concept(2, "Weather", "Clouds bring heavy rain showers.")])

        self.assertEqual(decided, {})

    def test_files_agreeing_while_the_text_is_silent_is_only_a_suggestion(self):
        first = concept(1, "Pollination", member("Pollen moves to the flower top.", material_id=10, order=0),
                        member("Pollen moves to the flower top.", material_id=11, order=0))
        second = concept(2, "Fertilization", member("Egg cells join sperm cells inside.", material_id=10, order=1),
                         member("Egg cells join sperm cells inside.", material_id=11, order=1))

        decided = decide([first, second])

        self.assertEqual({pair: row["verdict"] for pair, row in decided.items()}, {(1, 2): PENDING})
        self.assertEqual(decided[(1, 2)]["evidence"]["direction_from"], "pdf_agreement")

    def test_a_figure_is_suggested_after_the_text_its_description_refers_to(self):
        figure = concept(1, "Particles in a solid", "The picture shows solid particles packed tightly.", kind="image")
        solid = concept(2, "Solid", "A solid has particles packed tightly in rows. " * 4)

        row = decide([figure, solid])[(2, 1)]

        self.assertEqual(row["verdict"], PENDING)
        self.assertEqual(row["evidence"]["direction_from"], "figure")
        self.assertIn("figure", row["evidence"]["contradictions"])

    def test_concepts_from_different_files_are_only_suggested(self):
        stamen = concept(1, "Stamen", member("The anther makes pollen grains. " * 8, material_id=10))
        pollination = concept(2, "Pollination", member("Pollen travels from an anther to a stigma.", material_id=11))
        # A third concept gives term ownership an outside to compare against.
        pistil = concept(3, "Pistil", member("The stigma is sticky and holds the style. " * 8, material_id=12))

        row = decide([stamen, pollination, pistil])[(1, 2)]

        self.assertEqual(row["verdict"], PENDING)
        self.assertEqual(row["evidence"]["contradictions"], ["no_shared_pdf"])
        self.assertEqual(row["evidence"]["direction_from"], "merged_order")

    def test_files_disagreeing_on_the_order_is_only_suggested(self):
        stamen = concept(1, "Stamen", member("The anther makes pollen grains. " * 8, material_id=10, order=0),
                         member("The anther makes pollen grains. " * 8, material_id=11, order=1))
        pollination = concept(2, "Pollination", member("Pollen travels from an anther to a stigma.", material_id=10, order=1),
                              member("Pollen travels from an anther to a stigma.", material_id=11, order=0))
        pistil = concept(3, "Pistil", member("The stigma is sticky and holds the style. " * 8, material_id=10, order=2),
                         member("The stigma is sticky and holds the style. " * 8, material_id=11, order=2))

        row = decide([stamen, pollination, pistil])[(1, 2)]

        self.assertEqual(row["verdict"], PENDING)
        self.assertIn("pdfs_disagree", row["evidence"]["contradictions"])

    def test_an_overview_naming_its_part_is_a_suggestion_in_order(self):
        matter = concept(1, "Matter", "Matter comes as a solid or a liquid in daily life.")
        solid = concept(2, "Solid", "A solid keeps its own shape well.")

        row = decide([matter, solid])[(1, 2)]

        self.assertEqual(row["verdict"], PENDING)
        self.assertIn("reverse_name", row["evidence"]["contradictions"])

    def test_a_heading_naming_the_other_accepts_even_against_the_order(self):
        solid = concept(1, "Solid", member("A solid keeps its own shape well.", section_title="Matter"))
        matter = concept(2, "Matter", "Matter comes as a solid or a liquid in daily life.")

        row = decide([solid, matter])[(2, 1)]

        self.assertEqual(row["verdict"], ACCEPTED)
        self.assertEqual(row["evidence"]["direction_from"], "heading")

    def test_siblings_under_one_heading_get_no_link(self):
        solid = concept(1, "Solid", member("A solid keeps its own shape well.", section_title="States"))
        gas = concept(2, "Gas", member("A gas spreads out more than a solid does.", section_title="States"))

        self.assertEqual(decide([solid, gas]), {})

    def test_without_the_encoder_the_verdicts_are_the_same(self):
        with_encoder = {pair: row["verdict"] for pair, row in decide(flower()).items()}
        offline = decide(flower(), embed=no_encoder)

        self.assertEqual({pair: row["verdict"] for pair, row in offline.items()}, with_encoder)
        self.assertFalse(offline[(1, 2)]["evidence"]["semantic"])
        self.assertIsNone(offline[(1, 2)]["evidence"]["relatedness"])
        for row in offline.values():
            json.dumps(row["evidence"])

    def test_a_concept_with_no_full_sentence_takes_part_in_no_link(self):
        concepts = flower() + [concept(4, "Figure", "Stamen")]

        self.assertFalse(any(4 in pair for pair in decide(concepts)))
        self.assertFalse(any(4 in pair for pair in decide(concepts, embed=no_encoder)))

    def test_a_clue_can_be_left_out_for_an_ablation(self):
        matter = concept(1, "Matter", "Matter is anything with mass and volume.")
        solid = concept(2, "Solid", member("A solid is matter with a fixed shape.", section_title="Matter"))

        rows = decide_pairs([matter, solid], calibration=CALIBRATION, embed=word_vectors, without=("heading",))

        self.assertEqual(rows[0]["evidence"]["votes"]["heading"], 0)
        self.assertEqual(rows[0]["evidence"]["direction_from"], "pdf_order")

    def test_evidence_is_json_serialisable(self):
        for row in decide(flower()).values():
            json.dumps(row["evidence"])

    def test_too_few_concepts_decide_nothing(self):
        self.assertEqual(decide([concept(1, "Matter", "Matter has mass and takes up space.")]), {})


class SectionTests(SimpleTestCase):
    def test_different_headings_cross_sections(self):
        solid = concept(1, "Solid", member("x", section_title="Solids"))
        comparing = concept(2, "Comparing", member("x", section_title="Comparing the Three States"))

        self.assertTrue(crosses_sections(solid, comparing))

    def test_a_concept_without_a_heading_crosses_nothing(self):
        self.assertFalse(crosses_sections(concept(1, "Matter", "x"), concept(2, "Solid", member("x", section_title="Solids"))))


class MemberTextTests(TestCase):
    """Concepts built the way the topic path builds them carry every PDF's text."""

    def test_member_text_joins_every_pdf(self):
        course = CourseGroup.objects.create(title="Grade 1 Science")
        topic = OutlineNode.objects.create(course=course, title="Solid, Liquid and Gas", order=0, depth=0)
        first = LearningMaterial.objects.create(course=course, outline_node=topic, title="A", status="completed")
        second = LearningMaterial.objects.create(course=course, outline_node=topic, title="B", status="completed")
        group = LearningObjectGroup.objects.create(outline_node=topic, label="Solid")
        LearningObject.objects.create(material=first, group=group, title="Solid", content="A solid is matter that keeps its shape.", order=0)
        LearningObject.objects.create(material=second, group=group, title="Solids", content="Solid particles vibrate.", order=0)

        concept_unit = next(c for c in concepts_for_topic(topic) if c.id == group.id)

        self.assertIn("A solid is matter that keeps its shape.", concept_unit.member_text)
        self.assertIn("Solid particles vibrate.", concept_unit.member_text)


def decide_by_votes(concepts):
    return {
        (row["prerequisite"].id, row["dependent"].id): row
        for row in decide_pairs(concepts, calibration=CALIBRATION, embed=word_vectors, rule=THREE_VOTES)
    }


class ThreeVoteTests(SimpleTestCase):
    """v7 spec section 6: the order is one vote of three."""

    def test_the_hierarchy_and_the_references_outvote_a_misplaced_concept(self):
        """Solid is taught before Matter; Matter is broader and Solid refers to it."""
        row = decide_by_votes(three_states())[(2, 1)]

        self.assertEqual(row["verdict"], ACCEPTED)
        self.assertEqual(row["evidence"]["rule"], "three-votes")
        self.assertEqual(row["evidence"]["direction_from"], "outvoted_order")
        self.assertEqual(row["evidence"]["votes"], {"hierarchy": 1, "order": -1, "reference": 1})
        self.assertAlmostEqual(row["evidence"]["confidence"], 2 / 3, places=3)

    def test_votes_and_records_read_prerequisite_first(self):
        row = decide_by_votes(three_states())[(2, 1)]

        hierarchy = row["evidence"]["records"]["hierarchy"]
        self.assertEqual((hierarchy["blocks_a"], hierarchy["blocks_b"]), (6, 3))
        self.assertEqual(row["evidence"]["records"]["reference"], {"a_refers_b": 0.0, "b_refers_a": 1.0})

    def test_the_existence_rule_is_v6s(self):
        """Siblings under one heading naming neither still get no link."""
        lesson = [concept(1, "Solid", member("A solid keeps its shape.", section_title="States", order=0)),
                  concept(2, "Liquid", member("A liquid is not a solid.", section_title="States", order=1))]

        self.assertEqual(decide_by_votes(lesson), {})

    def test_files_agreeing_while_the_text_is_silent_stays_a_v6_suggestion(self):
        lesson = [
            concept(1, "Stamen", member("The anther makes pollen grains.", material_id=1, order=0),
                    member("The anther makes pollen grains.", material_id=2, order=0)),
            concept(2, "Fruit", member("A ripe fruit protects the seeds.", material_id=1, order=1),
                    member("A ripe fruit protects the seeds.", material_id=2, order=1)),
        ]

        row = decide_by_votes(lesson)[(1, 2)]

        self.assertEqual(row["verdict"], PENDING)
        self.assertEqual(row["evidence"]["rule"], "reference-order")

    def test_three_vote_evidence_is_json_serialisable(self):
        rows = decide_pairs(three_states(), calibration=CALIBRATION, embed=word_vectors, rule=THREE_VOTES)

        self.assertTrue(rows)
        json.dumps([row["evidence"] for row in rows])

    def test_the_default_rule_is_still_v6_until_the_final_check(self):
        rows = decide_pairs(three_states(), calibration=CALIBRATION, embed=word_vectors)

        self.assertTrue(all(row["evidence"]["rule"] == "reference-order" for row in rows))


def decide_cleanly(concepts, without=()):
    return {
        (row["prerequisite"].id, row["dependent"].id): row
        for row in decide_pairs(concepts, calibration=CALIBRATION, embed=word_vectors,
                                rule=CLEANER_EDGES, without=without)
    }


class CleanerEdgeTests(SimpleTestCase):
    """v6.1 spec section 3, end to end."""

    def test_two_shared_words_still_link(self):
        row = decide_cleanly(flower())[(1, 2)]

        self.assertEqual(row["verdict"], ACCEPTED)
        self.assertEqual(row["evidence"]["version"], "6.2")
        self.assertEqual(row["evidence"]["records"]["terms"]["use"], 1.0)

    def test_a_single_shared_word_is_only_a_suggestion(self):
        """Pollination uses only "stigma" from Pistil."""
        row = decide_cleanly(flower())[(2, 3)]

        self.assertEqual(row["verdict"], PENDING)
        self.assertEqual(row["evidence"]["contradictions"], ["weak_terms"])
        self.assertEqual(row["evidence"]["records"]["terms"]["owned_back"], ["stigma"])

    def test_a_figure_no_longer_blocks_a_link(self):
        figure = concept(1, "Particles in a solid", "The picture shows solid particles packed tightly.", kind="image")
        solid = concept(2, "Solid", "A solid has particles packed tightly in rows. " * 4)

        row = decide_cleanly([figure, solid])[(1, 2)]

        self.assertEqual((row["verdict"], row["evidence"]["direction_from"]), (ACCEPTED, "pdf_order"))

    def test_different_files_no_longer_block_a_link(self):
        stamen = concept(1, "Stamen", member("The anther makes pollen grains. " * 8, material_id=10))
        pollination = concept(2, "Pollination", member("Pollen travels from an anther to a stigma.", material_id=11))
        pistil = concept(3, "Pistil", member("The stigma is sticky and holds the style. " * 8, material_id=12))

        row = decide_cleanly([stamen, pollination, pistil])[(1, 2)]

        self.assertEqual((row["verdict"], row["evidence"]["direction_from"]), (ACCEPTED, "merged_order"))

    def test_leaving_out_terms_leaves_out_single_words_too(self):
        self.assertNotIn((2, 3), decide_cleanly(flower(), without=("terms",)))

    def test_cleaner_edges_is_the_default(self):
        rows = decide_pairs(flower(), calibration=CALIBRATION, embed=word_vectors)

        self.assertTrue(rows)
        self.assertTrue(all(row["evidence"]["version"] == "6.2" for row in rows))


def reproduction():
    return [
        concept(
            5, "How Flowering Plants Reproduce",
            member("Pollen is carried from the anther to the stigma by wind or insects.", order=1, title="Pollination"),
            member("A pollen grain grows a tube down the style into the ovary.", order=2, title="Fertilization"),
        ),
        concept(6, "Everyday Examples", member(
            "These examples show that pollination and fertilization must happen first.", order=3,
        )),
    ]


class PartNameTests(SimpleTestCase):
    """6.2 end to end: naming a concept's parts links to the concept."""

    def test_naming_its_parts_links_to_the_concept(self):
        row = decide_cleanly(reproduction())[(5, 6)]

        self.assertEqual(row["verdict"], ACCEPTED)
        self.assertEqual(row["evidence"]["records"]["name"]["parts"], ["Pollination", "Fertilization"])

    def test_v6_does_not_read_part_names(self):
        rows = decide_pairs(reproduction(), calibration=CALIBRATION, embed=word_vectors, rule=REFERENCE_ORDER)

        self.assertNotIn((5, 6), {(row["prerequisite"].id, row["dependent"].id) for row in rows})
