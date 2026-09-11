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
        self.assertFalse(group["versions"]["classification_complete"])
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
        group = response.data["learning_object_groups"][0]
        self.assertEqual(group["versions"]["needs_confirmation"], [])
        self.assertEqual(
            group["versions"]["slots"]["simplified"]["source_learning_object_id"],
            self.second.id,
        )

    def test_teacher_can_move_a_source_without_leaving_it_in_two_slots(self):
        url = (
            f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/"
            "version-assignment/"
        )
        self.client.post(
            url,
            {"learning_object_id": self.second.id, "slot": "SIMPLIFIED"},
            format="json",
        )

        response = self.client.post(
            url,
            {"learning_object_id": self.second.id, "slot": "ELABORATED"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        rows = LessonVariant.objects.filter(
            learning_object=self.first,
            source_learning_object=self.second,
        )
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.get().variant, "ELABORATED")

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
            {"learning_object_id": self.second.id, "slot": "ORIGINAL"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_teacher_can_swap_a_pdf_source_into_normal(self):
        url = f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/version-assignment/"
        self.client.post(
            url,
            {"learning_object_id": self.second.id, "slot": "SIMPLIFIED"},
            format="json",
        )
        LessonVariant.objects.create(
            learning_object=self.first,
            variant="ELABORATED",
            narration="Generated from the old Normal.",
            origin=LessonVariant.Origin.GENERATED,
        )

        response = self.client.post(
            url,
            {"learning_object_id": self.second.id, "slot": "NORMAL"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        group = response.data["learning_object_groups"][0]
        self.assertEqual(group["versions"]["representative_id"], self.second.id)
        self.first.refresh_from_db()
        self.second.refresh_from_db()
        self.assertEqual(self.first.represented_by, self.second)
        self.assertIsNone(self.second.represented_by)
        swapped = LessonVariant.objects.get(
            learning_object=self.second,
            variant="SIMPLIFIED",
        )
        self.assertEqual(swapped.source_learning_object, self.first)
        self.assertFalse(
            LessonVariant.objects.filter(
                learning_object=self.second,
                variant="ELABORATED",
            ).exists()
        )

    def test_replacing_simplified_preserves_previous_source_as_extra(self):
        third = self._object("PDF three", timezone.now(), "A solid keeps its own shape.")
        url = f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/version-assignment/"
        first_response = self.client.post(url, {"learning_object_id": self.second.id, "slot": "SIMPLIFIED"}, format="json")
        self.assertEqual(first_response.status_code, 200)
        response = self.client.post(url, {"learning_object_id": third.id, "slot": "SIMPLIFIED"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["version_assignment"]["moved_to_extra"], self.second.id)
        self.assertEqual(LessonVariant.objects.get(learning_object=self.first, variant="SIMPLIFIED").source_learning_object_id, third.id)
        extra = LessonVariant.objects.get(learning_object=self.first, variant="EXTRA")
        self.assertEqual(extra.source_learning_object_id, self.second.id)
        self.assertEqual(extra.narration, self.second.content)
        response = self.client.post(url, {"learning_object_id": self.second.id, "slot": "SIMPLIFIED"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(LessonVariant.objects.filter(learning_object=self.first).count(), 2)

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
