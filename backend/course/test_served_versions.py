"""Learners get a PDF's version only once its role is confirmed.

A flagged or readability-only role is a candidate: it does not hold publishing
back and it is not served -- the written Simplified or Elaborated stands in.
"""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from lessons.models import CourseGroup, LearningMaterial, LearningObject, LearningObjectGroup, OutlineNode

from .models import LessonVariant
from .version_assignment import served_version_bundles, version_bundles


class ServedVersionBundlesTests(TestCase):
    def setUp(self):
        course = CourseGroup.objects.create(title="Science")
        node = OutlineNode.objects.create(course=course, title="States of matter")
        self.group = LearningObjectGroup.objects.create(outline_node=node, label="Solid")
        first = LearningMaterial.objects.create(
            course=course, outline_node=node, title="PDF one",
            generated_json={"learning_objects_confirmed": True},
        )
        self.second = LearningMaterial.objects.create(
            course=course, outline_node=node, title="PDF two",
            generated_json={"learning_objects_confirmed": True},
        )
        LearningMaterial.objects.filter(pk=self.second.pk).update(
            created_at=timezone.now() + timedelta(minutes=5),
        )
        LearningObject.objects.create(material=first, group=self.group, title="Solid", content="Solids keep their shape.")
        LearningObject.objects.create(
            material=self.second, group=self.group, title="Solid",
            content="Solids keep their own shape because their particles are tightly packed.",
        )

    def role(self, assigned_by):
        from .version_assignment import _eligible_bundles, _ordered_bundle_ids, _roles_signature

        bundles = _eligible_bundles(self.group)
        self.group.version_selection = {
            "bundle_roles": {str(self.second.id): "ELABORATED"},
            "bundle_roles_assigned_by": {str(self.second.id): assigned_by},
            # An automatic role counts only while the texts it was judged on
            # are unchanged; the signature records them.
            "roles_signature": _roles_signature(bundles, _ordered_bundle_ids(self.group, bundles)),
        }
        self.group.save()

    def test_a_teacher_or_validated_role_is_served(self):
        for who in (LessonVariant.AssignedBy.TEACHER, LessonVariant.AssignedBy.LLM_VALIDATED):
            self.role(who)
            self.assertIn("ELABORATED", served_version_bundles(self.group), msg=who)

    def test_an_unconfirmed_role_is_not_served(self):
        self.role(LessonVariant.AssignedBy.HEURISTIC)

        self.assertIn("ELABORATED", version_bundles(self.group))
        self.assertNotIn("ELABORATED", served_version_bundles(self.group))
        self.assertIn("STANDARD", served_version_bundles(self.group))
