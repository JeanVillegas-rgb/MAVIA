"""The three criteria, their thresholds, and the veto over their verdicts.

These exist because the module had no coverage at all while carrying the entire
edge decision. Each test below pins a rule that a measurement on real content
showed to be load-bearing -- the docstrings say which, so a later change that
trips one of them can tell whether it is breaking a deliberate choice or an
accident.
"""

import math

from django.test import TestCase

from lessons.models import (
    CourseGroup,
    LearningMaterial,
    LearningObject,
    LearningObjectGroup,
    OutlineNode,
)

from .services import criteria
from .services.concept_units import concepts_for_topic
from .services.criteria import (
    ACCEPTED,
    MAX_IOL,
    MIN_IOL_MARGIN,
    PENDING,
    WINDOW_SIZE,
    _windows,
    cast_votes,
    concept_names,
    crosses_sections,
    decide,
    decide_pairs,
    head_words,
    inbound_outbound,
    inbound_outbound_ratios,
    key_terms,
    only_contrastive_mentions,
    reference_details,
    reference_matrix,
    semantic_reference,
    temporal_order,
)
from .services.text_signals import normalize


class Stub:
    """The learning-object surface the criteria actually read."""

    def __init__(self, id, order=0, title="", content=""):
        self.id = id
        self.order = order
        self.title = title
        self.content = content
        self.section_title = ""
        self.kind = "text"


def votes(temporal=0, semantic=0, ratio=0):
    """A vote tally in the shape ``decide`` expects."""
    return {
        "temporal_order": temporal,
        "semantic_reference": semantic,
        "inbound_outbound": ratio,
    }


class WindowTests(TestCase):
    def test_short_text_is_one_window(self):
        self.assertEqual(_windows("a solid keeps its shape"), ["a solid keeps its shape"])

    def test_empty_text_has_no_windows(self):
        self.assertEqual(_windows(""), [])

    def test_long_text_slides_by_one_word(self):
        text = " ".join(str(number) for number in range(WINDOW_SIZE + 2))
        result = _windows(text)

        self.assertEqual(len(result), 3)
        self.assertEqual(result[0].split()[0], "0")
        self.assertEqual(result[1].split()[0], "1")

    def test_a_very_long_concept_is_capped(self):
        """Runtime is O(windows x concepts), and a long passage's later windows
        repeat its subject, so the cap costs nothing it was measuring."""
        text = " ".join(str(number) for number in range(2000))

        self.assertLessEqual(len(_windows(text)), criteria.MAX_WINDOWS_PER_CONCEPT)


class ConceptNameTests(TestCase):
    def test_a_structural_label_names_nothing(self):
        names = concept_names([Stub(1, title="Examples"), Stub(2, title="Solid")])

        self.assertIsNone(names[1])
        self.assertEqual(names[2], "solid")


class TemporalOrderTests(TestCase):
    def test_the_earlier_concept_votes_yes(self):
        self.assertEqual(temporal_order(Stub(1, order=0), Stub(2, order=5)), 1)

    def test_the_later_concept_votes_no(self):
        self.assertEqual(temporal_order(Stub(1, order=5), Stub(2, order=0)), 0)


class SemanticReferenceTests(TestCase):
    def setUp(self):
        self.a, self.b = Stub(1), Stub(2)

    def test_the_more_referenced_concept_votes_yes(self):
        matrix = {(1, 2): 0.40, (2, 1): 0.10}

        self.assertEqual(semantic_reference(self.a, self.b, matrix), 1)

    def test_the_less_referenced_concept_votes_no(self):
        matrix = {(1, 2): 0.10, (2, 1): 0.40}

        self.assertEqual(semantic_reference(self.a, self.b, matrix), 0)

    def test_an_unnameable_concept_casts_no_vote(self):
        """Regression, measured: a concept with no name -- "Examples",
        "Everyday Examples" -- has no row in the matrix, so its side of the
        comparison is structurally 0.000. Comparing a measured number against
        one that could never be measured is not evidence of direction; it made
        every nameless concept a dependent of nearly everything.
        """
        only_forward = {(1, 2): 0.40}

        self.assertEqual(semantic_reference(self.a, self.b, only_forward), 0)

    def test_a_measured_zero_still_counts_against_a_measured_score(self):
        """A present-but-zero row is a measurement, not an absence, so it must
        not be confused with the unnameable case above."""
        matrix = {(1, 2): 0.40, (2, 1): 0.0}

        self.assertEqual(semantic_reference(self.a, self.b, matrix), 1)

    def test_two_measured_zeroes_cast_no_vote(self):
        matrix = {(1, 2): 0.0, (2, 1): 0.0}

        self.assertEqual(semantic_reference(self.a, self.b, matrix), 0)


