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
from .test_semantic_grouping import FakeRuntime


LABEL_ENV = {
    "SEMANTIC_GROUPING_MODE": "auto",
    "SEMANTIC_GROUPING_LABEL_CORROBORATION": "True",
    "SEMANTIC_GROUPING_CALIBRATION": "",
    "SEMANTIC_GROUPING_AUTO_THRESHOLD": "",
    "SEMANTIC_GROUPING_REVIEW_THRESHOLD": "",
}


class LabelCorroborationTests(TestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Science")
        self.topic = OutlineNode.objects.create(course=self.course, title="States of matter")
        self.source_material = self.material("Paragraph lesson")
        self.twin_material = self.material("Glossary lesson")
        self.source_group = LearningObjectGroup.objects.create(outline_node=self.topic)
        self.twin_group = LearningObjectGroup.objects.create(outline_node=self.topic)
        self.source = self.object(
            self.source_material,
            self.source_group,
            "Solid",
            "A solid has definite shape and volume and tightly packed particles.",
        )
        self.twin = self.object(
            self.twin_material,
            self.twin_group,
            "solid",
            "matter that keeps its shape and size",
        )

    def material(self, title):
        return LearningMaterial.objects.create(
            course=self.course,
            outline_node=self.topic,
            title=title,
            generated_json={"learning_objects_confirmed": True},
        )

    def object(self, material, group, title, content, kind=LearningObject.Kind.TEXT):
        return LearningObject.objects.create(
            material=material,
            group=group,
            title=title,
            content=content,
            kind=kind,
        )

    def decision(self, scores):
        with patch.object(semantic, "runtime", return_value=FakeRuntime(scores)):
            return semantic.semantic_decision(
                self.source_material,
                self.source.title,
                self.source.content,
                self.source.kind,
                self.source.order,
                self.source.section_title,
                self.source.id,
            )

    @patch.dict(os.environ, LABEL_ENV)
    def test_matching_label_promotes_existing_content_evidence_without_pending_work(self):
        distractor_material = self.material("Third lesson")
        distractor_group = LearningObjectGroup.objects.create(outline_node=self.topic)
        self.object(distractor_material, distractor_group, "State of matter", "wrong preferred content")

        with patch.object(
            semantic,
            "runtime",
            return_value=FakeRuntime({self.twin.content: .40, "wrong preferred content": .50}),
        ):
            refresh_learning_object_match_suggestions(self.source_material)

        self.source.refresh_from_db()
        suggestion = LearningObjectMatchSuggestion.objects.get(status="accepted")
        self.assertEqual(self.source.group_id, self.twin_group.id)
        self.assertEqual(suggestion.candidate_learning_object_id, self.twin.id)
        self.assertEqual(suggestion.confidence, "high")
        self.assertTrue(suggestion.evidence["label_corroborated"])
        self.assertTrue(suggestion.evidence["title_used"])
        self.assertEqual(suggestion.evidence["normalized_label"], "solid")
        self.assertFalse(LearningObjectMatchSuggestion.objects.filter(status="pending").exists())

    @patch.dict(os.environ, LABEL_ENV)
    def test_matching_label_below_review_threshold_is_not_grouped(self):
        decision = self.decision({self.twin.content: .20})
        self.assertIsNone(decision["confidence"])
        with patch.object(semantic, "runtime", return_value=FakeRuntime({self.twin.content: .20})):
            refresh_learning_object_match_suggestions(self.source_material)
        self.source.refresh_from_db()
        self.assertEqual(self.source.group_id, self.source_group.id)
        self.assertFalse(LearningObjectMatchSuggestion.objects.exists())

    @patch.dict(os.environ, LABEL_ENV)
    def test_generic_label_does_not_use_label_path(self):
        self.source.title = self.twin.title = "Examples"
        self.source.save(update_fields=["title"])
        self.twin.save(update_fields=["title"])
        decision = self.decision({self.twin.content: .40})
        self.assertFalse(decision["evidence"].get("label_corroborated", False))

    @patch.dict(os.environ, LABEL_ENV)
    def test_structural_and_numbered_generic_labels_do_not_use_label_path(self):
        for label in ("Lesson 1", "Figure 1", "Table 2"):
            self.source.title = self.twin.title = label
            self.source.save(update_fields=["title"])
            self.twin.save(update_fields=["title"])
            decision = self.decision({self.twin.content: .40})
            self.assertFalse(decision["evidence"].get("label_corroborated", False), label)

    @patch.dict(os.environ, LABEL_ENV)
    def test_duplicate_label_in_one_material_disables_label_path(self):
        duplicate_group = LearningObjectGroup.objects.create(outline_node=self.topic)
        self.object(self.source_material, duplicate_group, "solid", "another solid passage")
        decision = self.decision({self.twin.content: .40})
        self.assertFalse(decision["evidence"].get("label_corroborated", False))

    @patch.dict(os.environ, LABEL_ENV)
    def test_duplicate_label_in_candidate_material_disables_label_path(self):
        duplicate_group = LearningObjectGroup.objects.create(outline_node=self.topic)
        duplicate = self.object(
            self.twin_material, duplicate_group, "Solid", "another solid passage"
        )
        decision = self.decision({self.twin.content: .40, duplicate.content: .45})
        self.assertFalse(decision["evidence"].get("label_corroborated", False))

    @patch.dict(os.environ, LABEL_ENV)
    def test_different_kind_does_not_use_label_path(self):
        self.twin.kind = LearningObject.Kind.IMAGE
        self.twin.save(update_fields=["kind"])
        self.assertIsNone(self.decision({self.twin.content: .40}))

    @patch.dict(os.environ, LABEL_ENV)
    def test_teacher_rejected_pair_stays_rejected(self):
        LearningObjectMatchSuggestion.objects.create(
            outline_node=self.topic,
            source_learning_object=self.source,
            candidate_learning_object=self.twin,
            confidence="teacher_confirmed",
            status="rejected",
            evidence={"teacher_reviewed": True},
        )
        self.assertIsNone(self.decision({self.twin.content: .40}))
        self.source.refresh_from_db()
        self.assertEqual(self.source.group_id, self.source_group.id)

    @patch.dict(os.environ, LABEL_ENV)
    def test_source_in_multi_member_group_is_not_moved(self):
        companion_material = self.material("Companion")
        self.object(companion_material, self.source_group, "Companion", "companion passage")
        self.assertIsNone(self.decision({self.twin.content: .40}))
        self.source.refresh_from_db()
        self.assertEqual(self.source.group_id, self.source_group.id)

    @patch.dict(os.environ, LABEL_ENV)
    def test_represented_candidate_disables_label_path(self):
        self.twin.represented_by = self.source
        self.twin.save(update_fields=["represented_by"])
        decision = self.decision({self.twin.content: .40})
        self.assertFalse(
            decision and decision["evidence"].get("label_corroborated", False)
        )

    @patch.dict(os.environ, LABEL_ENV)
    def test_weak_member_in_destination_group_blocks_label_path(self):
        third_material = self.material("Third lesson")
        weak = self.object(third_material, self.twin_group, "Solid", "different teaching step")
        decision = self.decision({self.twin.content: .40, weak.content: .20})
        self.assertIsNone(decision["confidence"])

    @patch.dict(
        os.environ,
        {**LABEL_ENV, "SEMANTIC_GROUPING_LABEL_CORROBORATION": "False"},
    )
    def test_disabled_flag_preserves_content_only_result(self):
        distractor_material = self.material("Third lesson")
        distractor_group = LearningObjectGroup.objects.create(outline_node=self.topic)
        distractor = self.object(
            distractor_material, distractor_group, "State of matter", "wrong preferred content"
        )
        decision = self.decision({self.twin.content: .40, distractor.content: .50})
        self.assertEqual(decision["candidate"].id, distractor.id)
        self.assertEqual(decision["confidence"], "medium")
        self.assertFalse(decision["evidence"].get("label_corroborated", False))

    @patch.dict(
        os.environ,
        {**LABEL_ENV, "SEMANTIC_GROUPING_MODE": "review"},
    )
    def test_review_mode_preserves_no_automatic_grouping_contract(self):
        decision = self.decision({self.twin.content: .40})
        self.assertEqual(decision["confidence"], "medium")
        self.assertFalse(decision["evidence"].get("label_corroborated", False))

    @patch.dict(os.environ, LABEL_ENV)
    def test_part_and_number_normalization_match(self):
        self.source.title = "1. Solid (Part 1 of 1)"
        self.source.save(update_fields=["title"])
        decision = self.decision({self.twin.content: .40})
        self.assertEqual(decision["candidate"].id, self.twin.id)
        self.assertEqual(decision["confidence"], "high")
        self.assertTrue(decision["evidence"]["label_corroborated"])

    @patch.dict(os.environ, LABEL_ENV)
    def test_content_only_high_decision_keeps_precedence(self):
        distractor_material = self.material("Third lesson")
        distractor_group = LearningObjectGroup.objects.create(outline_node=self.topic)
        distractor = self.object(
            distractor_material, distractor_group, "State of matter", "strong content twin"
        )
        decision = self.decision({self.twin.content: .40, distractor.content: .95})
        self.assertEqual(decision["candidate"].id, distractor.id)
        self.assertEqual(decision["confidence"], "high")
        self.assertFalse(decision["evidence"].get("label_corroborated", False))

    @patch.dict(os.environ, LABEL_ENV)
    def test_multiple_destination_groups_with_same_label_are_ambiguous(self):
        third_material = self.material("Third lesson")
        other_group = LearningObjectGroup.objects.create(outline_node=self.topic)
        other_twin = self.object(third_material, other_group, "Solid", "another solid definition")
        decision = self.decision({self.twin.content: .40, other_twin.content: .45})
        self.assertFalse(decision["evidence"].get("label_corroborated", False))
