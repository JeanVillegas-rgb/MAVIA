from django.db import transaction
from django.db.utils import IntegrityError
from django.test import TestCase

from lessons.models import CourseGroup, LearningMaterial, LearningObject, OutlineNode

from .models import LessonVariant


class VersionSchemaTests(TestCase):
    def setUp(self):
        course = CourseGroup.objects.create(title="Grade 1 Science")
        node = OutlineNode.objects.create(course=course, title="Matter", order=0, depth=0)
        material = LearningMaterial.objects.create(course=course, outline_node=node, title="Lesson 1")
        self.original = LearningObject.objects.create(
            material=material, title="Solid", content="A solid holds its shape.", order=0
        )
        self.partner = LearningObject.objects.create(
            material=material, title="Solid again", content="A solid keeps a fixed shape.", order=1
        )

    def test_learning_object_can_be_represented_by_another(self):
        self.partner.represented_by = self.original
        self.partner.save()
        self.partner.refresh_from_db()
        self.assertEqual(self.partner.represented_by, self.original)
        self.assertIn(self.partner, self.original.represents.all())

    def test_representation_defaults_to_null(self):
        self.assertIsNone(self.original.represented_by)

    def test_variant_records_provenance(self):
        row = LessonVariant.objects.create(
            learning_object=self.original,
            variant="SIMPLIFIED",
            narration="A solid keeps a fixed shape.",
            origin="source_pdf",
            source_learning_object=self.partner,
            assigned_by="heuristic",
        )
        row.refresh_from_db()
        self.assertEqual(row.origin, "source_pdf")
        self.assertEqual(row.source_learning_object, self.partner)
        self.assertEqual(row.assigned_by, "heuristic")

    def test_primary_slots_are_single_occupancy(self):
        LessonVariant.objects.create(
            learning_object=self.original, variant="SIMPLIFIED", narration="One", origin="generated"
        )
        with self.assertRaises(IntegrityError):
            LessonVariant.objects.create(
                learning_object=self.original, variant="SIMPLIFIED", narration="Two", origin="generated"
            )

    def test_elaborated_slot_is_unique_too(self):
        LessonVariant.objects.create(
            learning_object=self.original, variant="ELABORATED", narration="Third PDF wording", origin="source_pdf"
        )
        with self.assertRaises(IntegrityError):
            LessonVariant.objects.create(
                learning_object=self.original, variant="ELABORATED", narration="Fourth PDF wording", origin="source_pdf"
            )

    def test_unassigned_wording_cannot_be_saved_as_a_version(self):
        for invalid_role in ("NEEDS_REVIEW", "EXTRA"):
            with self.subTest(invalid_role=invalid_role), self.assertRaises(IntegrityError), transaction.atomic():
                LessonVariant.objects.create(
                    learning_object=self.original,
                    variant=invalid_role,
                    narration="Unassigned PDF wording",
                )

    def test_variant_can_be_written_before_the_lesson_package_exists(self):
        # No CourseModule or LessonNode has been created for this material.
        LessonVariant.objects.create(
            learning_object=self.original, variant="ELABORATED", narration="Longer wording", origin="generated"
        )
        self.assertEqual(self.original.variants.count(), 1)
