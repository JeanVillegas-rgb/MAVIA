"""Top-down course level: the outline gate and the concept shortlist (course spec 2026-10-02, section 3)."""

from django.test import SimpleTestCase

from .services.concept_text import prepare
from .services.course_shortlist import (
    SHORTLIST_SIZE, concept_score, shortlist, title_vectors, topic_similarities,
)
from .testing import concept, word_vectors


def texts(*concepts):
    return prepare(list(concepts), embed=word_vectors)


class TopicSimilarityTests(SimpleTestCase):
    def test_related_titles_score_higher_than_unrelated_ones(self):
        scores = topic_similarities(["Flower parts", "Flower reproduction", "Weather"], word_vectors)

        self.assertGreater(scores[(0, 1)], scores[(0, 2)])
        self.assertEqual(set(scores), {(0, 1), (0, 2), (1, 2)})


class ShortlistTests(SimpleTestCase):
    def test_names_are_ranked_by_title(self):
        earlier = texts(
            concept(1, "Liquid", "A liquid flows and takes the shape of its container."),
            concept(2, "Solid", "A solid keeps its own shape all the time."),
        )
        [later] = texts(concept(3, "Liquid mixtures", "Some mixtures are made by stirring things into water."))
        vectors = title_vectors(earlier + [later], word_vectors)

        ranked = shortlist(earlier, later, vectors)

        self.assertEqual([(text.id, rank, by) for text, rank, _, by in ranked], [(1, 1, "title"), (2, 2, "title")])

    def test_a_title_that_is_not_a_name_is_ranked_by_text(self):
        earlier = texts(concept(1, "Liquid", "A liquid flows and takes the shape of its container."))
        [later] = texts(concept(3, "Pour the water into the cup and watch how the liquid flows away",
                                "Pour the liquid and watch it flow into the container."))
        vectors = title_vectors(earlier + [later], word_vectors)

        _, ranked_by = concept_score(earlier[0], later, vectors)

        self.assertEqual(ranked_by, "content")

    def test_only_the_closest_few_are_kept(self):
        earlier = texts(*[concept(index, f"Topic word {index}", f"Sentence number {index} about matter.")
                          for index in range(1, 6)])
        [later] = texts(concept(9, "Matter", "Matter takes up space in every form."))
        vectors = title_vectors(earlier + [later], word_vectors)

        self.assertEqual(len(shortlist(earlier, later, vectors)), SHORTLIST_SIZE)

    def test_concepts_without_a_full_sentence_are_not_candidates(self):
        earlier = texts(concept(1, "Liquid", "Flows."), concept(2, "Solid", "A solid keeps its own shape."))
        [later] = texts(concept(3, "Liquid mixtures", "Some mixtures are made by stirring things into water."))
        vectors = title_vectors(earlier + [later], word_vectors)

        self.assertEqual([text.id for text, *_ in shortlist(earlier, later, vectors)], [2])
