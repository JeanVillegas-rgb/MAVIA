from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from course.models import LessonVariant
from course.variant_generator import VariantGenerationError

from .models import CourseGroup, LearningMaterial, LearningObject, LearningObjectGroup, OutlineNode


@override_settings(
    ADAPTIVE_VARIANT_GENERATION_ENABLED=True,
    ADAPTIVE_VARIANT_LLM_MODEL="gemma3:4b",
)
class PublishVersionTests(TestCase):
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
        self.material = LearningMaterial.objects.create(
            course=self.course,
            outline_node=self.node,
            title="Lesson 1",
            generated_json={"learning_objects_confirmed": True},
        )
        group = LearningObjectGroup.objects.create(outline_node=self.node)
        self.obj = LearningObject.objects.create(
            material=self.material,
            group=group,
            title="Solid",
            content="A solid holds its shape and does not flow away from its place.",
            order=0,
        )

    def _publish(self):
        return self.client.post(
            f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/publish/"
        )

    @patch("lessons.views.populate_missing_image_descriptions")
    @patch("lessons.views.generate_material_audio_playlist")
    @patch("course.variant_generator._request_variants")
    def test_publish_reports_incomplete_versions(self, request_variants, audio, images):
        images.return_value = {"generated_count": 0, "errors": []}
        audio.return_value = {"generated_count": 0}
        request_variants.side_effect = VariantGenerationError("Gemma did not return valid JSON.")

        response = self._publish()

        self.assertEqual(response.status_code, 200)
        self.assertIn(self.obj.id, response.data["publish"]["incomplete_versions"])

    @patch("lessons.views.populate_missing_image_descriptions")
    @patch("lessons.views.generate_material_audio_playlist")
    @patch("course.variant_generator._request_variants")
    def test_already_settled_content_generates_nothing(self, request_variants, audio, images):
        images.return_value = {"generated_count": 0, "errors": []}
        audio.return_value = {"generated_count": 0}
        for slot in ("SIMPLIFIED", "ELABORATED"):
            LessonVariant.objects.create(
                learning_object=self.obj, variant=slot, narration=f"{slot} text", origin="source_pdf"
            )

        response = self._publish()

        request_variants.assert_not_called()
        self.assertEqual(response.data["publish"]["incomplete_versions"], [])
