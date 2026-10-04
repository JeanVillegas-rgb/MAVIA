from unittest.mock import patch

from django.test import TestCase, override_settings

from lessons.models import CourseGroup, LearningMaterial, LearningObject, OutlineNode

from .models import LessonVariant
from .variant_generator import VariantGenerationError, fill_missing_slots


@override_settings(
    ADAPTIVE_VARIANT_GENERATION_ENABLED=True,
    ADAPTIVE_VARIANT_LLM_MODEL="gemma3:4b",
)
class GapFillingTests(TestCase):
    def setUp(self):
        course = CourseGroup.objects.create(title="Grade 1 Science")
        node = OutlineNode.objects.create(course=course, title="Matter", order=0, depth=0)
        material = LearningMaterial.objects.create(course=course, outline_node=node, title="Lesson 1")
        self.obj = LearningObject.objects.create(
            material=material, title="Solid", content="A solid holds its shape and does not flow.", order=0
        )

    @patch("course.variant_generator._request_variants")
    def test_fills_both_slots_when_none_exist(self, request_variants):
        request_variants.return_value = {"SIMPLIFIED": "Solid keeps shape.", "ELABORATED": "A solid holds a fixed shape."}

        result = fill_missing_slots(self.obj)

        self.assertCountEqual(result["generated"], ["SIMPLIFIED", "ELABORATED"])
        self.assertEqual(self.obj.variants.count(), 2)
        self.assertTrue(all(row.origin == "generated" for row in self.obj.variants.all()))

    @patch("course.variant_generator._request_variants")
    def test_only_fills_the_missing_slot(self, request_variants):
        LessonVariant.objects.create(
            learning_object=self.obj, variant="ELABORATED", narration="Real teacher text", origin="source_pdf"
        )
        request_variants.return_value = {"SIMPLIFIED": "Solid keeps shape.", "ELABORATED": "ignored"}

        result = fill_missing_slots(self.obj)

        self.assertEqual(result["generated"], ["SIMPLIFIED"])
        self.assertEqual(result["skipped"], ["ELABORATED"])
        self.assertEqual(
            LessonVariant.objects.get(learning_object=self.obj, variant="ELABORATED").narration,
            "Real teacher text",
        )

    @patch("course.variant_generator._request_variants")
    def test_never_overwrites_source_pdf_text(self, request_variants):
        LessonVariant.objects.create(
            learning_object=self.obj, variant="SIMPLIFIED", narration="Human wording", origin="source_pdf"
        )
        LessonVariant.objects.create(
            learning_object=self.obj, variant="ELABORATED", narration="Human wording two", origin="source_pdf"
        )

        result = fill_missing_slots(self.obj)

        request_variants.assert_not_called()
        self.assertEqual(result["generated"], [])

    @patch("course.variant_generator._request_variants")
    def test_failure_leaves_the_slot_empty_and_is_reported(self, request_variants):
        request_variants.side_effect = VariantGenerationError("Gemma did not return valid JSON.")

        result = fill_missing_slots(self.obj)

        self.assertEqual(result["generated"], [])
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("valid JSON", result["errors"][0]["detail"])
        self.assertEqual(self.obj.variants.count(), 0)

    def test_empty_content_generates_nothing(self):
        self.obj.content = "   "
        self.obj.save()
        result = fill_missing_slots(self.obj)
        self.assertEqual(result["generated"], [])