class InboundOutboundTests(TestCase):
    def setUp(self):
        self.a, self.b = Stub(1), Stub(2)

    def test_a_clearly_more_foundational_concept_votes_yes(self):
        self.assertEqual(inbound_outbound(self.a, self.b, {1: 2.0, 2: 1.0}), 1)

    def test_a_narrow_lead_casts_no_vote(self):
        """Regression, measured: the ratios sat between 1.60 and 2.12, so a bare
        `>` let 2.001 beating 2.000 cast a full vote on nearly every pair. That
        is what filled the review queue."""
        just_under = 2.0 * (1.0 + MIN_IOL_MARGIN) - 0.01

        self.assertEqual(inbound_outbound(self.a, self.b, {1: just_under, 2: 2.0}), 0)

    def test_the_margin_is_relative_not_absolute(self):
        """The ratio is scale-free: being half again as foundational is what
        matters, not being 0.4 higher."""
        self.assertEqual(inbound_outbound(self.a, self.b, {1: 10.4, 2: 10.0}), 0)

    def test_a_missing_ratio_never_wins(self):
        self.assertEqual(inbound_outbound(self.a, self.b, {2: 1.0}), 0)

    def test_an_unnameable_concept_casts_no_vote_in_either_direction(self):
        """Regression, measured: an unnamed concept used to score 0, so every
        named concept beat it and 55 verdicts rested on nothing else."""
        ratios = {1: 2.0}  # concept 2 has no name, so it has no ratio

        self.assertEqual(inbound_outbound(self.a, self.b, ratios), 0)
        self.assertEqual(inbound_outbound(self.b, self.a, ratios), 0)


class RatioTests(TestCase):
    def test_a_concept_referring_to_nothing_is_capped_not_infinite(self):
        """`inf > inf` is False, which would silently drop such a pair."""
        concepts = [Stub(1), Stub(2)]
        # Concept 1 is referred to and refers to nothing, so its denominator
        # falls back to MIN_OUTBOUND and the raw quotient overshoots the cap.
        ratios = inbound_outbound_ratios(concepts, {(1, 2): 2.0, (2, 1): 0.0})

        self.assertEqual(ratios[1], MAX_IOL)
        self.assertEqual(ratios[2], 0.0)

    def test_an_unnameable_concept_gets_no_ratio(self):
        """No name means nothing can refer to it, so its inbound is zero by
        construction. Only concepts with a matrix row are measured."""
        ratios = inbound_outbound_ratios([Stub(1), Stub(2)], {(1, 2): 2.0})

        self.assertIn(1, ratios)
        self.assertNotIn(2, ratios)


class DecisionTests(TestCase):
    def test_all_three_criteria_accept(self):
        self.assertEqual(decide(votes(1, 1, 1)), ACCEPTED)

    def test_two_of_three_goes_to_the_teacher(self):
        self.assertEqual(decide(votes(1, 1, 0)), PENDING)

    def test_two_content_criteria_without_position_still_pend(self):
        self.assertEqual(decide(votes(0, 1, 1)), PENDING)

    def test_position_alone_never_creates_an_edge(self):
        """The density guard. Temporal order votes on one direction of *every*
        pair, so without this the middle band fills with pairs whose only
        evidence is that one paragraph came first -- document order, not
        dependency."""
        self.assertIsNone(decide(votes(1, 0, 0)))

    def test_no_evidence_decides_nothing(self):
        self.assertIsNone(decide(votes(0, 0, 0)))


