from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from lessons.models import (
    CourseGroup,
    LearningMaterial,
    LearningObject,
    LearningObjectGroup,
    OutlineNode,
)

from .models import LessonVariant
from .version_assignment import release_learning_object, settle_group


SHORT = "Solid has a fixed shape. It holds its form. It does not flow."
LONG = (
    "A solid is a state of matter that maintains a fixed shape and a fixed volume. "
    "The particles inside it are packed tightly together in a regular arrangement. "
    "Because those particles cannot move past one another, a solid does not flow."
)
MIDDLING = "A solid keeps its shape. The particles are packed closely. It will not flow away."


@override_settings(
    ADAPTIVE_VARIANT_GENERATION_ENABLED=True,
    ADAPTIVE_VARIANT_LLM_MODEL="gemma3:4b",
)
class RepresentationTests(TestCase):
    def setUp(self):
        classifier_patcher = patch("course.version_assignment.classify_group_versions")
        self.classify_group_versions = classifier_patcher.start()
        self.addCleanup(classifier_patcher.stop)
        def classify(members, representative=None):
            original = representative or members[0]
            return {
                item.id: {
                    "slot": "ORIGINAL" if item.id == original.id else "ELABORATED",
                    "confidence": 0.95,
                    "reason": "Balanced original." if item.id == original.id else "More detailed.",
                }
                for item in members
            }
        self.classify_group_versions.side_effect = classify
        self.course = CourseGroup.objects.create(title="Grade 1 Science")
        self.node = OutlineNode.objects.create(
            course=self.course, title="Matter", order=0, depth=0
        )
        self.group = LearningObjectGroup.objects.create(outline_node=self.node)
        now = timezone.now()
        self.first = self._object("PDF one", now, SHORT)
        self.second = self._object("PDF two", now + timedelta(minutes=5), LONG)

    def _object(self, title, created_at, content):
        material = LearningMaterial.objects.create(
            course=self.course, outline_node=self.node, title=title
        )
        LearningMaterial.objects.filter(pk=material.pk).update(created_at=created_at)
        material.refresh_from_db()
        return LearningObject.objects.create(
            material=material, group=self.group, title="Solid", content=content, order=0
        )

    @patch("course.variant_generator._request_variants")
    def test_partner_is_flagged_and_representative_is_not(self, request_variants):
        request_variants.return_value = {"SIMPLIFIED": "Solid keeps shape.", "ELABORATED": "x"}

        settle_group(self.group)

        self.first.refresh_from_db()
        self.second.refresh_from_db()
        self.assertIsNone(self.first.represented_by)
        self.assertEqual(self.second.represented_by, self.first)

    @patch("course.variant_generator._request_variants")
    def test_gap_is_filled_after_real_text_is_placed(self, request_variants):
        request_variants.return_value = {"SIMPLIFIED": "Solid keeps shape.", "ELABORATED": "ignored"}

        result = settle_group(self.group)

        self.assertEqual(result["generated"], ["SIMPLIFIED"])
        elaborated = LessonVariant.objects.get(learning_object=self.first, variant="ELABORATED")
        self.assertEqual(elaborated.origin, "source_pdf")
        simplified = LessonVariant.objects.get(learning_object=self.first, variant="SIMPLIFIED")
        self.assertEqual(simplified.origin, "generated")

    @patch("course.variant_generator._request_variants")
    def test_llm_selects_original_and_only_missing_slot_is_generated(self, request_variants):
        self.classify_group_versions.side_effect = None
        self.classify_group_versions.return_value = {
            self.first.id: {"slot": "SIMPLIFIED", "confidence": 0.96, "reason": "Clearer."},
            self.second.id: {"slot": "ORIGINAL", "confidence": 0.94, "reason": "Balanced."},
        }
        request_variants.return_value = {
            "SIMPLIFIED": "ignored",
            "ELABORATED": "A fuller generated explanation.",
        }

        result = settle_group(self.group)

        self.assertEqual(result["representative_id"], self.second.id)
        self.assertEqual(result["generated"], ["ELABORATED"])
        simplified = LessonVariant.objects.get(
            learning_object=self.second,
            variant="SIMPLIFIED",
        )
        self.assertEqual(simplified.source_learning_object, self.first)
        elaborated = LessonVariant.objects.get(
            learning_object=self.second,
            variant="ELABORATED",
        )
        self.assertEqual(elaborated.origin, "generated")

    @patch("course.variant_generator._request_variants")
    def test_release_takes_back_its_own_text_and_drops_generated_rows(self, request_variants):
        request_variants.return_value = {"SIMPLIFIED": "Solid keeps shape.", "ELABORATED": "ignored"}
        settle_group(self.group)

        release_learning_object(self.second)

        self.second.refresh_from_db()
        self.assertIsNone(self.second.represented_by)
        # The released object supplied the elaborated rung; it leaves with it.
        self.assertFalse(
            LessonVariant.objects.filter(
                learning_object=self.first, source_learning_object=self.second
            ).exists()
        )
        # The generated rung existed only to complete a triple that no longer
        # has a partner, so it goes too.
        self.assertFalse(
            LessonVariant.objects.filter(learning_object=self.first, origin="generated").exists()
        )

    @patch("course.variant_generator._request_variants")
    def test_release_keeps_text_supplied_by_other_members(self, request_variants):
        request_variants.return_value = {"SIMPLIFIED": "Solid keeps shape.", "ELABORATED": "ignored"}
        third = self._object(
            "PDF three",
            timezone.now() + timedelta(minutes=9),
            "A solid keeps a fixed shape at all times. The particles inside it are packed "
            "together very closely. It cannot flow the way that water does.",
        )
        settle_group(self.group)
        self.assertTrue(
            LessonVariant.objects.filter(
                learning_object=self.first, source_learning_object=third
            ).exists()
        )

        release_learning_object(self.second)

        # Another member's teacher-written text is untouched by this release.
        self.assertTrue(
            LessonVariant.objects.filter(
                learning_object=self.first, source_learning_object=third
            ).exists()
        )

    @patch("course.variant_generator._request_variants")
    def test_unconfident_member_is_not_flagged(self, request_variants):
        request_variants.return_value = {"SIMPLIFIED": "a", "ELABORATED": "b"}
        LearningObject.objects.filter(pk=self.second.pk).update(group=None)
        middling = self._object(
            "PDF three", timezone.now() + timedelta(minutes=9), MIDDLING
        )

        settle_group(self.group)

        middling.refresh_from_db()
        self.assertIsNone(middling.represented_by)
