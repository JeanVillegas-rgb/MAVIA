"""A PDF is filed under a topic, never under a module that has topics.

Measured live: a lumped Science 7 module was uploaded with the module
"Distance and Displacement" selected -- whose first topic has the same title --
and landed on the module row. Its topics ("Motion") stayed empty, and the PDF
was grouped with none of their PDFs.
"""

from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from lessons.features.pdf_processing.use_cases import (
    DuplicatePdfUploadError,
    PdfProcessingUseCaseError,
    upload_learning_material,
)
from lessons.models import CourseGroup, LearningMaterial, OutlineNode
from lessons.services.content_generator import choose_outline_node_for_material


class UploadDestinationTests(TestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Science 7")
        self.module = OutlineNode.objects.create(
            course=self.course, title="Distance and Displacement", order=0, depth=0,
        )
        self.same_name_topic = OutlineNode.objects.create(
            course=self.course, parent=self.module, title="Distance and Displacement", order=0, depth=1,
        )
        self.topic = OutlineNode.objects.create(
            course=self.course, parent=self.module, title="Motion", order=1, depth=1,
        )

    def _pdf(self):
        return SimpleUploadedFile("module.pdf", b"%PDF-1.4 motion", content_type="application/pdf")

    def test_a_module_with_topics_is_refused_before_anything_is_saved(self):
        with patch("lessons.features.pdf_processing.use_cases.generate_material_outputs") as generate:
            with self.assertRaisesMessage(PdfProcessingUseCaseError, "Select one of its topics"):
                upload_learning_material(
                    course=self.course, pdf_file=self._pdf(), outline_node_id=self.module.id,
                )

        generate.assert_not_called()
        self.assertFalse(LearningMaterial.objects.exists())

    def test_a_topic_is_accepted(self):
        with patch("lessons.features.pdf_processing.use_cases.generate_material_outputs") as generate:
            material, reused = upload_learning_material(
                course=self.course, pdf_file=self._pdf(), outline_node_id=self.topic.id,
            )

        self.assertFalse(reused)
        self.assertEqual(material.outline_node, self.topic)
        generate.assert_called_once()

    def test_unconfirmed_outline_requests_confirmation_only(self):
        with patch("lessons.features.pdf_processing.use_cases.generate_material_outputs") as generate:
            with self.assertRaisesMessage(
                PdfProcessingUseCaseError,
                "Confirm the course outline first for automatic classification.",
            ):
                upload_learning_material(course=self.course, pdf_file=self._pdf())

        generate.assert_not_called()
        self.assertFalse(LearningMaterial.objects.exists())

    def test_same_lesson_pdf_bytes_are_rejected_in_the_same_course(self):
        with patch("lessons.features.pdf_processing.use_cases.generate_material_outputs") as generate:
            upload_learning_material(
                course=self.course,
                pdf_file=self._pdf(),
                outline_node_id=self.topic.id,
            )

            with self.assertRaisesMessage(
                DuplicatePdfUploadError,
                "already been uploaded",
            ):
                upload_learning_material(
                    course=self.course,
                    pdf_file=SimpleUploadedFile(
                        "renamed.pdf",
                        b"%PDF-1.4 motion",
                        content_type="application/pdf",
                    ),
                    outline_node_id=self.topic.id,
                )

        self.assertEqual(LearningMaterial.objects.count(), 1)
        generate.assert_called_once()

    def test_a_module_without_topics_is_still_a_valid_place(self):
        """A module the outline never split into topics is its own topic."""
        lone = OutlineNode.objects.create(course=self.course, title="Heat", order=1, depth=0)

        with patch("lessons.features.pdf_processing.use_cases.generate_material_outputs"):
            material, _ = upload_learning_material(
                course=self.course, pdf_file=self._pdf(), outline_node_id=lone.id,
            )

        self.assertEqual(material.outline_node, lone)

    def test_automatic_filing_never_picks_a_module_with_topics(self):
        text = " ".join(["Distance and displacement describe motion of an object."] * 20)

        chosen = choose_outline_node_for_material(self.course, "", text)

        self.assertNotEqual(chosen, self.module)