class CastVoteTests(TestCase):
    def test_the_numbers_behind_each_vote_are_recorded(self):
        """Moving to a margin-based score later needs these and nothing else."""
        result = cast_votes(
            Stub(1, order=0), Stub(2, order=1),
            {(1, 2): 0.4, (2, 1): 0.1}, {1: 2.0, 2: 1.0},
        )

        # Renamed 2026-09-17: reference is RefD key-term share, not SBERT cosine.
        self.assertEqual(result["ref_forward"], 0.4)
        self.assertEqual(result["ref_backward"], 0.1)
        self.assertAlmostEqual(result["ref_margin"], 0.3)
        self.assertEqual(result["iol_prerequisite"], 2.0)
        self.assertEqual(result["iol_dependent"], 1.0)


VOCAB = ("matter", "solid", "liquid", "gas")


class KeywordRuntime:
    """A deterministic stand-in for the sentence encoder.

    Each text becomes a unit vector over the words it contains, so the cosines
    the criteria read are predictable from the fixture's wording. Loading the
    real encoder would make these tests slow and their outcomes opaque.
    """

    def embeddings(self, payload):
        return [self._vector(text) for text in payload]

    def _vector(self, text):
        words = set(normalize(text).split())
        vector = [1.0 if term in words else 0.0 for term in VOCAB]
        # A neutral axis, so text containing none of the vocabulary is still a
        # unit vector and simply sits orthogonal to everything else.
        vector.append(0.0 if any(vector) else 1.0)
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


class ReferenceMatrixTests(TestCase):
    def test_reference_is_directional(self):
        """B's text naming A is not the same as A's text naming B, and the
        whole criterion rests on telling those apart."""
        matter = Stub(1, title="Matter", content="Everything around us has mass.")
        solid = Stub(2, title="Solid", content="A solid is matter that keeps its shape.")

        matrix = reference_matrix([matter, solid], KeywordRuntime())

        self.assertGreater(matrix[(1, 2)], 0.0)
        self.assertEqual(matrix[(2, 1)], 0.0)

    def test_a_concept_owning_no_key_terms_gets_no_row(self):
        """A concept with no name and no distinctive term cannot be referred
        to. This absence is what `semantic_reference` reads to abstain."""
        # Changed 2026-09-17: was test_an_unnameable_concept_gets_no_row. Rows
        # now follow key-term ownership, not names; "Ice and steam." owns "ice"
        # and "steam", so this concept uses only a term Solid shares.
        examples = Stub(1, title="Examples", content="Its shape.")
        solid = Stub(2, title="Solid", content="A solid keeps its shape.")

        matrix = reference_matrix([examples, solid], KeywordRuntime())

        self.assertNotIn((1, 2), matrix)
        self.assertIn((2, 1), matrix)

    def test_a_nameless_concept_owning_a_distinctive_term_gets_a_row(self):
        """Key terms are the name plus distinctive terms; no name is required.
        A long sentence title names nothing, yet its unique term is matched
        when a later concept's text uses it. Six concepts, so a term used by two
        still counts as distinctive."""
        concepts = [
            Stub(1, order=0,
                 title="Tiny moving particles called atoms fill all the matter around us",
                 content="Tiny atoms fill everything around us."),
            Stub(2, order=1, title="Solid", content="A solid keeps atoms packed tightly."),
            Stub(3, order=2, title="Liquid", content="A liquid flows into a container."),
            Stub(4, order=3, title="Gas", content="A gas spreads out to fill a room."),
            Stub(5, order=4, title="Melting", content="Heat turns ice into water."),
            Stub(6, order=5, title="Freezing", content="Cold turns water into ice."),
        ]

        self.assertIsNone(concept_names(concepts)[1])
        matrix, matched = reference_details(concepts, KeywordRuntime())

        self.assertIn((1, 2), matrix)
        self.assertIn("atom", matched[(1, 2)])
        self.assertGreater(matrix[(1, 2)], 0.0)


