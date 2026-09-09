from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from course.models import LessonVariant
from course.variant_generator import VariantGenerationError

from .models import CourseGroup, LearningMaterial, LearningObject, LearningObjectGroup, OutlineNode


SHORT = "Solid has a fixed shape. It holds its form. It does not flow."
LONG = (
    "A solid is a state of matter that maintains a fixed shape and a fixed volume. "
    "The particles inside it are packed tightly together in a regular arrangement. "
    "Because those particles cannot move past one another, a solid does not flow."
)


@override_settings(
    ADAPTIVE_VARIANT_GENERATION_ENABLED=True,
    ADAPTIVE_VARIANT_LLM_MODEL="gemma3:4b",
)
class VersionEditingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="teacher", password="pw", role="TEACHER"
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
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
            course=self.course,
            outline_node=self.node,
            title=title,
            generated_json={"learning_objects_confirmed": True},
        )
        LearningMaterial.objects.filter(pk=material.pk).update(created_at=created_at)
        material.refresh_from_db()
        return LearningObject.objects.create(
            material=material, group=self.group, title="Solid", content=content, order=0
        )

    def _generate_url(self, obj):
        return (
            f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}"
            f"/learning-objects/{obj.id}/generate-versions/"
        )

    @patch("course.variant_generator._request_variants")
    def test_generates_the_missing_slot_for_a_representative(self, request_variants):
        request_variants.return_value = {"SIMPLIFIED": "Solid keeps shape.", "ELABORATED": "ignored"}

        response = self.client.post(self._generate_url(self.first), {}, format="json")

        self.assertEqual(response.status_code, 200)
        row = LessonVariant.objects.get(learning_object=self.first, variant="SIMPLIFIED")
        self.assertEqual(row.narration, "Solid keeps shape.")
        self.assertEqual(row.origin, "generated")

    @patch("course.variant_generator._request_variants")
    def test_refuses_to_generate_for_a_represented_object(self, request_variants):
        LearningObject.objects.filter(pk=self.second.pk).update(represented_by=self.first)

        response = self.client.post(self._generate_url(self.second), {}, format="json")

        self.assertEqual(response.status_code, 400)
        request_variants.assert_not_called()

    @patch("course.variant_generator._request_variants")
    def test_generation_failure_is_reported_not_raised(self, request_variants):
        request_variants.side_effect = VariantGenerationError("Gemma did not return valid JSON.")

        response = self.client.post(self._generate_url(self.first), {}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["version_generation"]["errors"])

    def test_object_outside_the_topic_is_rejected(self):
        stranger = self._stranger_object()
        response = self.client.post(
            f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}"
            f"/learning-objects/{stranger.id}/generate-versions/",
            {},
            format="json",
        )
        self.assertEqual(response.status_code, 404)

    def _stranger_object(self):
        other_course = CourseGroup.objects.create(title="Other")
        other_node = OutlineNode.objects.create(
            course=other_course, title="Elsewhere", order=0, depth=0
        )
        other_material = LearningMaterial.objects.create(
            course=other_course, outline_node=other_node, title="X"
        )
        return LearningObject.objects.create(
            material=other_material, title="X", content="Y", order=0
        )

    def _variant(self, slot="SIMPLIFIED", origin="generated"):
        return LessonVariant.objects.create(
            learning_object=self.first, variant=slot, narration="Machine wording.", origin=origin
        )

    def _edit_url(self, variant):
        return (
            f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}"
            f"/versions/{variant.id}/"
        )

    def test_teacher_can_reword_a_version(self):
        row = self._variant()

        response = self.client.post(
            self._edit_url(row), {"narration": "A solid keeps its shape."}, format="json"
        )

        self.assertEqual(response.status_code, 200)
        row.refresh_from_db()
        self.assertEqual(row.narration, "A solid keeps its shape.")
        self.assertEqual(row.assigned_by, "teacher")

    def test_editing_preserves_where_the_text_came_from(self):
        row = self._variant(origin="source_pdf")

        self.client.post(self._edit_url(row), {"narration": "Reworded."}, format="json")

        row.refresh_from_db()
        self.assertEqual(row.origin, "source_pdf")

    def test_blank_narration_is_rejected(self):
        row = self._variant()

        response = self.client.post(self._edit_url(row), {"narration": "   "}, format="json")

        self.assertEqual(response.status_code, 400)
        row.refresh_from_db()
        self.assertEqual(row.narration, "Machine wording.")

    def test_variant_from_another_topic_is_rejected(self):
        stranger = self._stranger_object()
        row = LessonVariant.objects.create(
            learning_object=stranger, variant="SIMPLIFIED", narration="Z", origin="generated"
        )
        response = self.client.post(
            f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/versions/{row.id}/",
            {"narration": "Nope."},
            format="json",
        )
        self.assertEqual(response.status_code, 404)

    def test_payload_exposes_extras_and_row_ids(self):
        extra = LessonVariant.objects.create(
            learning_object=self.first,
            variant="EXTRA",
            narration="Third PDF wording",
            origin="source_pdf",
            source_learning_object=self.second,
        )
        response = self.client.get(
            f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/learning-resources/"
        )
        versions = response.data["learning_object_groups"][0]["versions"]
        self.assertIn("extras", versions)
        self.assertEqual(versions["extras"][0]["id"], extra.id)
        self.assertEqual(versions["extras"][0]["text"], "Third PDF wording")

    def test_payload_slots_carry_row_ids(self):
        row = self._variant()
        response = self.client.get(
            f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/learning-resources/"
        )
        versions = response.data["learning_object_groups"][0]["versions"]
        self.assertEqual(versions["slots"]["simplified"]["id"], row.id)
