"""Sentences, terms, names and PDF positions the clues read (spec section 4)."""

from django.test import SimpleTestCase

from .services.concept_text import (
    material_positions,
    name_terms,
    prepare,
    split_sentences,
    terms,
)
from .testing import concept, member, word_vectors


class SentenceTests(SimpleTestCase):
    def test_fragments_under_four_words_are_dropped(self):
        text = "Solid. A solid keeps its shape. Particles vibrate in place."

        self.assertEqual(split_sentences(text), ["A solid keeps its shape.", "Particles vibrate in place."])

    def test_line_breaks_split_sentences(self):
        self.assertEqual(
            split_sentences("Melting turns ice to water\nFreezing turns water to ice"),
            ["Melting turns ice to water", "Freezing turns water to ice"],
        )


class TermTests(SimpleTestCase):
    def test_verb_and_noun_forms_share_a_stem(self):
        self.assertEqual(terms("fertilized"), terms("fertilization"))

    def test_general_stopwords_are_dropped(self):
        self.assertEqual(terms("The pollen is carried to the stigma"), terms("pollen carried stigma"))

    def test_lesson_words_are_not_stopwords(self):
        self.assertEqual(len(terms("example part one")), 3)


class NameTests(SimpleTestCase):
    def test_numbering_and_part_suffix_do_not_count(self):
        self.assertEqual(name_terms("7. Everyday Examples (Part 1 of 2)"), tuple(terms("Everyday Examples")))

    def test_a_sentence_like_title_is_not_a_name(self):
        self.assertEqual(name_terms("Matter usually exists in one of three everyday states"), ())


class PrepareTests(SimpleTestCase):
    def test_sentences_remember_their_pdf_and_get_vectors(self):
        solid = concept(
            1, "Solid",
            member("A solid keeps its shape.", material_id=10),
            member("Solid particles vibrate in place.", material_id=11),
        )

        [text] = prepare([solid], embed=word_vectors)

        self.assertEqual(text.pdfs, [10, 11])
        self.assertEqual(text.vectors.shape[0], 2)
        self.assertEqual(len(text.passages), 2)
        self.assertEqual(text.id, 1)

    def test_spelling_keeps_a_readable_word_per_stem(self):
        [text] = prepare([concept(1, "Stamen", "The anther produces tiny pollen grains.")])

        self.assertEqual(text.spelling[terms("anther")[0]], "anther")

    def test_a_concept_with_no_full_sentence_has_no_vectors_rows(self):
        [text] = prepare([concept(1, "Figure", "Solid")], embed=word_vectors)

        self.assertEqual(text.sentences, [])
        self.assertEqual(text.vectors.shape[0], 0)


class PositionTests(SimpleTestCase):
    def test_positions_are_renumbered_per_pdf(self):
        matter = concept(1, "Matter", member("x", material_id=10, order=4), member("x", material_id=11, order=9))
        solid = concept(2, "Solid", member("x", material_id=10, order=7))

        self.assertEqual(material_positions([solid, matter]), {10: {1: 0, 2: 1}, 11: {1: 0}})