class ContrastTests(TestCase):
    def test_a_mention_only_after_a_contrast_marker_is_contrastive(self):
        self.assertTrue(only_contrastive_mentions("solid", "A gas spreads out, unlike a solid."))

    def test_contrast_is_scoped_to_the_clause_not_the_sentence(self):
        """Liquids and gases are what this sentence is about; only solids are
        contrasted."""
        text = "Liquids and gases can flow, while solids normally do not."

        self.assertFalse(only_contrastive_mentions("liquid", text))
        self.assertTrue(only_contrastive_mentions("solid", text))

    def test_one_plain_mention_is_enough_to_count(self):
        text = "A solid keeps its shape. Unlike a solid, a gas spreads out."

        self.assertFalse(only_contrastive_mentions("solid", text))

    def test_no_mention_is_not_a_contrast(self):
        self.assertFalse(only_contrastive_mentions("solid", "A gas spreads out."))


class SectionTests(TestCase):
    def _stub(self, id, section):
        stub = Stub(id)
        stub.section_title = section
        return stub

    def test_different_headings_cross_sections(self):
        self.assertTrue(crosses_sections(self._stub(1, "Solids"), self._stub(2, "Gases")))

    def test_the_same_heading_does_not(self):
        self.assertFalse(crosses_sections(self._stub(1, "Solids"), self._stub(2, " solids ")))

    def test_a_concept_without_a_heading_crosses_nothing(self):
        """A comparison at the end of a lesson, or the opening definition, sits
        under no section and legitimately builds on, or underpins, all of them."""
        self.assertFalse(crosses_sections(self._stub(1, "Solids"), self._stub(2, "")))
        self.assertFalse(crosses_sections(self._stub(1, ""), self._stub(2, "Gases")))


