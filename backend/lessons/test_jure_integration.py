"""Merge regression tests. LLM calls are mocked; Django uses a test database."""

import json
from types import SimpleNamespace
from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase, override_settings

from lessons.models import CourseGroup, ExtractedQuestion, LearningMaterial, LearningObject, LearningObjectGroup, OutlineNode
from lessons.services.question_workflow import synchronize_detected_questions
from lessons.services.visual_describer import describe_image
from learning_path.models import ConceptPrerequisite
from learning_path.services.concept_units import concepts_for_topic
from learning_path.services.published import get_published_path
from learning_path.services.publishing import LoopError, hold_loop_links, order_with_links, save_learning_path
from question_generation.models import Question
from question_generation.services.question_bank import settle, tier_counts


class VisualRequestTests(SimpleTestCase):
    @override_settings(GROQ_TEXT_MODEL="test-text", GROQ_VISION_MODEL="test-vision")
    @patch("lessons.services.visual_describer.groq_generate")
    def test_table_uses_compact_cells_without_sending_png(self, generate):
        generate.return_value = (json.dumps({
            "source_id": None, "missing_details": [],
            "generated_description": "Solids keep their shape.",
        }), {})
        result = describe_image(b"png", table_data={
            "num_rows": 1, "num_cols": 1,
            "table_cells": [{"text": "Solid", "bbox": {"l": 5}, "row_span": 1}],
            "grid": [[{"text": "duplicate grid"}]],
        })
        self.assertEqual(result["description"], "Solids keep their shape.")
        prompt = generate.call_args.args[0]
        self.assertIn('"text":"Solid"', prompt)
        self.assertNotIn("bbox", prompt)
        self.assertNotIn("duplicate grid", prompt)
        self.assertEqual(generate.call_args.kwargs["model"], "test-text")
        self.assertIsNone(generate.call_args.kwargs["image_bytes"])

    @patch("lessons.services.visual_describer.groq_generate")
    def test_diagram_still_sends_image(self, generate):
        generate.return_value = (json.dumps({
            "source_id": None, "missing_details": [],
            "generated_description": "Particles are close together.",
        }), {})
        describe_image(b"png")
        self.assertEqual(generate.call_args.kwargs["image_bytes"], b"png")


class PathOrderingTests(SimpleTestCase):
    def concepts(self):
        return [SimpleNamespace(id=i, order=i, title=str(i)) for i in range(3)]

    def test_kahn_sort_respects_dependencies_and_document_ties(self):
        concepts = self.concepts()
        ordered, depth = order_with_links(concepts, [(2, 0)])
        self.assertEqual([item.id for item in ordered], [1, 2, 0])
        self.assertEqual(depth, {1: 0, 2: 0, 0: 1})

    def test_cycle_is_not_silently_dropped(self):
        with self.assertRaises(LoopError):
            order_with_links(self.concepts(), [(0, 1), (1, 0)])

    def test_cycle_proposal_is_held_for_review(self):
        first, second, _ = self.concepts()
        decision = {"prerequisite": second, "dependent": first,
                    "verdict": "accepted", "evidence": {"contradictions": []}}
        self.assertEqual(hold_loop_links([decision], [(first.id, second.id)]), [decision])
        self.assertEqual(decision["verdict"], "pending")
        self.assertIn("loop", decision["evidence"]["contradictions"])


