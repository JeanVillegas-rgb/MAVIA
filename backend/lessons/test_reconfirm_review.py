"""Confirming an edited PDF again shows what it would change in approved PDFs first.

While a confirmed PDF is edited it is a draft, and nothing it does reaches
the approved PDFs (test_draft_isolation). Its effects are worked out once, on
reconfirm -- and a change to an approved PDF's concepts, like its "Solid"
being folded into "Matter" because the edited PDF no longer teaches solids,
is the teacher's to accept, not something to happen silently.
"""

from django.test import TestCase

from .models import CourseGroup, LearningMaterial, LearningObject, LearningObjectGroup, OutlineNode
from .services.learning_resource_linker import refresh_material_learning_relationships
from .tests import authenticated_api_client


class ReconfirmReviewTests(TestCase):
    def setUp(self):
        self.client = authenticated_api_client()
        self.course = CourseGroup.objects.create(title="Science")
        self.node = OutlineNode.objects.create(course=self.course, title="States of matter")
        self.approved = self.material("Approved")
        self.matter = self.add(self.approved, "Matter", "Matter", 0)
        self.solid = self.add(self.approved, "Solid", "Matter", 1)
        # The edited PDF also teaches solids, which is what keeps the approved
        # "Solid" a concept of its own.
        self.edited = self.material("Edited")
        self.solid_too = self.add(self.edited, "SOLID", "SOLID", 0, group=self.solid.group)
        self.gas = self.add(self.edited, "Gas", "Gas", 1)
        self.post(f"materials/{self.edited.id}/confirm-learning-objects/", {})

    def material(self, title):
        return LearningMaterial.objects.create(
            course=self.course, outline_node=self.node, title=title,
            status=LearningMaterial.Status.COMPLETED,
            generated_json={"learning_objects_confirmed": True},
        )

    def add(self, material, title, section, order, group=None):
        return LearningObject.objects.create(
            material=material, title=title, section_title=section, content=f"{title} text.",
            order=order,
            group=group or LearningObjectGroup.objects.create(outline_node=self.node, label=title),
        )

    def post(self, path, data):
        return self.client.post(f"/api/courses/{self.course.id}/{path}", data, format="json")

    def stop_teaching_solids(self):
        # Deleting it puts the edited PDF back in draft.
        self.client.delete(
            f"/api/courses/{self.course.id}/materials/{self.edited.id}/learning-objects/{self.solid_too.id}/"
        )

    def reconfirm(self, data=None):
        return self.post(f"materials/{self.edited.id}/confirm-learning-objects/", data or {})

    def confirmed(self, material):
        material.refresh_from_db()
        return bool(material.generated_json.get("learning_objects_confirmed"))

    def test_the_change_is_shown_and_nothing_is_applied(self):
        self.stop_teaching_solids()

        response = self.reconfirm()

        self.assertEqual(response.status_code, 200)
        changes = response.data["approved_changes"]
        self.assertEqual([change["learning_object_id"] for change in changes], [self.solid.id])
        self.assertIn("Matter", changes[0]["to_label"])
        self.assertFalse(self.confirmed(self.edited))
        self.solid.refresh_from_db()
        self.assertNotEqual(self.solid.group_id, self.matter.group_id)

    def test_applying_the_change_confirms_and_folds(self):
        self.stop_teaching_solids()

        response = self.reconfirm({"keep": []})

        self.assertNotIn("approved_changes", response.data)
        self.assertTrue(self.confirmed(self.edited))
        self.solid.refresh_from_db()
        self.assertEqual(self.solid.group_id, self.matter.group_id)

    def test_keeping_it_confirms_and_leaves_it_separate_for_good(self):
        self.stop_teaching_solids()

        self.reconfirm({"keep": [self.solid.id]})

        self.assertTrue(self.confirmed(self.edited))
        self.solid.refresh_from_db()
        self.assertNotEqual(self.solid.group_id, self.matter.group_id)
        # A later refresh of the approved PDF does not fold it after all.
        refresh_material_learning_relationships(self.approved)
        self.solid.refresh_from_db()
        self.assertNotEqual(self.solid.group_id, self.matter.group_id)

    def test_a_reconfirm_that_changes_nothing_approved_just_confirms(self):
        self.client.delete(
            f"/api/courses/{self.course.id}/materials/{self.edited.id}/learning-objects/{self.gas.id}/"
        )

        response = self.reconfirm()

        self.assertNotIn("approved_changes", response.data)
        self.assertTrue(self.confirmed(self.edited))

    def test_a_reconfirm_that_changes_nothing_approved_runs_once(self):
        from unittest.mock import patch
        from .services import learning_resource_linker as linker

        self.client.delete(
            f"/api/courses/{self.course.id}/materials/{self.edited.id}/learning-objects/{self.gas.id}/"
        )
        with patch.object(linker, "release_section_joins", wraps=linker.release_section_joins) as release:
            self.reconfirm()

        self.assertEqual(release.call_count, 1)

    def test_a_first_confirm_is_not_held_for_review(self):
        fresh = LearningMaterial.objects.create(
            course=self.course, outline_node=self.node, title="New",
            status=LearningMaterial.Status.COMPLETED,
            generated_json={"learning_objects_confirmed": False},
        )
        self.add(fresh, "Plasma", "Plasma", 0)

        response = self.post(f"materials/{fresh.id}/confirm-learning-objects/", {})

        self.assertNotIn("approved_changes", response.data)
        self.assertTrue(self.confirmed(fresh))
