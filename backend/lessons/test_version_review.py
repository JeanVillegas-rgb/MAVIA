from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from course.models import LessonVariant
from course.version_assignment import bundle_roles

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

        # Changed 2026-09-20: roles are per bundle; a PDF-supplied version is its own objects.
        self.group.refresh_from_db()
        self.assertEqual(bundle_roles(self.group), {self.second.material_id: "SIMPLIFIED"})
        self.assertEqual(
            self.group.version_selection["bundle_roles_assigned_by"][
                str(self.second.material_id)
            ],
            "teacher",
        )
        self.assertFalse(
            LessonVariant.objects.filter(origin=LessonVariant.Origin.SOURCE_PDF).exists()
        )
        group = response.data["learning_object_groups"][0]
        self.assertEqual(group["versions"]["needs_confirmation"], [])

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
        # Changed 2026-09-20: roles are per bundle; a PDF-supplied version is its own objects.
        self.group.refresh_from_db()
        self.assertEqual(bundle_roles(self.group), {self.second.material_id: "ELABORATED"})

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
        # Changed 2026-09-20: roles are per bundle; a PDF-supplied version is its own objects.
        self.group.refresh_from_db()
        self.assertEqual(bundle_roles(self.group), {self.first.material_id: "SIMPLIFIED"})
        # Wording generated against the old Normal cannot survive the swap.
        self.assertFalse(
            LessonVariant.objects.filter(variant="ELABORATED").exists()
        )

    def test_replacing_simplified_moves_the_previous_source_out_of_the_slot(self):
        third = self._object("PDF three", timezone.now(), "A solid keeps its own shape.")
        url = f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/version-assignment/"
        first_response = self.client.post(url, {"learning_object_id": self.second.id, "slot": "SIMPLIFIED"}, format="json")
        self.assertEqual(first_response.status_code, 200)
        response = self.client.post(url, {"learning_object_id": third.id, "slot": "SIMPLIFIED"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["version_assignment"]["moved_to_extra"], self.second.id)
        # Changed 2026-09-20: roles are per bundle; a PDF-supplied version is its own objects.
        self.group.refresh_from_db()
        # Changed 2026-09-20: the displaced bundle is not erased and is not
        # stamped as the teacher's choice either -- its role is re-derived, so
        # it takes the primary slot its wording actually fits.
        self.assertEqual(
            bundle_roles(self.group),
            {third.material_id: "SIMPLIFIED", self.second.material_id: "ELABORATED"},
        )
        response = self.client.post(url, {"learning_object_id": self.second.id, "slot": "SIMPLIFIED"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.group.refresh_from_db()
        self.assertEqual(
            bundle_roles(self.group),
            {self.second.material_id: "SIMPLIFIED", third.material_id: "ELABORATED"},
        )

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
