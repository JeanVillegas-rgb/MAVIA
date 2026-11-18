from unittest.mock import patch

from django.test import TestCase

from lessons.models import CourseGroup, LearningMaterial, LearningObject, OutlineNode
from lessons.services.embedding_service import SBERT_MODEL
from lessons.services.topic_matcher import (
    suggest_main_topic,
    suggest_main_topic_for_drafts,
)


class PipelineAdapterTests(TestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Science")
        parent = OutlineNode.objects.create(course=self.course, title="Matter")
        self.solid = OutlineNode.objects.create(
            course=self.course, parent=parent, title="Solids", depth=1, order=0,
            embedding=[1.0, 0.0], embedding_model=SBERT_MODEL,
        )
        OutlineNode.objects.create(
            course=self.course, parent=parent, title="Liquids", depth=1, order=1,
            embedding=[0.0, 1.0], embedding_model=SBERT_MODEL,
        )
        self.material = LearningMaterial.objects.create(course=self.course, title="Lesson")

    def test_object_average_match_returns_existing_outline_node(self):
        LearningObject.objects.create(
            material=self.material,
            kind=LearningObject.Kind.TEXT,
            title="Solid",
            section_title="Solid",
            content="A solid keeps its shape.",
            embedding=[1.0, 0.0],
        )
        result = suggest_main_topic(self.material)
        self.assertEqual(result["suggested_topic"]["outline_node_id"], self.solid.id)

    def test_draft_average_match_uses_same_rule_before_rows_are_saved(self):
        with patch(
            "lessons.services.embedding_service.embed_texts",
            return_value=[[1.0, 0.0]],
        ):
            result = suggest_main_topic_for_drafts(
                self.course,
                [{
                    "draft_id": "object_1",
                    "title": "Solid",
                    "section_title": "Solid",
                    "content": "A solid keeps its shape.",
                }],
            )

        self.assertEqual(result["suggested_topic"]["outline_node_id"], self.solid.id)
