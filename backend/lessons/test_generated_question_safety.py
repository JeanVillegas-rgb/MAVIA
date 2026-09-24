"""Deleting a concept must only delete the questions generated from it.

Regression for a data-loss bug: deleting one learning object re-ran question
pairing for the whole file. Pairing re-guessed every generated question by
word overlap, most scored below the confirmation threshold, and their
learner-facing copies were deleted -- 56 questions across 11 concepts, from one
delete. Three other paths could wipe other concepts' questions the same way;
each has a test here.
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
    QuestionLearningObjectLink,
)
from .services.learning_resource_linker import (
    refresh_question_learning_object_links,
    synchronize_detected_questions,
)
from .services.question_workflow import mirror_generated_questions
from .tests import authenticated_api_client


@patch.dict(os.environ, {"SEMANTIC_GROUPING_MODE": "legacy"})
class GeneratedQuestionSafetyTests(TestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Grade 1 Science")
        self.node = OutlineNode.objects.create(
            course=self.course, title="Solid, Liquid and Gas", order=0, depth=0,
        )
        self.material = LearningMaterial.objects.create(
            course=self.course, outline_node=self.node, title="Lesson two",
            status="completed", generated_json={"learning_objects_confirmed": True},
        )
        self.solid = self._object(0, "Key properties of solids", "A solid has a fixed shape and a fixed volume.")
        self.liquid = self._object(1, "Key properties of liquids", "A liquid flows and takes the shape of its container.")
        self.gas = self._object(2, "Key properties of gases", "A gas spreads out to fill any container.")

        self.solid_question = self._generate(self.solid, "Does a solid keep its shape?")
        self.liquid_question = self._generate(self.liquid, "Which state of matter flows?")
        self.gas_question = self._generate(self.gas, "What does a gas do inside a container?")

    def _object(self, order, title, content):
        group = LearningObjectGroup.objects.create(outline_node=self.node, label=title)
        return LearningObject.objects.create(
            material=self.material, group=group, title=title, content=content, order=order,
        )

    def _generate(self, learning_object, text):
        """Exactly what the generation pipeline does once a run finishes."""
        generated = GeneratedQuestion.objects.create(
            node=learning_object, question_text=text, question_format="TF",
            correct_answer="True", bloom_level="remember", thinking_order="LOT",
            status="final",
        )
        mirror_generated_questions(learning_object, [generated])
        return Question.objects.get(adaptive_question=generated)

    def _assert_intact(self, question, learning_object):
        question.refresh_from_db()
        link = question.learning_object_links.get()
        self.assertEqual(link.learning_object_id, learning_object.id)
        self.assertEqual(link.method, "generated_from_object")
        self.assertEqual(link.review_status, QuestionLearningObjectLink.ReviewStatus.AUTO_CONFIRMED)
        self.assertIsNotNone(question.adaptive_question_id)
        self.assertTrue(GeneratedQuestion.objects.filter(pk=question.adaptive_question_id).exists())

    def _assert_gone(self, question):
        self.assertFalse(Question.objects.filter(pk=question.pk).exists())
        self.assertFalse(GeneratedQuestion.objects.filter(pk=question.adaptive_question_id).exists())

    def test_deleting_a_concept_in_final_review_deletes_only_its_questions(self):
        response = authenticated_api_client().delete(
            f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/learning-objects/{self.solid.id}/",
        )

        self.assertEqual(response.status_code, 200, getattr(response, "data", None))
        self._assert_gone(self.solid_question)
        self._assert_intact(self.liquid_question, self.liquid)
        self._assert_intact(self.gas_question, self.gas)

    def test_deleting_an_object_on_step_one_deletes_only_its_questions(self):
        """Step 1 also unconfirms the file, which took a second pairing branch
        that stripped every question's link in the file."""
        response = authenticated_api_client().delete(
            f"/api/courses/{self.course.id}/materials/{self.material.id}/learning-objects/{self.solid.id}/",
        )

        self.assertEqual(response.status_code, 200, getattr(response, "data", None))
        self._assert_gone(self.solid_question)
        self._assert_intact(self.liquid_question, self.liquid)
        self._assert_intact(self.gas_question, self.gas)

    def test_re_pairing_never_re_guesses_a_generated_question(self):
        refresh_question_learning_object_links(self.material)

        self._assert_intact(self.solid_question, self.solid)
        self._assert_intact(self.liquid_question, self.liquid)
        self._assert_intact(self.gas_question, self.gas)

    def test_extracted_questions_are_still_paired(self):
        """The protection is for generated questions only; extracted ones keep
        being matched to the object they read like."""
        extracted = Question.objects.create(
            material=self.material, prompt="Why does a liquid take the shape of its container?",
            source_type=Question.SourceType.PDF,
        )

        refresh_question_learning_object_links(self.material)

        self.assertTrue(extracted.learning_object_links.exists())

    def test_a_question_a_teacher_moved_to_another_concept_survives(self):
        link = self.solid_question.learning_object_links.get()
        link.learning_object = self.liquid
        link.method = "teacher_selected"
        link.review_status = QuestionLearningObjectLink.ReviewStatus.TEACHER_CONFIRMED
        link.save()

        authenticated_api_client().delete(
            f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/learning-objects/{self.solid.id}/",
        )

        self.assertTrue(Question.objects.filter(pk=self.solid_question.pk).exists())

    def test_re_syncing_detected_questions_keeps_generated_and_manual_ones(self):
        """Changing a block's classification re-syncs the PDF's detected
        questions, which used to delete every question not found in the PDF."""
        manual = Question.objects.create(
            material=self.material, prompt="Name a solid in your classroom.",
            source_type=Question.SourceType.MANUAL,
        )
        stale_extracted = Question.objects.create(
            material=self.material, prompt="An old detected question.",
            source_type=Question.SourceType.PDF,
        )

        synchronize_detected_questions(self.material, [])

        self.assertTrue(Question.objects.filter(pk=manual.pk).exists())
        self.assertFalse(Question.objects.filter(pk=stale_extracted.pk).exists())
        self._assert_intact(self.liquid_question, self.liquid)
        self._assert_intact(self.gas_question, self.gas)

    def test_generating_one_concept_leaves_another_concepts_questions_alone(self):
        """A generated question sent back for review has no learner-facing copy.
        Regenerating a different concept used to delete it."""
        GeneratedQuestion.objects.filter(pk=self.gas_question.adaptive_question_id).delete()
        self.gas_question.refresh_from_db()
        self.assertIsNone(self.gas_question.adaptive_question_id)

        self._generate(self.solid, "Can a solid be squeezed into a new shape?")

        self.assertTrue(Question.objects.filter(pk=self.gas_question.pk).exists())
        self._assert_intact(self.liquid_question, self.liquid)

    def test_regenerating_a_concept_still_replaces_its_own_old_questions(self):
        GeneratedQuestion.objects.filter(node=self.solid).delete()

        self._generate(self.solid, "Does a solid have a fixed volume?")

        self.assertFalse(Question.objects.filter(pk=self.solid_question.pk).exists())
