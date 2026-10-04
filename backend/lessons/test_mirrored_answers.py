"""A mirrored question must keep the answer the generator chose.

A multiple-choice answer arrives as a key into the choice map ("B"), so the
mirror looks it up. A true/false answer is already the answer ("True") and the
map is empty -- but an empty map is still a dict, so the lookup ran anyway,
missed, and fell back to "". Every true/false question reached the teacher
with no answer key at all, and nothing reported it.
"""

import os
from unittest.mock import patch

from django.test import TestCase

from question_generation.models import GeneratedQuestion

from .models import (
    CourseGroup,
    LearningMaterial,
    LearningObject,
    LearningObjectGroup,
    OutlineNode,
    Question,
)
from .services.question_workflow import mirror_generated_questions


@patch.dict(os.environ, {"SEMANTIC_GROUPING_MODE": "legacy"})
class MirroredAnswerTests(TestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Grade 1 Science")
        self.node = OutlineNode.objects.create(
            course=self.course, title="Solid, Liquid and Gas", order=0, depth=0,
        )
        self.material = LearningMaterial.objects.create(
            course=self.course, outline_node=self.node, title="Lesson two",
            status="completed", generated_json={"learning_objects_confirmed": True},
        )
        group = LearningObjectGroup.objects.create(outline_node=self.node, label="Solids")
        self.object = LearningObject.objects.create(
            material=self.material, group=group, title="Solids",
            content="A solid has a fixed shape and a fixed volume.", order=0,
        )

    def mirror(self, **fields):
        generated = GeneratedQuestion.objects.create(
            node=self.object, bloom_level="remember", thinking_order="LOT",
            status="final", **fields,
        )
        mirror_generated_questions(self.object, [generated])
        return Question.objects.get(adaptive_question=generated)

    def test_a_true_false_answer_survives_the_mirror(self):
        question = self.mirror(
            question_text="A solid keeps its shape.",
            question_format="TF",
            correct_answer="True",
            choices=None,
        )

        self.assertEqual(question.correct_answer, "True")
        self.assertIn(question.correct_answer, question.choices)

    def test_a_false_answer_survives_too(self):
        question = self.mirror(
            question_text="A solid takes the shape of its container.",
            question_format="TF",
            correct_answer="False",
            choices=None,
        )

        self.assertEqual(question.correct_answer, "False")

    def test_a_multiple_choice_answer_is_still_resolved_through_its_key(self):
        question = self.mirror(
            question_text="Which state keeps a fixed shape?",
            question_format="MCQ",
            correct_answer="B",
            choices={"A": "Gas", "B": "Solid", "C": "Liquid", "D": "Plasma"},
        )

        self.assertEqual(question.correct_answer, "Solid")
        self.assertIn(question.correct_answer, question.choices)

    def test_a_mirrored_question_never_arrives_without_an_answer(self):
        """The failure was silent, so the invariant is asserted directly."""
        for fmt, answer, choices in (
            ("TF", "True", None),
            ("MCQ", "A", {"A": "Solid", "B": "Liquid"}),
        ):
            question = self.mirror(
                question_text=f"Question for {fmt} {answer}",
                question_format=fmt, correct_answer=answer, choices=choices,
            )
            self.assertTrue(question.correct_answer, f"{fmt} lost its answer")
