from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from course.models import LessonVariant

from .models import CourseGroup, LearningMaterial, LearningObject, LearningObjectGroup, OutlineNode


SHORT = "Solid has a fixed shape. It holds its form. It does not flow."
MIDDLING = "A solid keeps its shape. The particles are packed closely. It will not flow away."


class VersionReviewTests(TestCase):
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
        self.second = self._object("PDF two", now + timedelta(minutes=5), MIDDLING)

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

    def _resources(self):
        return self.client.get(
            f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/learning-resources/"
        )

    def test_payload_reports_a_pair_awaiting_confirmation(self):
        response = self._resources()
        self.assertEqual(response.status_code, 200)
        group = response.data["learning_object_groups"][0]
        self.assertEqual(group["versions"]["representative_id"], self.first.id)
        self.assertEqual(len(group["versions"]["needs_confirmation"]), 1)
        self.assertFalse(group["versions"]["complete"])

    def test_teacher_can_assign_a_slot(self):
        response = self.client.post(
            f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/version-assignment/",
            {"learning_object_id": self.second.id, "slot": "SIMPLIFIED"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)

        row = LessonVariant.objects.get(learning_object=self.first, variant="SIMPLIFIED")
        self.assertEqual(row.narration, MIDDLING)
        self.assertEqual(row.origin, "source_pdf")
        self.assertEqual(row.assigned_by, "teacher")

    def test_assignment_flags_the_assigned_object(self):
        self.client.post(
            f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/version-assignment/",
            {"learning_object_id": self.second.id, "slot": "SIMPLIFIED"},
            format="json",
        )
        self.second.refresh_from_db()
        self.assertEqual(self.second.represented_by, self.first)

    def test_invalid_slot_is_rejected(self):
        response = self.client.post(
            f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/version-assignment/",
            {"learning_object_id": self.second.id, "slot": "NORMAL"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_object_outside_the_topic_is_rejected(self):
        other_course = CourseGroup.objects.create(title="Other")
        other_node = OutlineNode.objects.create(
            course=other_course, title="Elsewhere", order=0, depth=0
        )
        other_material = LearningMaterial.objects.create(
            course=other_course, outline_node=other_node, title="X"
        )
        stranger = LearningObject.objects.create(
            material=other_material, title="X", content="Y", order=0
        )

        response = self.client.post(
            f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/version-assignment/",
            {"learning_object_id": stranger.id, "slot": "SIMPLIFIED"},
            format="json",
        )
        self.assertEqual(response.status_code, 404)
