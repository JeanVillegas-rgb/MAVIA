"""The teacher's Generate button must cover the whole Standard bundle.

A concept's Standard version can be several objects of one PDF -- a comparison
section written as Shape, Volume, Particle arrangement and Flow. The review
screen shows a slot as written only once every one of them has a version, but
the button generated for the representative alone. So it wrote one row of four,
the screen went on saying "Not written yet", and pressing it again found that
object already done and wrote nothing. The slot could never be filled.

The bulk path already used the bundle-aware generator; only this button did not.
"""

from unittest.mock import patch

from django.test import TestCase

from course.models import LessonVariant
from course.testing import without_measurements
from course.version_assignment import assign_group_versions

from .models import (
    CourseGroup,
    LearningMaterial,
    LearningObject,
    LearningObjectGroup,
    OutlineNode,
)
from .tests import authenticated_api_client


class GenerateButtonBundleTests(TestCase):
    def setUp(self):
        without_measurements(self)
        self.client = authenticated_api_client()
        self.course = CourseGroup.objects.create(title="Science")
        self.topic = OutlineNode.objects.create(course=self.course, title="States")
        confirmed = {"learning_objects_confirmed": True}
        self.first = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="A", generated_json=dict(confirmed))
        self.second = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="B", generated_json=dict(confirmed))
        self.group = LearningObjectGroup.objects.create(outline_node=self.topic, label="Comparing")

        # The Standard version is one PDF's comparison section: four objects.
        self.standard = [
            LearningObject.objects.create(
                material=self.first, group=self.group, title=title, order=index,
                section_title="Comparing", content=content,
            )
            for index, (title, content) in enumerate((
                ("Shape", "Solids keep their shape; liquids and gases take their container's."),
                ("Volume", "Solids and liquids have definite volume; gases expand to fill space."),
                ("Particle arrangement", "Solid particles are packed tightly; gas particles are far apart."),
                ("Flow", "Liquids and gases can flow, while solids normally do not."),
            ))
        ]
        self.elaborated = LearningObject.objects.create(
            material=self.second, group=self.group, title="Comparing the Three States", order=0,
            section_title="Comparing",
            content=(
                "The table below summarises the key differences between the three states, "
                "listing shape, volume, particle spacing, movement and compressibility "
                "for each one in turn so they can be contrasted directly."
            ),
        )
        with patch("course.version_assignment.classify_group_versions") as classify:
            classify.return_value = {
                self.standard[0].id: {"slot": "ORIGINAL", "confidence": 0.95, "reason": "Baseline."},
                self.elaborated.id: {"slot": "ELABORATED", "confidence": 0.9, "reason": "Fuller."},
            }
            assign_group_versions(self.group, use_llm=True)

    def generate(self, slot="SIMPLIFIED"):
        state = assign_group_versions(self.group)
        return self.client.post(
            f"/api/courses/{self.course.id}/outline-nodes/{self.topic.id}"
            f"/learning-objects/{state['representative_id']}/generate-versions/",
            {"slot": slot}, format="json",
        )

    def test_every_object_of_the_standard_bundle_gets_the_version(self):
        with patch("course.variant_generator._request_variants") as request:
            request.return_value = {"SIMPLIFIED": "Short.", "ELABORATED": "Longer."}
            response = self.generate()

        self.assertEqual(response.status_code, 200, getattr(response, "data", None))
        written = set(
            LessonVariant.objects.filter(
                learning_object__in=self.standard, variant="SIMPLIFIED",
            ).values_list("learning_object_id", flat=True)
        )
        self.assertEqual(written, {item.id for item in self.standard})

    def test_a_second_press_does_not_leave_the_slot_half_written(self):
        """The stuck state: one row written, three missing, and no way forward."""
        with patch("course.variant_generator._request_variants") as request:
            request.return_value = {"SIMPLIFIED": "Short.", "ELABORATED": "Longer."}
            self.generate()
            self.generate()

        self.assertEqual(
            LessonVariant.objects.filter(
                learning_object__in=self.standard, variant="SIMPLIFIED",
            ).count(),
            len(self.standard),
        )

    def test_a_role_a_pdf_already_supplies_is_not_generated_over(self):
        """Elaborated comes from the second PDF, so nothing should write it."""
        with patch("course.variant_generator._request_variants") as request:
            request.return_value = {"SIMPLIFIED": "Short.", "ELABORATED": "Longer."}
            self.generate(slot="ELABORATED")

        self.assertFalse(
            LessonVariant.objects.filter(
                learning_object__in=self.standard, variant="ELABORATED",
            ).exists()
        )
