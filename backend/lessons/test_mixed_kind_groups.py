"""A concept holding a section and its figure must still accept new material.

``unit_matching`` deliberately places a section, its diagram and its examples
into one concept, so a concept legitimately holds more than one kind. Matching
eligibility nevertheless demanded that every member share the incoming object's
kind, which such a concept can satisfy for no kind at all: text is refused for
the figure inside it and a figure for the text. It was sealed against every
later PDF, and an exact-label twin could not reach it either.
"""

import os
from unittest.mock import patch

from django.test import TestCase

from .models import (
    CourseGroup,
    LearningMaterial,
    LearningObject,
    LearningObjectGroup,
    OutlineNode,
)
from .services import semantic_grouping as semantic
from .test_semantic_grouping import FakeRuntime

LABEL_ENV = {
    "SEMANTIC_GROUPING_MODE": "auto",
    "SEMANTIC_GROUPING_LABEL_CORROBORATION": "True",
    "SEMANTIC_GROUPING_CALIBRATION": "",
    "SEMANTIC_GROUPING_AUTO_THRESHOLD": "",
    "SEMANTIC_GROUPING_REVIEW_THRESHOLD": "",
}


class MixedKindGroupTests(TestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Science")
        self.topic = OutlineNode.objects.create(course=self.course, title="States of matter")
        self.source_material = self.material("Fourth lesson")
        self.twin_material = self.material("Earlier lesson")

        # The destination already teaches this concept as a section plus its
        # figure, so it holds both kinds.
        self.twin_group = LearningObjectGroup.objects.create(outline_node=self.topic)
        self.twin = self.object(
            self.twin_material, self.twin_group, "Comparing the Three States",
            "the figure contrasts the particle spacing of solids, liquids and gases",
            kind=LearningObject.Kind.IMAGE,
        )
        self.companion = self.object(
            self.twin_material, self.twin_group, "Shape",
            "a solid keeps its shape while a liquid takes the shape of its container",
        )

        self.source_group = LearningObjectGroup.objects.create(outline_node=self.topic)
        self.source = self.object(
            self.source_material, self.source_group, "Comparing the Three States",
            "the diagram compares how particles are arranged in each of the three states",
            kind=LearningObject.Kind.IMAGE,
        )

    def material(self, title):
        return LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title=title,
            generated_json={"learning_objects_confirmed": True},
        )

    def object(self, material, group, title, content, kind=LearningObject.Kind.TEXT):
        return LearningObject.objects.create(
            material=material, group=group, title=title, content=content, kind=kind,
        )

    def decision(self, scores):
        with patch.object(semantic, "runtime", return_value=FakeRuntime(scores)):
            return semantic.semantic_decision(
                self.source_material, self.source.title, self.source.content,
                self.source.kind, self.source.order, self.source.section_title,
                self.source.id,
            )

    @patch.dict(os.environ, LABEL_ENV)
    def test_exact_label_twin_reaches_a_concept_that_holds_both_kinds(self):
        decision = self.decision({self.twin.content: .62})

        self.assertIsNotNone(decision, "a mixed-kind concept was unreachable")
        self.assertEqual(decision["candidate"].id, self.twin.id)
        self.assertTrue(decision["evidence"]["label_corroborated"])

    @patch.dict(os.environ, LABEL_ENV)
    def test_the_score_ignores_members_of_the_other_kind(self):
        """A figure is judged against figures, not against the prose beside it.

        Scoring takes the weakest member, so one off-kind member otherwise sets
        the whole concept's score and sinks a match that genuinely belongs.
        """
        decision = self.decision({self.twin.content: .62, self.companion.content: .05})

        self.assertIsNotNone(decision)
        self.assertAlmostEqual(decision["evidence"]["score"], .62, places=3)

    @patch.dict(os.environ, LABEL_ENV)
    def test_a_different_label_still_cannot_enter_a_mixed_concept(self):
        """Only the corroborated path reaches in; ordinary scoring may not.

        Relaxing this without a label to vouch for it would let two unrelated
        AI-written figure descriptions group on their shared stock phrasing.
        """
        self.source.title = "Everyday Examples"
        self.source.save(update_fields=["title"])

        self.assertIsNone(self.decision({self.twin.content: .95}))
