from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from .models import CourseGroup, LearningMaterial, LearningObject, LearningObjectGroup, OutlineNode


class SourceReviewGateTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            username="source_gate_teacher",
            password="test",
            role="TEACHER",
            is_verified=True,
        )
        self.client = APIClient()
        self.client.force_authenticate(user)
        self.course = CourseGroup.objects.create(title="Science")
        self.topic = OutlineNode.objects.create(course=self.course, title="Matter")
        self.material = LearningMaterial.objects.create(
            course=self.course,
            outline_node=self.topic,
            title="Matter lesson",
            generated_json={"learning_objects_confirmed": True},
            status=LearningMaterial.Status.COMPLETED,
        )
        group = LearningObjectGroup.objects.create(outline_node=self.topic, label="Matter")
        self.learning_object = LearningObject.objects.create(
            material=self.material,
            group=group,
            title="Matter",
            content="Matter has mass and occupies space.",
        )

    def finalize(self):
        return self.client.post(
            f"/api/courses/{self.course.id}/outline-nodes/{self.topic.id}/finalize-sources/"
        )

    def test_finalization_is_persisted_and_returned(self):
        response = self.finalize()

        self.assertEqual(response.status_code, 200)
        topic = next(node for node in response.data["hierarchy"] if node["id"] == self.topic.id)
        self.assertTrue(topic["source_review"]["finalized"])
        self.topic.refresh_from_db()
        self.assertTrue(self.topic.related_info["source_review_finalized_at"])

    def test_finalized_sources_reject_source_changes(self):
        self.assertEqual(self.finalize().status_code, 200)

        edit = self.client.patch(
            f"/api/courses/{self.course.id}/materials/{self.material.id}/"
            f"learning-objects/{self.learning_object.id}/",
            {"title": "Changed"},
            format="json",
        )
        delete = self.client.delete(
            f"/api/courses/{self.course.id}/materials/{self.material.id}/"
        )
        upload = self.client.post(
            f"/api/courses/{self.course.id}/upload-material/",
            {
                "outline_node_id": self.topic.id,
                "pdf_file": SimpleUploadedFile("more.pdf", b"%PDF-1.4", content_type="application/pdf"),
            },
            format="multipart",
        )

        self.assertEqual(edit.status_code, 409)
        self.assertEqual(delete.status_code, 409)
        self.assertEqual(upload.status_code, 400)
        self.assertIn("finalized", upload.data["detail"].lower())

    def test_unconfirmed_lesson_cannot_be_finalized(self):
        self.material.generated_json = {"learning_objects_confirmed": False}
        self.material.save(update_fields=["generated_json"])

        response = self.finalize()

        self.assertEqual(response.status_code, 400)
        self.assertIn("confirm every lesson pdf", response.data["detail"].lower())