class QuestionPathIntegrationTests(TestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Integration science")
        self.topic = OutlineNode.objects.create(course=self.course, title="States")
        self.material = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="Lesson",
            status="completed", generated_json={"learning_objects_confirmed": True},
        )
        self.groups, self.objects = [], []
        for index, title in enumerate(("Matter", "Solid")):
            group = LearningObjectGroup.objects.create(outline_node=self.topic, label=title)
            obj = LearningObject.objects.create(
                material=self.material, group=group, title=title, section_title=title,
                content=f"{title} has observable physical properties.", order=index,
            )
            self.groups.append(group)
            self.objects.append(obj)

    def upload_question(self):
        synchronize_detected_questions(self.material, [{
            "category": "assessment", "page": 2, "block_id": 9,
            "text": "1. Which state keeps its shape?\nA. Solid\nB. Liquid\nAnswer: A",
        }])
        return Question.objects.get(extracted_question__material=self.material)

    def final_question(self, **changes):
        values = dict(
            outline_node=self.topic, node=self.objects[0], source="manual",
            question_text="Matter has mass?", question_format="TF", correct_answer="True",
            thinking_order="LOT", bloom_level="remember", paired_by="teacher",
            check_result="supported", status="final", content_fingerprint="unique",
        )
        values.update(changes)
        return Question.objects.create(**values)

    @patch("question_generation.services.pipeline._get_classifier", side_effect=AssertionError("Upload must not label"))
    @patch("config.groq_client.generate", side_effect=AssertionError("Upload must not call LLM"))
    def test_printed_choices_and_answer_reach_bank_without_labelling(self, _groq, _classifier):
        row = self.upload_question()
        self.assertEqual(ExtractedQuestion.objects.count(), 1)
        self.assertEqual(row.choices, {"A": "Solid", "B": "Liquid"})
        self.assertEqual(row.correct_answer, "A")
        self.assertEqual((row.status, row.thinking_order, row.node_id), ("review", "", None))
        self.upload_question()
        self.assertEqual(Question.objects.count(), 1)

    def test_teacher_reviewed_printed_question_fills_minimum(self):
        row = self.upload_question()
        row.node = self.objects[1]
        row.paired_by, row.thinking_order, row.check_result = "teacher", "LOT", "supported"
        row.teacher_edited = True
        settle(row)
        self.assertEqual(tier_counts(self.objects[1]), {"LOT": 1, "HOT": 0})

    def test_deleting_extracted_source_removes_its_bank_copy(self):
        row = self.upload_question()
        row.extracted_question.delete()
        self.assertFalse(Question.objects.filter(pk=row.pk).exists())

    def test_database_refuses_incomplete_final_question(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.final_question(node=None)

    def test_database_refuses_repeated_final_text(self):
        self.final_question()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.final_question()

    def test_database_refuses_printed_question_without_source(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.final_question(source="lesson_pdf", status="review", node=None)

    def test_deleting_one_concept_preserves_other_concepts_questions(self):
        first = self.final_question()
        other = self.final_question(node=self.objects[1], source="generated",
                                    content_fingerprint="different", question_text="Solids keep shape?")
        self.objects[1].delete()
        first.refresh_from_db()
        self.assertEqual(first.node_id, self.objects[0].pk)
        self.assertFalse(Question.objects.filter(pk=other.pk).exists())

    def test_teacher_question_outlives_deleted_concept_as_review(self):
        row = self.final_question()
        self.objects[0].delete()
        row.refresh_from_db()
        self.assertEqual((row.node_id, row.paired_by, row.status), (None, "", "review"))

    def test_teacher_link_orders_saved_path_and_student_answers_are_hidden(self):
        self.final_question()
        ConceptPrerequisite.objects.create(
            outline_node=self.topic, prerequisite=self.groups[1], dependent=self.groups[0],
            status="approved", source="teacher",
        )
        save_learning_path(self.topic, concepts_for_topic(self.topic))
        student = get_published_path(self.topic, include_answers=False)
        self.assertEqual([step["concept_id"] for step in student["steps"]],
                         [self.groups[1].id, self.groups[0].id])
        questions = [question for step in student["steps"] for question in step["questions"]]
        self.assertEqual(len(questions), 1)
        self.assertNotIn("correct_answer", questions[0])
        teacher = get_published_path(self.topic, include_answers=True)
        self.assertEqual(teacher["steps"][1]["questions"][0]["correct_answer"], "True")
