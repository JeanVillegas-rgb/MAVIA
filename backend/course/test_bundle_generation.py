"""Missing versions are written one object at a time.

A long bundle in one call is where Gemma returns invalid JSON; per object the
calls stay short and one failure costs one object, not a concept.
"""

from unittest.mock import patch

from django.test import TestCase

from lessons.models import (
    CourseGroup,
    LearningMaterial,
    LearningObject,
    LearningObjectGroup,
    OutlineNode,
)

from .models import LessonVariant
from .variant_generator import VariantGenerationError, fill_missing_bundle_slots
from .version_assignment import assign_group_versions


class BundleGenerationTests(TestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Science")
        self.topic = OutlineNode.objects.create(course=self.course, title="States")
        confirmed = {"learning_objects_confirmed": True}
        self.first = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="A", generated_json=dict(confirmed))
        self.second = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="B", generated_json=dict(confirmed))
        self.group = LearningObjectGroup.objects.create(outline_node=self.topic, label="Solid")
        self.normal = LearningObject.objects.create(
            material=self.first, group=self.group, title="Solid", order=0,
            content=(
                "A solid has a definite shape and a definite volume because its constituent "
                "particles occupy fixed positions within a rigid lattice arrangement."
            ),
        )
        self.normal_tail = LearningObject.objects.create(
            material=self.first, group=self.group, title="Particle diagram", order=1,
            section_title="Solid", content="Particles sit in a grid and vibrate in place.",
        )
        self.simple = LearningObject.objects.create(
            material=self.second, group=self.group, title="Solids", order=0, section_title="Solids",
            content="In a solid, bits are packed tight. They stay in place.",
        )
        # SIMPLIFIED comes from the second PDF. Changed 2026-09-21: a plain
        # read proposes a role but records none, so the role is put on the
        # record by a classification run -- which is what `settle_group`, the
        # only caller of `fill_missing_bundle_slots`, does before calling it.
        with patch("course.version_assignment.classify_group_versions") as classify:
            classify.return_value = {
                self.normal.id: {"slot": "ORIGINAL", "confidence": 0.95, "reason": "Baseline."},
                self.simple.id: {"slot": "SIMPLIFIED", "confidence": 0.9, "reason": "Plainer."},
            }
            assign_group_versions(self.group, use_llm=True)

    def test_each_normal_object_gets_its_own_generated_row(self):
        with patch("course.variant_generator._request_variants") as request:
            request.return_value = {"SIMPLIFIED": "Short.", "ELABORATED": "Longer text."}
            outcome = fill_missing_bundle_slots(self.group)

        self.assertEqual(request.call_count, 2)
        rows = LessonVariant.objects.filter(
            learning_object__in=[self.normal, self.normal_tail], variant="ELABORATED",
        ).order_by("learning_object__order")
        self.assertEqual([row.learning_object_id for row in rows], [self.normal.id, self.normal_tail.id])
        self.assertEqual(outcome["errors"], [])

    def test_a_failure_on_one_object_leaves_the_others(self):
        def flaky(learning_object, model):
            if learning_object.id == self.normal.id:
                raise VariantGenerationError("Gemma did not return valid JSON.")
            return {"SIMPLIFIED": "Short.", "ELABORATED": "Longer text."}

        with patch("course.variant_generator._request_variants", side_effect=flaky):
            outcome = fill_missing_bundle_slots(self.group)

        self.assertEqual(len(outcome["errors"]), 1)
        self.assertTrue(
            LessonVariant.objects.filter(learning_object=self.normal_tail, variant="ELABORATED").exists()
        )

    def test_a_role_supplied_by_a_pdf_is_never_generated(self):
        with patch("course.variant_generator._request_variants") as request:
            request.return_value = {"SIMPLIFIED": "Short.", "ELABORATED": "Longer text."}
            fill_missing_bundle_slots(self.group)

        self.assertFalse(
            LessonVariant.objects.filter(
                learning_object__in=[self.normal, self.normal_tail], variant="SIMPLIFIED",
            ).exists()
        )


class PendingBundleDoesNotSuppressGenerationTests(TestCase):
    """Carried finding from Task 4's review.

    ``settle_group`` used to compute its "supplied" roles from every stored
    bundle role, including a bundle still awaiting teacher confirmation. That
    let an unconfirmed Simplified both suppress generation (because a role
    was stored for it) and never be shown to students (because it was not
    collapsed while pending) -- so the concept ended up with no Simplified at
    all. A pending role must not count as supplied.
    """

    def setUp(self):
        self.course = CourseGroup.objects.create(title="Science")
        self.topic = OutlineNode.objects.create(course=self.course, title="States")
        confirmed = {"learning_objects_confirmed": True}
        self.first = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="A", generated_json=dict(confirmed))
        self.second = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="B", generated_json=dict(confirmed))
        self.group = LearningObjectGroup.objects.create(outline_node=self.topic, label="Solid")
        # A thin readability margin (see readability.compare) leaves the
        # second bundle's role unconfirmed rather than assigned outright.
        self.normal = LearningObject.objects.create(
            material=self.first, group=self.group, title="Solid", order=0,
            content="A solid keeps its shape. The particles are packed closely. It will not flow away.",
        )
        self.thin_margin = LearningObject.objects.create(
            material=self.second, group=self.group, title="Solids", order=0,
            content="Solid has a fixed shape. It holds its form. It does not flow.",
        )
        outcome = assign_group_versions(self.group)
        self.assertEqual(len(outcome["needs_confirmation"]), 1)
        self.assertFalse(outcome["needs_confirmation"][0]["confident"])
        # The thin-margin bundle is nonetheless stored under a role already,
        # which is exactly what must not be treated as "supplied".
        self.assertEqual(outcome["bundle_roles"].get(self.second.id), "SIMPLIFIED")

    def test_unconfirmed_simplified_does_not_stop_simplified_being_generated(self):
        with patch("course.variant_generator._request_variants") as request:
            request.return_value = {"SIMPLIFIED": "Short.", "ELABORATED": "Longer text."}
            outcome = fill_missing_bundle_slots(self.group)

        self.assertIn("SIMPLIFIED", outcome["generated"])
        self.assertTrue(
            LessonVariant.objects.filter(learning_object=self.normal, variant="SIMPLIFIED").exists()
        )
