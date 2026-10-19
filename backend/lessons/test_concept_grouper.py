from django.test import TestCase

from lessons.models import (
    CourseGroup, LearningMaterial, LearningObject, LearningObjectGroup, OutlineNode,
)
from lessons.services.concept_grouper import group_material_learning_objects


class ConceptGrouperTests(TestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Science")
        self.topic = OutlineNode.objects.create(course=self.course, title="Matter")

    def material(self, title):
        return LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title=title,
        )

    def obj(self, material, heading, content, embedding, grouped=True):
        group = (
            LearningObjectGroup.objects.create(outline_node=self.topic, label=heading)
            if grouped else None
        )
        return LearningObject.objects.create(
            material=material, group=group, title=heading,
            section_title=heading, content=content, embedding=embedding,
        )

    def test_specific_matching_heading_still_requires_minimum_similarity(self):
        first, second = self.material("First"), self.material("Second")
        a = self.obj(first, "Comparing the Three States", "short", [1.0, 0.0])
        b = self.obj(
            second, "Comparing the Three States", "long table",
            [0.59, 0.807403], grouped=False,
        )

        group_material_learning_objects(second)
        b.refresh_from_db()

        self.assertNotEqual(b.group_id, a.group_id)
        self.assertEqual(b.grouping_content_hash, b.current_grouping_fingerprint)
        self.assertTrue(b.grouping_content_hash)

    def test_generic_matching_heading_still_requires_strong_similarity(self):
        first, second = self.material("First"), self.material("Second")
        a = self.obj(first, "Everyday Examples", "solid examples", [1.0, 0.0])
        b = self.obj(
            second, "Everyday Examples", "liquid examples",
            [0.8, 0.6], grouped=False,
        )

        group_material_learning_objects(second)
        b.refresh_from_db()

        self.assertNotEqual(b.group_id, a.group_id)
