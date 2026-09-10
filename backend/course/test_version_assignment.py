from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from lessons.models import (
    CourseGroup,
    LearningMaterial,
    LearningObject,
    LearningObjectGroup,
    OutlineNode,
)

from .models import LessonVariant
from .version_assignment import assign_group_versions, choose_representative


SHORT = "Solid has a fixed shape. It holds its form. It does not flow."
LONG = (
    "A solid is a state of matter that maintains a fixed shape and a fixed volume. "
    "The particles inside it are packed tightly together in a regular arrangement. "
    "Because those particles cannot move past one another, a solid does not flow."
)
MIDDLING = "A solid keeps its shape. The particles are packed closely. It will not flow away."


class VersionAssignmentTests(TestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Grade 1 Science")
        self.node = OutlineNode.objects.create(
            course=self.course, title="Matter", order=0, depth=0
        )
        self.group = LearningObjectGroup.objects.create(outline_node=self.node)
        self.now = timezone.now()

    def _material(self, title, minutes_offset):
        material = LearningMaterial.objects.create(
            course=self.course, outline_node=self.node, title=title
        )
        LearningMaterial.objects.filter(pk=material.pk).update(
            created_at=self.now + timedelta(minutes=minutes_offset)
        )
        material.refresh_from_db()
        return material

    def _object(self, material, content, title="Solid"):
        return LearningObject.objects.create(
            material=material, group=self.group, title=title, content=content, order=0
        )

    def test_representative_is_the_earliest_uploaded_member(self):
        first = self._object(self._material("PDF one", 0), SHORT)
        second = self._object(self._material("PDF two", 5), LONG)
        self.assertEqual(choose_representative([second, first]), first)

    def test_confident_partner_is_assigned_and_stored_with_provenance(self):
        first = self._object(self._material("PDF one", 0), SHORT)
        second = self._object(self._material("PDF two", 5), LONG)

        result = assign_group_versions(self.group)

        self.assertEqual(result["representative_id"], first.id)
        self.assertEqual(len(result["assigned"]), 1)
        self.assertEqual(result["needs_confirmation"], [])

        row = LessonVariant.objects.get(learning_object=first)
        self.assertEqual(row.variant, "ELABORATED")
        self.assertEqual(row.narration, LONG)
        self.assertEqual(row.origin, "source_pdf")
        self.assertEqual(row.source_learning_object, second)

    def test_thin_margin_is_routed_to_the_teacher_not_stored(self):
        first = self._object(self._material("PDF one", 0), SHORT)
        self._object(self._material("PDF two", 5), MIDDLING)

        result = assign_group_versions(self.group)

        self.assertEqual(result["assigned"], [])
        self.assertEqual(len(result["needs_confirmation"]), 1)
        self.assertFalse(result["needs_confirmation"][0]["confident"])
        self.assertFalse(LessonVariant.objects.filter(learning_object=first).exists())

    def test_slot_collision_keeps_the_larger_margin_and_stores_an_extra(self):
        first = self._object(self._material("PDF one", 0), SHORT)
        bigger = self._object(self._material("PDF two", 5), LONG)
        # Also confidently "elaborated", but by a narrower Flesch-Kincaid
        # margin than LONG, so it loses the slot and becomes an extra.
        smaller = self._object(
            self._material("PDF three", 10),
            "A solid keeps a fixed shape at all times. The particles inside it are packed "
            "together very closely. It cannot flow the way that water does.",
        )

        result = assign_group_versions(self.group)

        elaborated = LessonVariant.objects.get(learning_object=first, variant="ELABORATED")
        self.assertEqual(elaborated.source_learning_object, bigger)
        extra = LessonVariant.objects.get(learning_object=first, variant="EXTRA")
        self.assertEqual(extra.source_learning_object, smaller)
        self.assertEqual(result["extras"], 1)

    def test_singleton_group_assigns_nothing(self):
        self._object(self._material("PDF one", 0), SHORT)
        result = assign_group_versions(self.group)
        self.assertEqual(result["assigned"], [])
        self.assertEqual(result["needs_confirmation"], [])

    def test_reassignment_is_idempotent(self):
        self._object(self._material("PDF one", 0), SHORT)
        self._object(self._material("PDF two", 5), LONG)

        assign_group_versions(self.group)
        assign_group_versions(self.group)

        self.assertEqual(LessonVariant.objects.filter(variant="ELABORATED").count(), 1)

    def test_saved_teacher_decision_is_not_returned_for_confirmation_again(self):
        first = self._object(self._material("PDF one", 0), SHORT)
        second = self._object(self._material("PDF two", 5), MIDDLING)
        LessonVariant.objects.create(
            learning_object=first,
            variant="SIMPLIFIED",
            narration=second.content,
            origin=LessonVariant.Origin.SOURCE_PDF,
            source_learning_object=second,
            assigned_by=LessonVariant.AssignedBy.TEACHER,
        )

        result = assign_group_versions(self.group)

        self.assertEqual(result["needs_confirmation"], [])
        self.assertEqual(result["assigned"][0]["learning_object_id"], second.id)
        self.assertTrue(result["assigned"][0]["persisted"])
