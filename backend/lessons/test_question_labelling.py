"""Printed questions are labelled in the Questions step, not at upload.

Upload stores a PDF's printed questions without a Bloom level, LOTS/HOTS or
category, so the classifier is not loaded before the teacher reaches the
Questions screen. Labelling happens there; until then, and for a "create"
question that has no LOTS/HOTS tier, nothing reaches the learners' bank.
"""

from unittest.mock import Mock, patch

from django.test import TestCase

from .models import CourseGroup, LearningMaterial, OutlineNode, ExtractedQuestion as Question
from question_generation.models import Question as BankQuestion
from .services import question_workflow
from .services.question_workflow import (
    detected_question_payloads,
    parse_question_structure,
    synchronize_detected_questions,
)
from .test_utils import authenticated_api_client

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
        with patch("question_generation.services.teacher_questions._get_classifier", side_effect=classifier_must_not_run):
            synchronize_detected_questions(self.pdf, [BLOCK])
        return Question.objects.get(material=self.pdf)

    def label(self, result):
        with patch("question_generation.services.teacher_questions._get_classifier", return_value=Mock(classify=Mock(return_value=result))), patch("question_generation.services.teacher_questions.build_index"):
            return self.client.post(
                f"/api/generation/topics/{self.node.id}/questions/label/"
            )

    def test_upload_stores_printed_questions_unlabelled(self):
        question = BankQuestion.objects.get(extracted_question=self.upload())

        self.assertEqual(
            (question.bloom_level, question.thinking_order, question.category), ("", "", ""),
        )

    def test_upload_mirrors_question_to_unlabelled_bank_row(self):
        question = self.upload()

        bank_question = BankQuestion.objects.get(extracted_question=question)

        self.assertEqual(bank_question.source, "lesson_pdf")
        self.assertEqual(bank_question.outline_node_id, self.node.id)
        self.assertEqual(bank_question.question_format, "MCQ")
        self.assertEqual(bank_question.choices, {
            "A": "Anything that has mass",
            "B": "Only liquids",
        })
        self.assertEqual(bank_question.correct_answer, "")
        self.assertEqual(
            (bank_question.bloom_level, bank_question.thinking_order, bank_question.category),
            ("", "", ""),
        )

    def test_an_unlabelled_question_is_not_given_to_learners(self):
        question = BankQuestion.objects.get(extracted_question=self.upload())

        self.assertNotEqual(question.status, "final")

    def test_the_questions_step_labels_them(self):
        question = BankQuestion.objects.get(extracted_question=self.upload())

        response = self.label(LOT)

        self.assertEqual(response.status_code, 200)
        question.refresh_from_db()
        self.assertEqual(question.thinking_order, "LOT")
        # Labelling alone cannot approve a question with no answer or concept.
        self.assertEqual(question.status, "review")

    def test_a_create_question_counts_as_labelled_but_stays_out_of_the_quiz(self):
        question = BankQuestion.objects.get(extracted_question=self.upload())

        self.label(CREATE)

        question.refresh_from_db()
        self.assertEqual(question.bloom_level, "create")
        self.assertEqual(question.thinking_order, "")
        self.assertNotEqual(question.status, "final")
        # Labelled, so it no longer waits: a second pass has nothing to do.
        from question_generation.services.teacher_questions import label_printed
        with patch("question_generation.services.teacher_questions._get_classifier", side_effect=classifier_must_not_run):
            self.assertEqual(label_printed(self.node), 0)

    def test_a_failure_is_reported_for_try_again(self):
        self.upload()

        with patch("question_generation.services.teacher_questions._get_classifier", side_effect=RuntimeError("model missing")), patch("question_generation.services.teacher_questions.build_index"):
            response = self.client.post(
                f"/api/generation/topics/{self.node.id}/questions/label/"
            )

        self.assertEqual(response.status_code, 503)
        self.assertIn("model missing", response.data["detail"])

    def test_processing_the_pdf_again_keeps_an_existing_label(self):
        question = BankQuestion.objects.get(extracted_question=self.upload())
        self.label(LOT)

        self.upload()

        question.refresh_from_db()
        self.assertEqual(question.thinking_order, "LOT")

    def test_extracts_lettered_choices_from_common_pdf_formats(self):
        result = parse_question_structure(
            "Which state of matter has a fixed volume?\n"
            "(A) Solid\n"
            "B: Liquid\n"
            "C - Gas\n"
            "D. Plasma"
        )

        self.assertEqual(result["question_type"], Question.Type.MULTIPLE_CHOICE)
        self.assertEqual(
            result["choices"],
            ["Solid", "Liquid", "Gas", "Plasma"],
        )

    def test_extracts_choices_from_adjacent_pdf_blocks_without_answer_key(self):
        blocks = [
            {
                "category": "assessment",
                "text": "Which state of matter has a fixed volume?",
                "page": 1,
                "block_id": 1,
            },
            {"category": "assessment", "text": "A. Solid", "page": 1, "block_id": 2},
            {"category": "assessment", "text": "B. Liquid", "page": 1, "block_id": 3},
            {"category": "assessment", "text": "C. Gas", "page": 1, "block_id": 4},
            {"category": "assessment", "text": "D. Plasma", "page": 1, "block_id": 5},
        ]

        payload = detected_question_payloads(blocks)[0]

        self.assertEqual(payload["question_type"], Question.Type.MULTIPLE_CHOICE)
        self.assertEqual(payload["choices"], ["Solid", "Liquid", "Gas", "Plasma"])
        self.assertEqual(payload["correct_answer"], "")

    def test_extracts_adjacent_choices_and_answer_key(self):
        blocks = [
            {"category": "assessment", "text": "Which state has fixed volume?", "page": 1},
            {"category": "assessment", "text": "A. Solid", "page": 1},
            {"category": "assessment", "text": "B. Liquid", "page": 1},
            {"category": "assessment", "text": "Answer: A", "page": 1},
        ]

        payload = detected_question_payloads(blocks)[0]

        self.assertEqual(payload["choices"], ["Solid", "Liquid"])
        self.assertEqual(payload["correct_answer"], "Solid")

    def test_extracts_adjacent_true_false_choices(self):
        blocks = [
            {"category": "assessment", "text": "Is water a solid at room temperature?", "page": 1},
            {"category": "assessment", "text": "True", "page": 1},
            {"category": "assessment", "text": "False", "page": 1},
        ]

        payload = detected_question_payloads(blocks)[0]

        self.assertEqual(payload["question_type"], Question.Type.TRUE_FALSE)
        self.assertEqual(payload["choices"], ["True", "False"])
