"""Pairing a printed question to the concept it asks about."""

import math
import re
import zlib
from unittest.mock import patch

from django.test import TestCase

from lessons.models import (
    CourseGroup,
    LearningMaterial,
    LearningObject,
    LearningObjectGroup,
    OutlineNode,
    Question,
    QuestionLearningObjectLink,
)
from lessons.services import semantic_grouping
from lessons.services.learning_resource_linker import (
    is_empty_prompt,
    refresh_question_learning_object_links,
)

Status = QuestionLearningObjectLink.ReviewStatus
ENCODER = "lessons.services.learning_resource_linker._question_encoder"


class WordVectorEncoder:
    """A deterministic stand-in for the sentence encoder: shared words make
    texts close. Enough to test the decisions built on top of the scores."""

    def supports(self, text):
        return bool((text or "").strip())

    def embeddings(self, texts):
        vectors = []
        for text in texts:
            vector = [0.0] * 64
            for word in re.findall(r"[a-z]+", text.lower()):
                if len(word) > 3:
                    vector[zlib.crc32(word.encode()) % 64] += 1.0
            norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            vectors.append([value / norm for value in vector])
        return vectors


class QuestionPairingTests(TestCase):
    def setUp(self):
        course = CourseGroup.objects.create(title="Biology")
        self.topic = OutlineNode.objects.create(course=course, title="Plants", order=0)
        self.material = LearningMaterial.objects.create(
            course=course, outline_node=self.topic, title="Leaves",
            status=LearningMaterial.Status.COMPLETED,
            generated_json={"learning_objects_confirmed": True},
        )
        self.light = self._concept("Light", [
            "Chlorophyll inside leaves absorbs sunlight energy.",
            "Leaves look green because chlorophyll reflects green light.",
        ])
        self.water = self._concept("Water", [
            "Roots draw water upward through narrow stems.",
        ])

    def _concept(self, label, texts):
        group = LearningObjectGroup.objects.create(outline_node=self.topic, label=label)
        return [
            LearningObject.objects.create(
                material=self.material, kind=LearningObject.Kind.TEXT, group=group,
                title=label, content=text, order=LearningObject.objects.count(),
            )
            for text in texts
        ]

    def _ask(self, prompt):
        return Question.objects.create(material=self.material, prompt=prompt)

    def _link(self, question):
        return QuestionLearningObjectLink.objects.filter(question=question).first()

    def test_a_question_pairs_with_the_concept_that_answers_it(self):
        question = self._ask("Why does chlorophyll make leaves look green?")

        with patch(ENCODER, return_value=WordVectorEncoder()):
            refresh_question_learning_object_links(self.material)

        link = self._link(question)
        self.assertEqual(link.learning_object.group_id, self.light[0].group_id)
        # The concept's best object, not just its first.
        self.assertEqual(link.learning_object, self.light[1])
        self.assertEqual(link.method, "sbert_concept")

    @patch.dict("os.environ", {"QUESTION_PAIR_AUTO_THRESHOLD": "0.10", "QUESTION_PAIR_MINIMUM_MARGIN": "0.90"})
    def test_a_close_call_between_concepts_waits_for_the_teacher(self):
        question = self._ask("How do roots and leaves use water and sunlight?")

        with patch(ENCODER, return_value=WordVectorEncoder()):
            refresh_question_learning_object_links(self.material)

        link = self._link(question)
        self.assertEqual(link.review_status, Status.PENDING_REVIEW)
        self.assertFalse(link.is_primary)

    def test_a_numbering_heading_is_not_paired(self):
        heading = self._ask("Question 3")
        empty = self._ask("   ")

        with patch(ENCODER, return_value=WordVectorEncoder()):
            refresh_question_learning_object_links(self.material)

        self.assertIsNone(self._link(heading))
        self.assertIsNone(self._link(empty))

    def test_a_teacher_decision_is_never_redone(self):
        question = self._ask("Why does chlorophyll make leaves look green?")
        QuestionLearningObjectLink.objects.create(
            question=question, learning_object=self.water[0], is_primary=True,
            review_status=Status.TEACHER_CONFIRMED,
        )

        with patch(ENCODER, return_value=WordVectorEncoder()):
            refresh_question_learning_object_links(self.material)

        self.assertEqual(self._link(question).learning_object, self.water[0])

    def test_without_the_encoder_existing_pairs_are_left_alone(self):
        question = self._ask("Why does chlorophyll make leaves look green?")
        QuestionLearningObjectLink.objects.create(
            question=question, learning_object=self.light[0], is_primary=False,
            review_status=Status.PENDING_REVIEW,
        )

        with patch(ENCODER, side_effect=semantic_grouping.SemanticUnavailable("no model")):
            refresh_question_learning_object_links(self.material)

        self.assertEqual(self._link(question).review_status, Status.PENDING_REVIEW)


class EmptyPromptTests(TestCase):
    def test_numbering_alone_is_empty(self):
        for prompt in ("Question 1", "question 12", "4.", "7)", "", "  ?  "):
            self.assertTrue(is_empty_prompt(prompt), prompt)

    def test_a_numbered_question_is_not_empty(self):
        for prompt in ("1. Why do leaves fall?", "Question 2: What do roots do?", "Name two leaves."):
            self.assertFalse(is_empty_prompt(prompt), prompt)
