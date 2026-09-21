"""A figure is grouped automatically only when a label corroborates it.

Two AI-written figure descriptions share stock phrasing, so the cross-encoder
scores them high against each other whatever they depict. Measured live on
topic 152: one PDF's particle figure was auto-grouped with two unrelated
figures of the other PDF, at 0.649 and 0.627. Wording alone may therefore
raise a card for the teacher, never place a figure on its own.
"""

import os
from unittest.mock import patch

from django.test import TestCase

from .models import (
    CourseGroup,
    LearningMaterial,
    LearningObject,
    LearningObjectGroup,
    LearningObjectMatchSuggestion,
    OutlineNode,
)
from .services import semantic_grouping as semantic
from .services.learning_resource_linker import refresh_learning_object_match_suggestions
from .test_label_corroboration import LABEL_ENV
from .test_semantic_grouping import FakeRuntime


STOCK = (
    "This figure shows the arrangement of particles and helps the student "
    "understand how the three states of matter differ from one another."
)
OTHER_STOCK = (
    "This figure shows how the particles are arranged and helps the student "
    "understand the differences between the three states of matter."
)


class ImageAutoGroupingTests(TestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Science")
        self.topic = OutlineNode.objects.create(course=self.course, title="States of matter")
        self.source_material = self.material("Lesson one")
        self.other_material = self.material("Lesson two")
        self.source_group = LearningObjectGroup.objects.create(outline_node=self.topic)
        self.other_group = LearningObjectGroup.objects.create(outline_node=self.topic)

    def material(self, title):
        return LearningMaterial.objects.create(
            course=self.course,
            outline_node=self.topic,
            title=title,
            generated_json={"learning_objects_confirmed": True},
        )

    def figure(self, material, group, title, content):
        return LearningObject.objects.create(
            material=material,
            group=group,
            title=title,
            content=content,
            kind=LearningObject.Kind.IMAGE,
        )

    def decision(self, source, scores):
        with patch.object(semantic, "runtime", return_value=FakeRuntime(scores)):
            return semantic.semantic_decision(
                source.material,
                source.title,
                source.content,
                source.kind,
                source.order,
                source.section_title,
                source.id,
            )

    @patch.dict(os.environ, LABEL_ENV)
    def test_unrelated_figures_scoring_above_auto_raise_a_card_not_a_group(self):
        source = self.figure(
            self.source_material, self.source_group, "Particle arrangement", STOCK,
        )
        other = self.figure(
            self.other_material, self.other_group,
            "Changing From One State to Another", OTHER_STOCK,
        )
        scores = {other.content: .95}

        decision = self.decision(source, scores)

        self.assertEqual(decision["confidence"], "medium")
        self.assertFalse(decision["evidence"]["auto_eligible"])
        self.assertTrue(decision["evidence"]["image_needs_label_corroboration"])

        with patch.object(semantic, "runtime", return_value=FakeRuntime(scores)):
            refresh_learning_object_match_suggestions(self.source_material)

        source.refresh_from_db()
        self.assertEqual(source.group_id, self.source_group.id)
        self.assertTrue(
            LearningObjectMatchSuggestion.objects.filter(status="pending").exists()
        )

    @patch.dict(os.environ, LABEL_ENV)
    def test_a_corroborating_title_still_groups_the_figures_automatically(self):
        source = self.figure(
            self.source_material, self.source_group, "Particle arrangement", STOCK,
        )
        other = self.figure(
            self.other_material, self.other_group, "Particle arrangements", OTHER_STOCK,
        )
        scores = {other.content: .95}

        decision = self.decision(source, scores)

        self.assertEqual(decision["confidence"], "high")
        self.assertTrue(decision["evidence"]["label_corroborated"])

        with patch.object(semantic, "runtime", return_value=FakeRuntime(scores)):
            refresh_learning_object_match_suggestions(self.source_material)

        source.refresh_from_db()
        self.assertEqual(source.group_id, self.other_group.id)
        self.assertFalse(
            LearningObjectMatchSuggestion.objects.filter(status="pending").exists()
        )

    @patch.dict(os.environ, LABEL_ENV)
    def test_text_objects_are_unaffected(self):
        source = LearningObject.objects.create(
            material=self.source_material, group=self.source_group,
            title="Solid", content="A solid keeps a fixed shape and a fixed volume.",
        )
        other = LearningObject.objects.create(
            material=self.other_material, group=self.other_group,
            title="Changing state", content="Heating a solid melts it into a liquid.",
        )

        decision = self.decision(source, {other.content: .95})

        self.assertEqual(decision["confidence"], "high")
        self.assertNotIn("image_needs_label_corroboration", decision["evidence"])
