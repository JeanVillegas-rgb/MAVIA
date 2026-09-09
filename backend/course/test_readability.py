from django.test import SimpleTestCase

from .readability import compare, flesch_kincaid_grade, syllable_count, text_metrics


SHORT = "Solid has a fixed shape. It holds its form. It does not flow."
LONG = (
    "A solid is a state of matter that maintains a fixed shape and a fixed volume. "
    "The particles inside it are packed tightly together in a regular arrangement. "
    "Because those particles cannot move past one another, a solid does not flow."
)


class SyllableCountTests(SimpleTestCase):
    def test_counts_single_vowel_group_as_one(self):
        self.assertEqual(syllable_count("solid"), 2)

    def test_silent_e_is_not_counted(self):
        self.assertEqual(syllable_count("shape"), 1)

    def test_every_word_has_at_least_one_syllable(self):
        self.assertEqual(syllable_count("rhythm"), 1)

    def test_punctuation_is_ignored(self):
        self.assertEqual(syllable_count("solid,"), 2)


class TextMetricsTests(SimpleTestCase):
    def test_counts_words_and_sentences(self):
        metrics = text_metrics(SHORT)
        self.assertEqual(metrics["words"], 13)
        self.assertEqual(metrics["sentences"], 3)

    def test_empty_text_does_not_divide_by_zero(self):
        metrics = text_metrics("")
        self.assertEqual(metrics["words"], 0)
        self.assertIsInstance(metrics["fk"], float)


class FleschKincaidTests(SimpleTestCase):
    def test_longer_denser_text_scores_higher(self):
        self.assertGreater(flesch_kincaid_grade(LONG), flesch_kincaid_grade(SHORT))


class CompareTests(SimpleTestCase):
    def test_clearly_longer_candidate_is_elaborated_and_confident(self):
        result = compare(SHORT, LONG)
        self.assertEqual(result["slot"], "ELABORATED")
        self.assertTrue(result["confident"])
        self.assertTrue(result["agree"])

    def test_clearly_shorter_candidate_is_simplified_and_confident(self):
        result = compare(LONG, SHORT)
        self.assertEqual(result["slot"], "SIMPLIFIED")
        self.assertTrue(result["confident"])

    def test_near_identical_texts_are_not_confident(self):
        other = "Solid keeps a fixed shape. It holds its form. It will not flow."
        result = compare(SHORT, other)
        self.assertFalse(result["confident"])

    def test_disagreeing_signals_are_never_confident(self):
        # More words, but far shorter sentences and simpler vocabulary:
        # word count says "elaborated", Flesch-Kincaid says "simplified".
        wordy_but_simple = "It is a solid. It has a shape. The shape stays. It is not a gas. It does not flow at all."
        dense_but_short = (
            "Solids demonstrate characteristically incompressible volumetric permanence "
            "alongside structurally invariant morphological configuration."
        )
        result = compare(dense_but_short, wordy_but_simple)
        self.assertFalse(result["agree"])
        self.assertFalse(result["confident"])

    def test_still_proposes_a_slot_when_not_confident(self):
        other = "Solid keeps a fixed shape. It holds its form. It will not flow."
        result = compare(SHORT, other)
        self.assertIn(result["slot"], ("SIMPLIFIED", "ELABORATED"))

    def test_reports_the_margins_it_used(self):
        result = compare(SHORT, LONG)
        self.assertGreater(result["delta_fk"], 0)
        self.assertGreater(result["delta_words"], 0)
        self.assertGreaterEqual(result["ratio"], 1.0)
