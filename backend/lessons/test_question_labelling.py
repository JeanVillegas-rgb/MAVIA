"""Printed questions are labelled in the Questions step, not at upload.

Upload stores a PDF's printed questions without a Bloom level, LOTS/HOTS or
category, so the classifier is not loaded before the teacher reaches the
Questions screen. Labelling happens there; until then, and for a "create"
question that has no LOTS/HOTS tier, nothing reaches the learners' bank.
"""

from unittest.mock import patch

from django.test import TestCase

from .models import CourseGroup, LearningMaterial, LearningObject, LearningObjectGroup, OutlineNode, Question
from .services import question_workflow
from .services.learning_resource_linker import (
    attach_orphan_objects_to_their_section,
    detected_question_payloads,
    synchronize_detected_questions,
)
from .tests import authenticated_api_client

BLOCK = {
    "category": "assessment",
    "text": "1. What is matter?\nA. Anything that has mass\nB. Only liquids",
    "page": 1,
    "block_id": 7,
}
LOT = {"bloom_level": "remember", "thinking_order": "LOT", "category": "Facts and Information"}
CREATE = {"bloom_level": "create", "thinking_order": None, "category": "Outcome"}


def classifier_must_not_run(_text):
    raise AssertionError("the classifier ran at upload")


class QuestionLabellingTests(TestCase):
    def setUp(self):
        self.client = authenticated_api_client()
        self.course = CourseGroup.objects.create(title="Science")
        self.node = OutlineNode.objects.create(course=self.course, title="States of matter")
        self.pdf = LearningMaterial.objects.create(
            course=self.course, outline_node=self.node, title="Worksheet",
            status=LearningMaterial.Status.COMPLETED,
            generated_json={"learning_objects_confirmed": True},
        )

    def upload(self):
        with patch.object(question_workflow, "classify_question", side_effect=classifier_must_not_run):
            synchronize_detected_questions(self.pdf, [BLOCK])
        return Question.objects.get(material=self.pdf)

    def label(self, result):
        with patch.object(question_workflow, "classify_question", return_value=result):
            return self.client.post(
                f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/label-questions/"
            )

    def test_upload_stores_printed_questions_unlabelled(self):
        question = self.upload()

        self.assertEqual(
            (question.bloom_level, question.thinking_order, question.category), ("", "", ""),
        )

    def test_an_unlabelled_question_is_not_given_to_learners(self):
        question = self.upload()

        self.assertFalse(question_workflow.is_given_to_learners(question))

    def test_the_questions_step_labels_them(self):
        question = self.upload()

        response = self.label(LOT)

        self.assertEqual(response.status_code, 200)
        question.refresh_from_db()
        self.assertEqual(question.thinking_order, "LOT")
        self.assertTrue(question_workflow.is_given_to_learners(question))

    def test_a_create_question_counts_as_labelled_but_stays_out_of_the_quiz(self):
        question = self.upload()

        self.label(CREATE)

        question.refresh_from_db()
        self.assertEqual(question.bloom_level, "create")
        self.assertEqual(question.thinking_order, "")
        self.assertFalse(question_workflow.is_given_to_learners(question))
        # Labelled, so it no longer waits: a second pass has nothing to do.
        with patch.object(question_workflow, "classify_question", side_effect=classifier_must_not_run):
            self.assertEqual(question_workflow.label_printed_questions(self.node), 0)

    def test_a_failure_is_reported_for_try_again(self):
        self.upload()

        with patch.object(question_workflow, "classify_question", side_effect=RuntimeError("model missing")):
            response = self.client.post(
                f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/label-questions/"
            )

        self.assertEqual(response.status_code, 503)
        self.assertIn("model missing", response.data["detail"])

    def test_processing_the_pdf_again_keeps_an_existing_label(self):
        question = self.upload()
        self.label(LOT)

        self.upload()

        question.refresh_from_db()
        self.assertEqual(question.thinking_order, "LOT")


class MoveOutStaysOutTests(TestCase):
    """A part the teacher took out of its concept is not filed back under its section."""

    def test_a_moved_out_part_is_not_folded_back(self):
        client = authenticated_api_client()
        course = CourseGroup.objects.create(title="Science")
        node = OutlineNode.objects.create(course=course, title="States of matter")
        pdf = LearningMaterial.objects.create(
            course=course, outline_node=node, title="Lesson 1",
            status=LearningMaterial.Status.COMPLETED,
            generated_json={"learning_objects_confirmed": True},
        )

        def add(title, order):
            return LearningObject.objects.create(
                material=pdf, title=title, section_title="Matter", content=f"{title} text.", order=order,
                group=LearningObjectGroup.objects.create(outline_node=node, label=title),
            )

        matter, solid = add("Matter", 0), add("Solid", 1)
        attach_orphan_objects_to_their_section(pdf)
        solid.refresh_from_db()
        self.assertEqual(solid.group_id, matter.group_id)

        client.post(
            f"/api/courses/{course.id}/outline-nodes/{node.id}/learning-objects/{solid.id}/move-out/"
        )
        attach_orphan_objects_to_their_section(pdf)

        solid.refresh_from_db()
        self.assertNotEqual(solid.group_id, matter.group_id)
