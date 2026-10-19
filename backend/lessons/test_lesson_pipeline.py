import hashlib
from unittest.mock import patch

import requests
from django.core.files.base import ContentFile
from django.test import SimpleTestCase, TestCase

from lessons.models import CourseGroup, LearningMaterial, OutlineNode
from lessons.services.block_classifier import classify_blocks
from lessons.services.embedding_service import SBERT_MODEL
from lessons.services.learning_object_builder import build_learning_objects
from lessons.services.lesson_pdf_extractor import join_text_lines
from lessons.services.lesson_pipeline import (
    LessonPipelineError,
    LessonPipelineUnavailableError,
    _describe_visuals_if_available,
    generate_material_outputs,
)
from lessons.features.pdf_processing.use_cases import (
    PdfProcessingUnavailableError,
    upload_learning_material,
)


def lesson_blocks():
    return [
        {
            "block_id": 0,
            "page": 1,
            "block_index": 0,
            "text": "Solids",
            "bbox": [10, 10, 100, 30],
            "dominant_font_size": 14.0,
            "is_bold": True,
            "leading_bold_text": "Solids",
        },
        {
            "block_id": 1,
            "page": 1,
            "block_index": 1,
            "text": "A solid keeps its shape because its particles stay closely packed.",
            "bbox": [10, 40, 300, 80],
            "dominant_font_size": 10.0,
            "is_bold": False,
            "leading_bold_text": None,
        },
    ]


class PipelineBlockBoundaryTests(SimpleTestCase):
    @patch("lessons.services.lesson_pipeline.describe_visual_objects")
    def test_groq_ssl_error_only_postpones_image_description(self, describe):
        describe.side_effect = requests.exceptions.SSLError("unexpected EOF")
        drafts = [{"draft_id": "image_1", "type": "image"}]

        warning = _describe_visuals_if_available(drafts, {"/image.png": b"png"})

        self.assertIn("pending", warning)

    def test_pdf_line_joining_preserves_numbered_lettered_and_bulleted_items(self):
        self.assertEqual(
            join_text_lines([
                "1. Pollination: Pollen reaches the stigma.",
                "This may happen with the help of insects.",
                "2. Fertilization: A pollen tube grows.",
                "a. First supporting detail",
                "- Final reminder",
            ]),
            "1. Pollination: Pollen reaches the stigma. This may happen with the help of insects.\n"
            "2. Fertilization: A pollen tube grows.\n"
            "a. First supporting detail\n"
            "- Final reminder",
        )

    def test_learning_object_keeps_list_items_from_separate_pdf_blocks(self):
        blocks = lesson_blocks() + [
            {
                "block_id": 2,
                "page": 1,
                "block_index": 2,
                "text": "1. Particles are tightly packed.",
                "bbox": [10, 85, 300, 105],
                "dominant_font_size": 10.0,
                "is_bold": False,
                "leading_bold_text": None,
            },
            {
                "block_id": 3,
                "page": 1,
                "block_index": 3,
                "text": "2. Particles vibrate in place.",
                "bbox": [10, 110, 300, 130],
                "dominant_font_size": 10.0,
                "is_bold": False,
                "leading_bold_text": None,
            },
        ]

        objects = build_learning_objects(blocks)

        self.assertIn(
            "\n1. Particles are tightly packed.\n2. Particles vibrate in place.",
            objects[0]["content"],
        )

    def test_question_style_heading_starts_section_and_front_matter_is_ignored(self):
        blocks = [
            {
                "block_id": 0,
                "page": 1,
                "text": "States of Matter Solid, Liquid, and Gas",
                "bbox": [70, 69, 389, 114],
                "dominant_font_size": 12.0,
                "is_bold": False,
                "line_count": 2,
            },
            {
                "block_id": 1,
                "page": 1,
                "text": "Science - Lesson 1 - Grade 4",
                "bbox": [70, 128, 200, 142],
                "dominant_font_size": 10.0,
                "is_bold": False,
                "line_count": 1,
            },
            {
                "block_id": 2,
                "page": 1,
                "text": "What Is Matter?",
                "bbox": [70, 158, 183, 178],
                "dominant_font_size": 15.0,
                "is_bold": True,
                "line_count": 1,
            },
            {
                "block_id": 3,
                "page": 1,
                "text": "Matter is anything that has mass and takes up space.",
                "bbox": [70, 188, 530, 220],
                "dominant_font_size": 10.8,
                "is_bold": False,
                "line_count": 2,
            },
        ]

        classified = classify_blocks(blocks)
        teaching = [
            block for block in classified
            if block["category"] in {"lesson_content", "needs_review"}
        ]
        objects = build_learning_objects(teaching)

        self.assertEqual(classified[2]["category"], "needs_review")
        self.assertEqual(len(objects), 1)
        self.assertEqual(objects[0]["title"], "What Is Matter?")
        self.assertEqual(
            objects[0]["content"],
            "Matter is anything that has mass and takes up space.",
        )

    def test_numbered_question_remains_an_assessment(self):
        block = {
            "block_id": 0,
            "page": 1,
            "text": "1. What is matter?",
            "bbox": [70, 158, 300, 178],
            "dominant_font_size": 15.0,
            "is_bold": True,
            "line_count": 1,
        }

        self.assertEqual(classify_blocks([block])[0]["category"], "assessment")

    def test_numbered_question_heading_is_kept_when_numbered_sections_follow(self):
        blocks = [
            {
                "block_id": 0,
                "page": 1,
                "text": "1. What is Matter?",
                "bbox": [70, 40, 300, 65],
                "dominant_font_size": 19.0,
                "is_bold": True,
                "leading_bold_text": "1. What is Matter?",
                "line_count": 1,
            },
            {
                "block_id": 1,
                "page": 1,
                "text": "Matter is anything that takes up space and has mass.",
                "bbox": [70, 75, 530, 105],
                "dominant_font_size": 11.5,
                "is_bold": False,
                "line_count": 2,
            },
            {
                "block_id": 2,
                "page": 1,
                "text": "2. Solids",
                "bbox": [70, 120, 300, 145],
                "dominant_font_size": 19.0,
                "is_bold": True,
                "line_count": 1,
            },
        ]

        classified = classify_blocks(blocks)
        teaching = [
            block for block in classified
            if block["category"] in {"lesson_content", "needs_review"}
        ]
        objects = build_learning_objects(teaching)

        self.assertEqual(classified[0]["category"], "needs_review")
        self.assertEqual(objects[0]["title"], "What is Matter?")


