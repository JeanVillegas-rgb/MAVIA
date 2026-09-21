from django.test import SimpleTestCase

from lessons.services.semantic_grouping import _GENERIC_INSTRUCTIONAL_LABELS

from .services.concepts import STRUCTURAL_LABELS, is_structural, resolve_concept, strip_numbering


class Chunk:
    """Stand-in for a LearningObject: resolution reads only title and content."""

    def __init__(self, title, content=""):
        self.title = title
        self.content = content


class ConceptResolutionTests(SimpleTestCase):
    def test_a_short_title_is_the_concept(self):
        self.assertEqual(resolve_concept(Chunk("Solid", "A solid keeps its shape.")), "solid")

    def test_part_suffixes_are_stripped_so_splits_share_one_concept(self):
        first = resolve_concept(Chunk("Solid (Part 1 of 3)", "A solid keeps its shape."))
        second = resolve_concept(Chunk("Solid (Part 2 of 3)", "Its particles vibrate."))
        self.assertEqual(first, "solid")
        self.assertEqual(first, second)

    def test_a_sentence_title_falls_back_to_the_defining_subject(self):
        chunk = Chunk(
            "Matter is anything that has mass and occupies space",
            "Matter is anything that has mass and occupies space.",
        )
        self.assertEqual(resolve_concept(chunk), "matter")

    def test_an_example_titled_chunk_owns_no_concept(self):
        """"Ice is a solid" is an instance of a solid, not the concept itself.

        Letting it resolve to `solid` was measured on real data and would make
        every rule that keys on concepts wrong.
        """
        # The body deliberately differs from the title: reading the body first
        # resolved this to `water` on real data.
        chunk = Chunk("Ice is a solid", "Water freezes into ice when it is cold enough.")
        self.assertEqual(resolve_concept(chunk), "ice")

    def test_instructional_labels_own_no_concept(self):
        for label in ("Everyday Examples", "Key Points for Students", "Summary"):
            self.assertIsNone(resolve_concept(Chunk(label, "Some lesson text.")), msg=label)

    def test_a_prose_title_with_no_definition_owns_nothing(self):
        chunk = Chunk(
            "These examples show that the same substance can exist in different states",
            "An ice cube melts into water and then evaporates away.",
        )
        self.assertIsNone(resolve_concept(chunk))

    def test_an_empty_title_owns_nothing(self):
        self.assertIsNone(resolve_concept(Chunk("", "Some text.")))

    def test_resolution_is_case_and_whitespace_insensitive(self):
        self.assertEqual(resolve_concept(Chunk("  GAS  ", "A gas spreads out.")), "gas")

    def test_a_multiword_concept_title_is_kept_whole(self):
        self.assertEqual(
            resolve_concept(Chunk("Particle arrangement", "Particles sit in a pattern.")),
            "particle arrangement",
        )


class Headed(Chunk):
    def __init__(self, title, content="", section_title=""):
        super().__init__(title, content)
        self.section_title = section_title


class NumberingAndStructureTests(SimpleTestCase):
    def test_leading_numbers_are_stripped(self):
        self.assertEqual(strip_numbering("7. Everyday Examples"), "Everyday Examples")
        self.assertEqual(strip_numbering("5) Comparing"), "Comparing")
        self.assertEqual(strip_numbering("Solid"), "Solid")

    def test_a_numbered_generic_label_is_structural_and_unnamed(self):
        """Regression: "7. Everyday Examples" escaped the label check and took
        13 accepted edges on topic 62."""
        chunk = Chunk("7. Everyday Examples", "Ice, water and steam.")
        self.assertTrue(is_structural(chunk))
        self.assertIsNone(resolve_concept(chunk))

    def test_a_six_word_heading_is_a_name(self):
        """"Changing From One State to Another" was unnameable at four words."""
        chunk = Chunk("Changing From One State to Another", "Heat changes states.")
        self.assertEqual(resolve_concept(chunk), "changing from one state to another")

    def test_a_long_title_falls_back_to_its_heading(self):
        chunk = Headed(
            "Here is what the table below shows about every state of matter",
            "It lists shape and volume.",
            section_title="3. Comparing the Three States",
        )
        self.assertEqual(resolve_concept(chunk), "comparing the three states")

    def test_structural_labels_do_not_drift_from_groupings_list(self):
        """STRUCTURAL_LABELS mirrors grouping's `_GENERIC_INSTRUCTIONAL_LABELS`
        by hand rather than by import, so this test is what keeps the two from
        silently drifting apart."""
        for label in _GENERIC_INSTRUCTIONAL_LABELS:
            self.assertIn(label, STRUCTURAL_LABELS, msg=label)

    def test_a_numbered_vocabulary_heading_is_structural(self):
        self.assertTrue(is_structural(Chunk("3. Vocabulary")))
