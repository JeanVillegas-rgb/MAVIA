"""Classifier review regressions; all model calls use fakes, DB is a test DB."""

from copy import deepcopy
from unittest.mock import Mock, patch

import numpy as np
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from lessons.models import CourseGroup, ExtractedQuestion, LearningMaterial, LearningObject, OutlineNode
from lessons.services.block_classifier import classify_blocks, LessonClassificationPending


class DecisionMarginTests(SimpleTestCase):
    def classify(self, scores, threshold=None):
        model = Mock()
        model.classes_ = np.array(["other", "assessment", "lesson_content"])
        model.predict.return_value = ["other"]
        model.decision_function.return_value = scores
        encoder = Mock()
        encoder.encode.return_value = np.ones((1, 2))
        with override_settings(BLOCK_CLASSIFIER_REVIEW_MARGIN=threshold), patch(
            "lessons.services.block_classifier._load_classifier",
            return_value=({"model": model}, encoder),
        ):
            return classify_blocks([{"block_id": 0, "text": "Source text"}])[0]

    def test_scores_follow_model_class_order_and_prediction_is_unchanged(self):
        result = self.classify([[0.8, -0.2, 0.75]], 0.1)
        self.assertEqual(result["category"], "other")
        self.assertEqual(result["predicted_category"], "other")
        self.assertEqual(result["decision_scores"]["assessment"], -0.2)
        self.assertAlmostEqual(result["decision_margin"], 0.05)
        self.assertTrue(result["review_required"])
        self.assertFalse(result["include_in_narration"])

    def test_disabled_cutoff_still_records_scores(self):
        result = self.classify([[0.8, -0.2, 0.75]])
        self.assertFalse(result["review_required"])
        self.assertIsNone(result["review_margin_threshold"])

    def test_cutoff_is_strict_and_ties_can_require_review(self):
        self.assertFalse(self.classify([[1.0, 0.5, -1.0]], 0.5)["review_required"])
        self.assertTrue(self.classify([[1.0, 1.0, -1.0]], 0.1)["review_required"])

    def test_margins_are_not_percentages_or_clamped(self):
        self.assertEqual(self.classify([[4.0, 1.0, -5.0]])["decision_margin"], 3.0)

    def test_bad_cutoffs_fail_instead_of_silently_disabling_review(self):
        for cutoff in (-1, float("nan"), float("inf")):
            with self.subTest(cutoff=cutoff), self.assertRaises(ValueError):
                self.classify([[1.0, 0.0, -1.0]], cutoff)

    def test_invalid_scores_are_rejected(self):
        for scores in ([[1, 2]], [[1, float("nan"), 0]]):
            with self.subTest(scores=scores), self.assertRaises(LessonClassificationPending):
                self.classify(scores)

    @patch("lessons.services.block_classifier._load_classifier")
    def test_empty_input_does_not_load_the_model(self, load):
        self.assertEqual(classify_blocks([]), [])
        load.assert_not_called()


class BlockReviewApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.teacher = get_user_model().objects.create_user(username="blockteacher", role="TEACHER")
        self.client.force_authenticate(self.teacher)
        self.course = CourseGroup.objects.create(title="Block review science")
        self.topic = OutlineNode.objects.create(course=self.course, title="Separating Mixtures")
        # Minimal reproduction of material 13: a good heading and the first
        # Picking line were excluded, while its page-2 continuation survived.
        self.blocks = [
            {"block_id": 0, "page": 1, "docling_label": "section_header", "text": "Why Mixtures Can Be Separated", "category": "lesson_content"},
            {"block_id": 10, "page": 1, "docling_label": "section_header", "text": "The Separation Techniques", "category": "other"},
            {"block_id": 11, "page": 1, "docling_label": "list_item", "list_marker": "1.", "text": "Picking is the simplest method.", "category": "other", "review_required": True},
            {"block_id": 12, "page": 2, "docling_label": "text", "text": "handled one at a time.", "category": "lesson_content"},
        ]
        self.material = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="Mixtures", status="completed",
            extracted_blocks=deepcopy(self.blocks),
            generated_json={"classified_blocks": deepcopy(self.blocks), "learning_objects_confirmed": False},
        )
        self.obj = LearningObject.objects.create(
            material=self.material, title="Teacher's title", section_title="Teacher's section",
            content="Teacher's edited continuation.", source_block_id=12, source_page=2, order=0,
        )
        self.url = f"/api/courses/{self.course.pk}/materials/{self.material.pk}/review-block/"
        self.embed = patch("lessons.services.block_review.embed_learning_objects",
                           side_effect=lambda drafts: {d["draft_id"]: [1.0, 0.0] for d in drafts}).start()
        self.addCleanup(patch.stopall)

    def review(self, block_id=11, category="lesson_content", url=None):
        return self.client.post(url or self.url, {"block_id": block_id, "category": category}, format="json")

    def test_restore_heading_then_picking_without_rebuilding_existing_work(self):
        original_id, original_uuid = self.obj.pk, self.obj.metadata_id
        self.assertEqual(self.review(10).status_code, 200)
        response = self.review()
        self.assertEqual(response.status_code, 200, response.data)
        restored = self.material.learning_objects.get(source_block_id=11)
        self.assertEqual(restored.title, "The Separation Techniques")
        self.assertEqual(restored.content, "1. Picking is the simplest method.")
        self.assertEqual(restored.source_page, 1)
        self.assertEqual(list(self.material.learning_objects.values_list("source_block_id", flat=True)), [11, 12])
        self.obj.refresh_from_db()
        self.assertEqual((self.obj.pk, self.obj.metadata_id), (original_id, original_uuid))
        self.assertEqual(self.obj.content, "Teacher's edited continuation.")
        self.assertEqual(self.obj.title, "Teacher's title")
        self.material.refresh_from_db()
        block = self.material.extracted_blocks[2]
        self.assertEqual(block["predicted_category"], "other")
        self.assertEqual(block["teacher_category"], "lesson_content")
        self.assertEqual(block["reviewed_by"], self.teacher.pk)
        self.assertTrue(block["teacher_reviewed"])
        self.assertEqual(self.material.generated_json["block_review_history"][-1]["from_category"], "other")

    def test_repeat_review_does_not_duplicate_restored_objects(self):
        self.assertEqual(self.review().status_code, 200)
        self.assertEqual(self.review().status_code, 200)
        self.assertEqual(self.material.learning_objects.filter(source_block_id=11).count(), 1)

    def test_accept_original_prediction_resolves_flag_without_restoring_text(self):
        self.assertEqual(self.review(category="other").status_code, 200)
        self.assertEqual(self.material.learning_objects.count(), 1)
        self.material.refresh_from_db()
        self.assertTrue(self.material.extracted_blocks[2]["teacher_reviewed"])
        self.embed.assert_not_called()

    def test_changing_existing_object_category_is_refused_without_deletion(self):
        response = self.review(12, "other")
        self.assertEqual(response.status_code, 409)
        self.obj.refresh_from_db()
        self.assertEqual(self.obj.content, "Teacher's edited continuation.")
        self.material.refresh_from_db()
        self.assertNotIn("block_review_history", self.material.generated_json)

    def test_merged_visual_source_is_also_protected(self):
        self.material.draft_learning_objects = [{"draft_id": "image_1", "source_block_ids": [11, 12]}]
        self.material.save(update_fields=["draft_learning_objects"])
        self.obj.draft_id = "image_1"
        self.obj.save(update_fields=["draft_id"])
        self.assertEqual(self.review().status_code, 409)

    def test_existing_questions_and_bank_rows_are_not_deleted_or_overwritten(self):
        question = ExtractedQuestion.objects.create(material=self.material, prompt="Teacher reviewed?", source_block_id=11)
        self.assertEqual(self.review().status_code, 409)
        question.refresh_from_db()
        self.assertEqual(question.prompt, "Teacher reviewed?")

    def test_new_assessment_uses_existing_parser_without_replacing_other_questions(self):
        self.material.generated_json["classified_blocks"][2]["text"] = "What is picking?"
        self.material.save(update_fields=["generated_json"])
        original = ExtractedQuestion.objects.create(material=self.material, prompt="Existing teacher prompt?", source_block_id=90)
        response = self.review(category="assessment")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.material.questions.count(), 2)
        original.refresh_from_db()
        created = self.material.questions.get(source_block_id=11)
        self.assertEqual(created.prompt, "What is picking?")
        self.assertEqual(created.question_bank_rows.count(), 1)

    def test_confirmed_or_finalized_material_cannot_be_changed(self):
        self.material.generated_json["learning_objects_confirmed"] = True
        self.material.save(update_fields=["generated_json"])
        self.assertEqual(self.review().status_code, 409)
        self.material.generated_json["learning_objects_confirmed"] = False
        self.material.save(update_fields=["generated_json"])
        self.topic.related_info = {"source_review_finalized_at": "2026-11-19"}
        self.topic.save(update_fields=["related_info"])
        self.assertEqual(self.review().status_code, 409)

    def test_unknown_block_bad_category_and_wrong_course_are_rejected(self):
        self.assertEqual(self.review(999).status_code, 400)
        self.assertEqual(self.review(category="needs_review").status_code, 400)
        other = CourseGroup.objects.create(title="Other course")
        self.assertEqual(self.review(url=f"/api/courses/{other.pk}/materials/{self.material.pk}/review-block/").status_code, 404)

    def test_student_and_anonymous_cannot_review(self):
        self.client.force_authenticate(None)
        self.assertIn(self.review().status_code, (401, 403))
        student = get_user_model().objects.create_user(username="blockstudent", role="STUDENT")
        self.client.force_authenticate(student)
        self.assertEqual(self.review().status_code, 403)

    def test_processing_material_cannot_be_changed(self):
        self.material.status = "processing"
        self.material.save(update_fields=["status"])
        self.assertEqual(self.review().status_code, 409)

    def test_flagged_question_only_material_also_prevents_finalizing_sources(self):
        self.material.generated_json["learning_objects_confirmed"] = True
        self.material.generated_json["classified_blocks"][2]["review_required"] = False
        self.material.save(update_fields=["generated_json"])
        LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="Questions", status="completed",
            generated_json={"classified_blocks": [{"block_id": 0, "review_required": True}]},
        )
        url = f"/api/courses/{self.course.pk}/outline-nodes/{self.topic.pk}/finalize-sources/"
        self.assertEqual(self.client.post(url).status_code, 400)
        self.topic.refresh_from_db()
        self.assertNotIn("source_review_finalized_at", self.topic.related_info)

    def test_pending_reviews_block_confirmation_until_explicitly_accepted(self):
        url = f"/api/courses/{self.course.pk}/materials/{self.material.pk}/confirm-learning-objects/"
        self.assertEqual(self.client.post(url).status_code, 400)
        self.material.refresh_from_db()
        self.assertFalse(self.material.generated_json["learning_objects_confirmed"])
        self.assertEqual(self.review(category="other").status_code, 200)
        self.assertEqual(self.client.post(url).status_code, 200)

    def test_embedding_failure_rolls_back_classification_and_object_changes(self):
        self.embed.side_effect = RuntimeError("encoder unavailable")
        with self.assertRaises(RuntimeError):
            self.review()
        self.material.refresh_from_db()
        self.assertEqual(self.material.learning_objects.count(), 1)
        self.assertEqual(self.material.extracted_blocks[2]["category"], "other")
