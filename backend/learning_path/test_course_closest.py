"""Closest-match course level: the per-concept search and verdict (course spec 2026-10-03, section 3)."""

from django.test import SimpleTestCase

from .services.clues import find_term_owners
from .services.concept_text import prepare
from .services.course_closest import closest_earlier, closest_verdict, own_topic_median
from .services.fusion import ACCEPTED, PENDING
from .testing import concept, word_vectors

NAMED = "Pollen travels from the stamen anther to a stigma."
UNNAMED = "Pollen travels from an anther to a stigma."
ONE_WORD = "The stamen is where the pollen comes from."


def stamen(id=1, title="Stamen"):
    return concept(id, title, "The anther makes pollen grains. " * 8)


def petals():
    return concept(4, "Petals", "Petals attract bees with bright colours. " * 8)


def weather(id=5):
    return concept(id, "Weather", "Clouds bring heavy rain showers today. " * 8)


def pollination(text=NAMED):
    return concept(2, "Pollination", text)


def judge(earlier, later):
    """``{later id: (verdict, closest id, evidence)}``, the earlier and later topic judged together."""
    texts = prepare(earlier + later, embed=word_vectors)
    first, second = texts[:len(earlier)], texts[len(earlier):]
    owners = find_term_owners(texts)
    judged = {}
    for text in second:
        closest, score = closest_earlier(first, text)
        verdict, evidence = closest_verdict(closest, text, score, own_topic_median(text, second), owners)
        judged[text.id] = (verdict, closest.id, evidence)
    return judged


class ClosestEarlierTests(SimpleTestCase):
    def test_the_closest_concept_has_the_most_similar_text(self):
        self.assertEqual(judge([stamen(), petals()], [pollination()])[2][1], 1)

    def test_ties_keep_the_earlier_topics_order(self):
        self.assertEqual(judge([stamen(1, "Stamen"), stamen(8, "Anther")], [pollination()])[2][1], 1)

    def test_no_earlier_concept_with_a_sentence_means_no_closest(self):
        earlier = prepare([concept(1, "Stamen", "Anther.")], embed=word_vectors)
        [later] = prepare([pollination()], embed=word_vectors)

        self.assertEqual(closest_earlier(earlier, later), (None, 0.0))


class ClosestVerdictTests(SimpleTestCase):
    def test_named_and_two_shared_words_is_accepted(self):
        verdict, _, evidence = judge([stamen(), petals()], [pollination()])[2]

        self.assertEqual(verdict, ACCEPTED)
        self.assertEqual(evidence["rule"], "course-closest")
        self.assertEqual(evidence["name_sentences"], 1)
        self.assertEqual(evidence["shared_words"], ["anther", "pollen"])
        self.assertTrue(evidence["confirmed"])
        self.assertFalse(evidence["contradicts_outline"])

    def test_named_with_one_shared_word_is_only_a_suggestion(self):
        verdict, _, evidence = judge([stamen(), petals()], [pollination(ONE_WORD), weather()])[2]

        self.assertEqual(verdict, PENDING)
        self.assertFalse(evidence["confirmed"])
        self.assertGreaterEqual(evidence["margin"], 0.10)

    def test_two_shared_words_without_the_name_is_only_a_suggestion(self):
        verdict, _, _ = judge([stamen(), petals()], [pollination(UNNAMED), weather()])[2]

        self.assertEqual(verdict, PENDING)

    def test_an_earlier_concept_without_a_name_is_never_accepted(self):
        sentence_title = stamen(1, "The anther makes pollen grains for the flower to use later on")

        verdict, closest, evidence = judge([sentence_title, petals()], [pollination(UNNAMED), weather()])[2]

        self.assertEqual((verdict, closest), (PENDING, 1))
        self.assertEqual(evidence["name_sentences"], 0)

    def test_a_concept_no_closer_than_its_own_topic_gets_no_link(self):
        seeds = concept(7, "Seeds", "Pollen on the stigma grows into seeds after an anther drops it.")

        judged = judge([stamen(), petals()], [pollination(UNNAMED), seeds])

        self.assertEqual([judged[2][0], judged[7][0]], [None, None])
        self.assertLess(judged[2][2]["margin"], 0.10)

    def test_an_unrelated_topic_gets_no_link(self):
        wind = concept(6, "Wind", "Strong wind blows across the open fields today.")

        judged = judge([stamen(), petals()], [weather(), wind])

        self.assertEqual([judged[5][0], judged[6][0]], [None, None])

    def test_alone_in_its_topic_only_acceptance_can_link(self):
        verdict, _, evidence = judge([stamen(), petals()], [pollination(UNNAMED)])[2]

        self.assertIsNone(verdict)
        self.assertEqual((evidence["own_median"], evidence["margin"]), (None, None))

    def test_a_concept_with_the_same_title_is_not_named_by_it(self):
        same_title = concept(2, "Stamen", "The stamen anther holds pollen for the bees.")

        verdict, closest, evidence = judge([stamen(), petals()], [same_title])[2]

        self.assertEqual((verdict, closest), (None, 1))
        self.assertEqual(evidence["name_sentences"], 0)
