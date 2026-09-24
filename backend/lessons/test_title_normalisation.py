"""Heading prefixes a PDF numbers or letters must not split one concept."""

from django.test import SimpleTestCase

from .services.learning_resource_linker import normalize_learning_object_title
from .services.semantic_grouping import _singular_label


def label(title):
    return _singular_label(normalize_learning_object_title(title))


class TitleNormalisationTests(SimpleTestCase):
    def test_lettered_heading_matches_the_same_heading_unnumbered(self):
        """One PDF enumerates "A. Melting"; another simply calls it "Melting".

        Numbered headings were already folded, so a lettered one splitting the
        concept was an inconsistency rather than a decision.
        """
        self.assertEqual(label("A. Melting"), label("Melting"))
        self.assertEqual(label("E. Condensation"), label("Condensation"))
        self.assertEqual(label("b) Freezing"), label("Freezing"))

    def test_numbered_heading_still_matches(self):
        self.assertEqual(label("5. Comparing the Three States"), label("Comparing the Three States"))

    def test_a_leading_article_is_not_an_enumeration(self):
        """"A" opening a sentence-style heading is a word, not a list marker.

        Only the separator tells them apart, so it is required.
        """
        self.assertEqual(label("A Solid Keeps Its Shape"), "a solid keeps its shape")
        self.assertEqual(label("An Everyday Example"), "an everyday example")

    def test_a_single_letter_heading_survives(self):
        """Folding this to nothing would make every such heading match."""
        self.assertEqual(label("A."), "a")