class DecidePairTests(TestCase):
    """The vetoes, over concepts built the way the topic path builds them."""

    def setUp(self):
        self.course = CourseGroup.objects.create(title="Grade 1 Science")
        self.module = OutlineNode.objects.create(
            course=self.course, title="Properties of Matter", order=0, depth=0
        )
        self.topic = OutlineNode.objects.create(
            course=self.course, parent=self.module,
            title="Solid, Liquid and Gas", order=0, depth=1,
        )
        self.material = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic,
            title="States", status="completed",
        )
        self._concept("Matter", "Everything around us has mass.", 0)
        self._concept("Solid", "A solid is matter that keeps its shape.", 1)
        self._concept("Gas", "A gas spreads out, unlike a solid.", 2)
        # Shares title words with the topic ("Solid, Liquid and Gas"), which the
        # removed sibling rule would have used to delete its edges.
        self._concept("Liquid", "A liquid flows, and it is a solid that melted.", 3)

        self.concepts = concepts_for_topic(self.topic)
        self.by_title = {concept.title: concept for concept in self.concepts}

    def _raw_verdict(self, prerequisite, dependent, concepts=None):
        concepts = concepts or self.concepts
        matrix = reference_matrix(concepts, KeywordRuntime())
        ratios = inbound_outbound_ratios(concepts, matrix)
        return decide(cast_votes(prerequisite, dependent, matrix, ratios))

    def _concept(self, title, content, order, section=""):
        group = LearningObjectGroup.objects.create(outline_node=self.topic, label=title)
        LearningObject.objects.create(
            material=self.material, group=group, title=title,
            content=content, order=order, section_title=section,
        )
        return group

    def _decisions(self, concepts=None):
        return {
            (row["prerequisite"].id, row["dependent"].id): row["verdict"]
            for row in decide_pairs(concepts or self.concepts, KeywordRuntime())
        }

    def _pair(self, prerequisite, dependent):
        return (self.by_title[prerequisite].id, self.by_title[dependent].id)

    def test_a_pair_mentioned_only_in_contrast_is_vetoed(self):
        """"A gas spreads out, unlike a solid" is not built on solids. The
        embedding cannot tell that apart from a real reference, so the numbers
        alone would keep the pair."""
        solid, gas = self.by_title["Solid"], self.by_title["Gas"]

        self.assertIsNotNone(self._raw_verdict(solid, gas))
        self.assertNotIn(self._pair("Solid", "Gas"), self._decisions())

    def test_sharing_words_with_the_topic_title_no_longer_vetoes_a_pair(self):
        """Regression for the removed sibling rule: whether an edge existed
        depended on how objects happened to be titled."""
        solid, liquid = self.by_title["Solid"], self.by_title["Liquid"]

        self.assertIsNotNone(self._raw_verdict(solid, liquid))
        self.assertIn(self._pair("Solid", "Liquid"), self._decisions())

    def test_two_concepts_with_the_same_name_are_vetoed(self):
        # Changed 2026-09-17: under RefD two concepts named "solid" refer to each
        # other through the name alone (a tie), so the raw verdict was None and
        # the veto untested. The second Solid now builds on the first's wording.
        self._concept("Solid", "A solid keeps its shape because it is rigid.", 4)
        concepts = concepts_for_topic(self.topic)
        first, second = [concept for concept in concepts if concept.title == "Solid"]

        self.assertIsNotNone(self._raw_verdict(first, second, concepts))
        self.assertNotIn((first.id, second.id), self._decisions(concepts))

    def test_a_real_dependency_survives_the_vetoes(self):
        self.assertIn(self._pair("Matter", "Solid"), self._decisions())

    def _verdicts_with_sections(self, solid_section, liquid_section):
        solid_id, liquid_id = self.by_title["Solid"].id, self.by_title["Liquid"].id
        LearningObject.objects.filter(title="Solid").update(section_title=solid_section)
        LearningObject.objects.filter(title="Liquid").update(section_title=liquid_section)
        concepts = concepts_for_topic(self.topic)
        by_id = {concept.id: concept for concept in concepts}
        rows = {
            (row["prerequisite"].id, row["dependent"].id): row
            for row in decide_pairs(concepts, KeywordRuntime())
        }
        return rows[(solid_id, liquid_id)], by_id

    def test_crossing_sections_is_recorded_but_no_longer_caps_the_verdict(self):
        """Changed 2026-09-17: the cap's evidence (35 wrong cross-section edges)
        came from per-state Examples and diagrams, now excluded or merged; the
        teacher's gold edge Solid -> Comparing crosses sections."""
        same, _ = self._verdicts_with_sections("States", "States")
        crossing, _ = self._verdicts_with_sections("Solids", "Liquids")

        self.assertFalse(same["cross_section"])
        self.assertTrue(crossing["cross_section"])
        self.assertEqual(crossing["verdict"], same["verdict"])

    def test_a_concept_is_never_its_own_prerequisite(self):
        for (prerequisite, dependent) in self._decisions():
            self.assertNotEqual(prerequisite, dependent)

    def test_too_few_concepts_decide_nothing(self):
        self.assertEqual(decide_pairs(self.concepts[:1], KeywordRuntime()), [])

    def test_structural_concepts_take_part_in_no_pair(self):
        examples = self._concept("7. Everyday Examples", "Matter, a solid, a liquid and a gas.", 5)
        concepts = concepts_for_topic(self.topic)

        decisions = decide_pairs(concepts, KeywordRuntime())

        self.assertFalse(any(
            examples.id in (row["prerequisite"].id, row["dependent"].id) for row in decisions
        ))

    def test_member_text_joins_every_pdf(self):
        other = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="Other", status="completed",
        )
        solid = self.by_title["Solid"]
        LearningObject.objects.create(
            material=other, group=solid.group, title="Solids",
            content="Solid particles vibrate.", order=0,
        )
        concept = next(c for c in concepts_for_topic(self.topic) if c.id == solid.id)

        self.assertIn("A solid is matter that keeps its shape.", concept.member_text)
        self.assertIn("Solid particles vibrate.", concept.member_text)