class LessonPipelineTests(TestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Science")
        module = OutlineNode.objects.create(course=self.course, title="Matter")
        self.topic = OutlineNode.objects.create(
            course=self.course,
            parent=module,
            title="States of Matter",
            depth=1,
            embedding=[1.0, 0.0],
            embedding_model=SBERT_MODEL,
            embedding_text_hash=hashlib.sha256(
                b"Matter > States of Matter"
            ).hexdigest(),
        )
        embedder = patch(
            "lessons.services.embedding_service.embed_texts",
            return_value=[[1.0, 0.0]],
        )
        embedder.start()
        self.addCleanup(embedder.stop)
        self.material = LearningMaterial.objects.create(
            course=self.course,
            outline_node=self.topic,
            module_node=module,
            title="Solids",
            pdf_file=ContentFile(b"pdf", name="solids.pdf"),
        )

    @patch(
        "lessons.services.lesson_pipeline.extract_lesson_pdf_blocks",
        return_value=(lesson_blocks(), []),
    )
    def test_jean_pipeline_writes_mavia_learning_contract(self, _extract):
        generate_material_outputs(self.material, propagate_validation_error=True)

        self.material.refresh_from_db()
        learning_object = self.material.learning_objects.get()
        self.assertEqual(self.material.status, LearningMaterial.Status.COMPLETED)
        self.assertEqual(learning_object.title, "Solids")
        self.assertIsNotNone(learning_object.group_id)
        self.assertFalse(self.material.generated_json["learning_objects_confirmed"])
        self.assertIsInstance(self.material.generated_json["narration_script"], list)
        self.assertEqual(
            self.material.generated_json["narration_script"][0]["learning_object_id"],
            learning_object.id,
        )

    @patch(
        "lessons.services.lesson_pipeline.extract_lesson_pdf_blocks",
        return_value=(lesson_blocks(), []),
    )
    def test_regeneration_returns_material_to_confirmation_gate(self, _extract):
        self.material.generated_json = {"learning_objects_confirmed": True}
        self.material.save(update_fields=["generated_json"])

        generate_material_outputs(self.material, propagate_validation_error=True)

        self.material.refresh_from_db()
        self.assertFalse(self.material.generated_json["learning_objects_confirmed"])

    @patch("lessons.services.lesson_pipeline.extract_lesson_pdf_blocks")
    def test_assessment_pdf_still_hands_questions_to_mavia(self, extract):
        extract.return_value = ([
            {
                "block_id": index,
                "page": 1,
                "block_index": index,
                "text": prompt,
                "bbox": [10, 10 + index * 20, 300, 25 + index * 20],
                "dominant_font_size": 10.0,
                "is_bold": False,
                "leading_bold_text": None,
            }
            for index, prompt in enumerate([
                "What is a solid?",
                "Why does a solid keep its shape?",
                "Which object is a solid?",
            ])
        ], [])

        generate_material_outputs(self.material, propagate_validation_error=True)

        self.material.refresh_from_db()
        self.assertEqual(self.material.generated_json["document_role"], "assessment")
        self.assertEqual(self.material.learning_objects.count(), 0)
        self.assertEqual(self.material.questions.count(), 3)
        self.assertEqual(len(self.material.generated_json["questions"]), 3)

    @patch(
        "lessons.services.lesson_pipeline.extract_lesson_pdf_blocks",
        side_effect=RuntimeError("extractor unavailable"),
    )
    def test_dependency_error_is_retryable_and_not_invalid_pdf(self, _extract):
        with self.assertRaisesMessage(
            LessonPipelineUnavailableError,
            "extractor unavailable",
        ):
            generate_material_outputs(
                self.material,
                propagate_validation_error=True,
            )
        self.material.refresh_from_db()
        self.assertEqual(self.material.status, LearningMaterial.Status.FAILED)
        self.assertEqual(self.material.error_message, "extractor unavailable")

    @patch("lessons.features.pdf_processing.use_cases.generate_material_outputs")
    def test_retryable_failure_keeps_uploaded_material(self, generate):
        def fail_processing(material, **_kwargs):
            material.status = LearningMaterial.Status.FAILED
            material.error_message = "Groq SSL connection failed"
            material.save(update_fields=["status", "error_message"])
            raise LessonPipelineUnavailableError("Groq SSL connection failed")

        generate.side_effect = fail_processing
        with self.assertRaises(PdfProcessingUnavailableError) as raised:
            upload_learning_material(
                course=self.course,
                pdf_file=ContentFile(b"different-pdf", name="retry.pdf"),
                outline_node_id=self.topic.id,
            )

        saved = LearningMaterial.objects.get(title="retry")
        self.assertEqual(raised.exception.material_id, saved.id)
        self.assertEqual(saved.status, LearningMaterial.Status.FAILED)
        self.assertTrue(saved.pdf_file)
