from datetime import timedelta
from unittest.mock import patch

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

    @patch("course.version_assignment.classify_group_versions")
    def test_llm_and_readability_agreement_is_assigned_with_provenance(self, classify):
        first = self._object(self._material("PDF one", 0), SHORT)
        second = self._object(self._material("PDF two", 5), LONG)
        classify.return_value = {
            second.id: {"slot": "ELABORATED", "confidence": 0.94, "reason": "More explanation."}
        }

        result = assign_group_versions(self.group, use_llm=True)

        self.assertEqual(result["representative_id"], first.id)
        self.assertEqual(len(result["assigned"]), 1)
        self.assertEqual(result["needs_confirmation"], [])

        row = LessonVariant.objects.get(learning_object=first)
        self.assertEqual(row.variant, "ELABORATED")
        self.assertEqual(row.narration, LONG)
        self.assertEqual(row.origin, "source_pdf")
        self.assertEqual(row.source_learning_object, second)
        self.assertEqual(row.assigned_by, "llm_validated")

    @patch("course.version_assignment.classify_group_versions")
    def test_llm_disagreement_is_routed_to_teacher(self, classify):
        first = self._object(self._material("PDF one", 0), SHORT)
        second = self._object(self._material("PDF two", 5), LONG)
        classify.return_value = {
            second.id: {"slot": "SIMPLIFIED", "confidence": 0.99, "reason": "Model guess."}
        }

        result = assign_group_versions(self.group, use_llm=True)

        self.assertEqual(result["assigned"], [])
        self.assertEqual(result["needs_confirmation"][0]["llm_slot"], "SIMPLIFIED")
        self.assertFalse(LessonVariant.objects.exists())

    def test_thin_margin_is_routed_to_the_teacher_not_stored(self):
        first = self._object(self._material("PDF one", 0), SHORT)
        self._object(self._material("PDF two", 5), MIDDLING)

        result = assign_group_versions(self.group)

        self.assertEqual(result["assigned"], [])
        self.assertEqual(len(result["needs_confirmation"]), 1)
        self.assertFalse(result["needs_confirmation"][0]["confident"])
        self.assertFalse(LessonVariant.objects.filter(learning_object=first).exists())

    @patch("course.version_assignment.classify_group_versions")
    def test_slot_collision_keeps_the_larger_margin_and_stores_an_extra(self, classify):
        first = self._object(self._material("PDF one", 0), SHORT)
        bigger = self._object(self._material("PDF two", 5), LONG)
        # Also confidently "elaborated", but by a narrower Flesch-Kincaid
        # margin than LONG, so it loses the slot and becomes an extra.
        smaller = self._object(
            self._material("PDF three", 10),
            "A solid keeps a fixed shape at all times. The particles inside it are packed "
            "together very closely. It cannot flow the way that water does.",
        )

        classify.return_value = {
            bigger.id: {"slot": "ELABORATED", "confidence": 0.96, "reason": "Fuller."},
            smaller.id: {"slot": "ELABORATED", "confidence": 0.91, "reason": "Also fuller."},
        }
        result = assign_group_versions(self.group, use_llm=True)

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

    @patch("course.version_assignment.classify_group_versions")
    def test_reassignment_is_idempotent(self, classify):
        self._object(self._material("PDF one", 0), SHORT)
        second = self._object(self._material("PDF two", 5), LONG)
        classify.return_value = {
            second.id: {"slot": "ELABORATED", "confidence": 0.95, "reason": "Fuller."}
        }

        assign_group_versions(self.group, use_llm=True)
        assign_group_versions(self.group, use_llm=True)

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