def plant_concepts():
    """Stamen, Pistil and Pollination among enough neighbours that their terms
    count as distinctive (six concepts: a term may appear in two)."""
    return [
        Stub(1, order=0, title="Stamen", content="The stamen has an anther and a filament. The anther makes pollen."),
        Stub(2, order=1, title="Pistil", content="The pistil has a stigma, a style and an ovary."),
        Stub(3, order=2, title="Pollination", content="Pollen moves from the anther to the stigma."),
        Stub(4, order=3, title="Petals", content="Petals attract bees with colour."),
        Stub(5, order=4, title="Seed", content="A seed holds a tiny plant and stored food."),
        Stub(6, order=5, title="Fruit", content="A fruit protects seeds and helps spread them."),
    ]


class KeyTermTests(TestCase):
    def test_a_concept_owns_the_terms_it_introduces(self):
        terms = key_terms(plant_concepts())

        self.assertIn("anther", terms[1])
        self.assertIn("pollen", terms[1])
        self.assertIn("stigma", terms[2])
        self.assertIn("stamen", terms[1])

    def test_terms_every_concept_uses_belong_to_none(self):
        concepts = [
            Stub(index, order=index, title=title, content=f"{title} is made of particles.")
            for index, title in enumerate(("Solid", "Liquid", "Gas", "Plasma"), start=1)
        ]

        terms = key_terms(concepts)

        self.assertFalse(any("particle" in weights for weights in terms.values()))


class RefDReferenceTests(TestCase):
    def test_using_a_concepts_terms_is_a_reference_to_it(self):
        """Regression, topic 79: Stamen -> Pollination scored backwards under
        name-to-window similarity (0.23 forward, 0.32 backward)."""
        concepts = plant_concepts()
        stamen, pollination = concepts[0], concepts[2]

        matrix, matched = reference_details(concepts, KeywordRuntime())

        self.assertGreater(matrix[(1, 3)], matrix[(3, 1)])
        self.assertIn("anther", matched[(1, 3)])
        self.assertEqual(semantic_reference(stamen, pollination, matrix), 1)
        self.assertEqual(semantic_reference(pollination, stamen, matrix), 0)

    def test_a_comparison_is_not_referenced_by_its_parts(self):
        """Regression, topic 62: every comparative sentence looked like a
        reference to "Comparing the Three States"."""
        shape = Stub(1, order=0, title="Shape", content="Solids keep their shape; liquids take the shape of the container.")
        comparing = Stub(2, order=1, title="Comparing the Three States", content="The table lists shape, volume and flow for each state.")

        matrix, _ = reference_details([shape, comparing], KeywordRuntime())

        self.assertEqual(semantic_reference(comparing, shape, matrix), 0)


class ThanContrastTests(TestCase):
    def test_a_comparison_with_than_is_contrastive(self):
        self.assertTrue(only_contrastive_mentions("solid", "Particles have more energy than in a solid."))


class MemberTextContrastTests(TestCase):
    def test_a_contrast_only_in_another_pdf_is_still_vetoed(self):
        """Criteria read every member's text (Task 5 review ruling): a mention
        that exists only in a second PDF's wording is judged there too."""
        solid = Stub(1, order=0, title="Solid", content="A solid keeps its shape.")
        gas = Stub(2, order=1, title="Gas", content="A gas spreads out.")
        solid.member_text = solid.content
        gas.member_text = "A gas spreads out. A gas fills its container, unlike a solid."

        self.assertTrue(criteria.vetoed(solid, gas, concept_names([solid, gas])))


class Headed:
    """A stub concept whose members carry section headings."""

    def __init__(self, id, order, title, content, sections=("",)):
        self.id = id
        self.order = order
        self.title = title
        self.content = content
        self.member_text = content
        self.section_title = sections[0]
        self.kind = "text"
        self.members = tuple(
            type("Member", (), {"section_title": section, "title": title, "content": content})()
            for section in sections
        )


