from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient
from unittest.mock import patch

from .models import CourseGroup, LearningMaterial, LearningObject, LearningObjectGroup, OutlineNode
from .services.source_review import finalize_source_review


class ManualConceptTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.teacher = get_user_model().objects.create_user(username="conceptteacher", role="TEACHER")
        self.client.force_authenticate(self.teacher)
        self.course = CourseGroup.objects.create(title="Science")
        self.node = OutlineNode.objects.create(course=self.course, title="Matter")
        self.material = LearningMaterial.objects.create(
            course=self.course, outline_node=self.node, title="Lesson", status="completed",
            generated_json={"learning_objects_confirmed": True},
        )
        self.source = LearningObjectGroup.objects.create(outline_node=self.node, label="Matter")
        self.target = LearningObjectGroup.objects.create(outline_node=self.node, label="Solid")
        self.object = LearningObject.objects.create(material=self.material, group=self.source,
                                                   title="Matter", content="Solid keeps its shape.")
        self.companion = LearningObject.objects.create(material=self.material, group=self.source,
                                                      title="Matter", content="Matter occupies space.")
        self.existing = LearningObject.objects.create(material=self.material, group=self.target,
                                                     title="Solid", content="Solids have fixed shape.")
        self.base = f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/"

    def move(self, **overrides):
        return self.client.post(self.base + "connect-learning-objects/", {
            "learning_object_ids": [self.object.id], "target_group_id": self.target.id, **overrides,
        }, format="json")

    def test_moves_only_chosen_object_to_explicit_destination_and_preserves_text(self):
        response = self.move()
        self.assertEqual(response.status_code, 200, response.data)
        self.object.refresh_from_db()
        self.companion.refresh_from_db()
        self.assertEqual(self.object.group_id, self.target.id)
        self.assertEqual(self.companion.group_id, self.source.id)
        self.assertEqual(self.object.content, "Solid keeps its shape.")
        self.assertTrue(self.object.grouping_content_hash)
        self.assertIn("learning_object_groups", response.data)
        self.material.refresh_from_db()
        snapshots = self.material.generated_json["learning_objects"]
        self.assertEqual(len(snapshots), 3)

    def test_separate_accepts_teacher_name_and_can_move_back(self):
        response = self.client.post(self.base + "separate-learning-object/", {
            "learning_object_id": self.object.id, "label": "Solid (state)",
        }, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.object.refresh_from_db()
        separated_id = self.object.group_id
        self.assertEqual(self.object.group.label, "Solid (state)")
        self.assertTrue(self.object.group.version_selection["label_locked"])
        visible = next(group for group in response.data["learning_object_groups"] if group["id"] == separated_id)
        self.assertEqual(visible["display_title"], "Solid (state)")
        self.assertEqual(self.move().status_code, 200)
        self.assertFalse(LearningObjectGroup.objects.filter(pk=separated_id).exists())

    def test_singleton_can_be_named_without_duplicate_groups(self):
        before = LearningObjectGroup.objects.count()
        response = self.client.post(self.base + "separate-learning-object/", {
            "learning_object_id": self.existing.id, "label": "Solids",
        }, format="json")
        self.assertEqual(response.status_code, 200)
        self.target.refresh_from_db()
        self.assertEqual(self.target.label, "Solids")
        self.assertEqual(LearningObjectGroup.objects.count(), before)

    def test_rejects_foreign_topic_destination_and_objects(self):
        foreign = OutlineNode.objects.create(course=self.course, title="Plants")
        group = LearningObjectGroup.objects.create(outline_node=foreign, label="Plants")
        self.assertEqual(self.move(target_group_id=group.id).status_code, 400)
        foreign_course = CourseGroup.objects.create(title="Other course")
        material = LearningMaterial.objects.create(course=foreign_course, title="Other")
        obj = LearningObject.objects.create(material=material, title="Other")
        self.assertEqual(self.move(learning_object_ids=[obj.id]).status_code, 400)
        self.object.refresh_from_db()
        self.assertEqual(self.object.group_id, self.source.id)

    def test_rejects_unconfirmed_and_finalized_sources(self):
        self.material.generated_json = {"learning_objects_confirmed": False}
        self.material.save()
        self.assertEqual(self.move().status_code, 400)
        self.material.generated_json = {"learning_objects_confirmed": True}
        self.material.save()
        finalize_source_review(self.node, user=self.teacher)
        self.assertEqual(self.move().status_code, 409)
        response = self.client.post(self.base + "separate-learning-object/", {
            "learning_object_id": self.object.id,
        }, format="json")
        self.assertEqual(response.status_code, 409)

    def test_rejects_invalid_ids_and_preserves_legacy_connect(self):
        for overrides in ({"learning_object_ids": [[1]]}, {"learning_object_ids": []},
                          {"target_group_id": "invalid"}, {"learning_object_ids": "1"}):
            self.assertEqual(self.move(**overrides).status_code, 400)
        response = self.client.post(self.base + "connect-learning-objects/", {
            "learning_object_ids": [self.object.id, self.existing.id],
        }, format="json")
        self.assertEqual(response.status_code, 200, response.data)

    def test_student_and_anonymous_cannot_correct(self):
        self.client.force_authenticate(None)
        self.assertIn(self.move().status_code, (401, 403))
        student = get_user_model().objects.create_user(username="conceptstudent", role="STUDENT")
        self.client.force_authenticate(student)
        self.assertEqual(self.move().status_code, 403)

    def test_destination_with_unconfirmed_source_is_rejected(self):
        draft = LearningMaterial.objects.create(course=self.course, outline_node=self.node, title="Draft")
        LearningObject.objects.create(material=draft, group=self.target, title="Unconfirmed")
        self.assertEqual(self.move().status_code, 400)
        self.object.refresh_from_db()
        self.assertEqual(self.object.group_id, self.source.id)

    def test_failed_snapshot_rolls_back_move(self):
        with patch("lessons.views.CourseGroupViewSet._refresh_relationship_snapshots", side_effect=RuntimeError("Failed")):
            with self.assertRaises(RuntimeError):
                self.move()
        self.object.refresh_from_db()
        self.assertEqual(self.object.group_id, self.source.id)

    def test_manual_move_survives_automatic_refresh(self):
        from .services.concept_grouper import refresh_material_learning_relationships
        self.assertEqual(self.move().status_code, 200)
        refresh_material_learning_relationships(self.material)
        self.object.refresh_from_db()
        self.assertEqual(self.object.group_id, self.target.id)