class LiteralRuntime:
    """An encoder stand-in under which no text paraphrases a name.

    KeywordRuntime puts every text without its vocabulary on one neutral axis,
    so "seed formation" and "fruit formation" would each paraphrase the other's
    text and hide the head-word evidence these tests measure.
    """

    def embeddings(self, payload):
        return [[0.0] for _ in payload]


class HeadWordTests(TestCase):
    def test_the_head_word_is_the_first_significant_word(self):
        names = {1: "seed formation", 2: "stamen male part", 3: "solid"}

        self.assertEqual(head_words(names), {1: "seed", 2: "stamen"})

    def test_a_shared_head_word_is_ambiguous(self):
        names = {1: "seed formation", 2: "seed dispersal"}

        self.assertEqual(head_words(names), {})

    def test_saying_the_head_word_refers_to_a_multi_word_concept(self):
        """Regression, topic 79: Fruit says "seed", never "seed formation"."""
        seed = Stub(1, order=0, title="Seed formation", content="The ovule becomes a seed with stored food.")
        fruit = Stub(2, order=1, title="Fruit formation", content="The ovary grows around the seed and ripens.")
        others = [
            Stub(3, order=2, title="Petals", content="Petals attract bees with colour."),
            Stub(4, order=3, title="Roots", content="Roots take in water from soil."),
        ]

        matrix, matched = reference_details([seed, fruit, *others], LiteralRuntime())

        self.assertIn("head:seed", matched[(1, 2)])
        self.assertEqual(semantic_reference(seed, fruit, matrix), 1)


    def test_a_single_word_name_makes_its_word_ambiguous(self):
        names = {1: "seed", 2: "seed dispersal"}

        self.assertEqual(head_words(names), {})

    def test_an_ambiguous_head_word_adds_no_reference(self):
        """Beside a "Seed" concept, saying "seed" refers to Seed, not to Seed dispersal."""
        seed = Stub(1, order=0, title="Seed", content="The ovule becomes a seed with stored food.")
        dispersal = Stub(2, order=1, title="Seed dispersal", content="Wind and animals carry it far away.")
        fruit = Stub(3, order=2, title="Fruit", content="The ovary grows around the seed and ripens.")
        others = [
            Stub(4, order=3, title="Petals", content="Petals attract bees with colour."),
            Stub(5, order=4, title="Roots", content="Roots take in water from soil."),
        ]

        _, matched = reference_details([seed, dispersal, fruit, *others], LiteralRuntime())

        self.assertFalse(any(
            term.startswith("head:")
            for (target_id, _), terms in matched.items() if target_id == 2
            for term in terms
        ))

class SectionContainmentTests(TestCase):
    def test_a_concept_under_anothers_heading_refers_to_it(self):
        """Regression, topic 62: Solid sits under the "Matter" heading but never
        says "matter"; the overview names solid, so RefD read it backwards."""
        matter = Headed(1, 0, "Matter", "Matter has mass. It can be a solid, a liquid or a gas.", ("Matter",))
        solid = Headed(2, 1, "Solid", "A solid keeps its shape.", ("Matter", "Solids"))
        others = [
            Headed(3, 2, "Roots", "Roots take in water."),
            Headed(4, 3, "Leaves", "Leaves make food."),
        ]

        matrix, matched = reference_details([matter, solid, *others], KeywordRuntime())

        self.assertEqual(matrix[(1, 2)], 1.0)
        self.assertEqual(matched[(1, 2)], ["section:matter"])
        self.assertEqual(semantic_reference(matter, solid, matrix), 1)
        self.assertEqual(semantic_reference(solid, matter, matrix), 0)

    def test_a_concept_never_contains_itself(self):
        matter = Headed(1, 0, "Matter", "Matter has mass.", ("Matter",))
        roots = Headed(2, 1, "Roots", "Roots take in water.")

        matrix, matched = reference_details([matter, roots], KeywordRuntime())

        self.assertNotIn((1, 1), matrix)
        self.assertFalse(any(term.startswith("section:") for terms in matched.values() for term in terms))
