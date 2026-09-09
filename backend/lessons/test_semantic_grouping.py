import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, SimpleTestCase
from django.core.management import call_command
from django.core.management.base import CommandError

from .models import CourseGroup, OutlineNode, LearningMaterial, LearningObject, LearningObjectGroup, LearningObjectMatchSuggestion
from .services import semantic_grouping as semantic
from .services.grouping_evaluation import read_labeled_pairs, calibrate, metrics
from .services.learning_resource_linker import _match_decision, refresh_learning_object_match_suggestions


class FakeRuntime:
    def __init__(self, scores=None):
        self.scores = scores or {}
        self.inputs = []

    def supports(self, text):
        return bool(text.strip()) and text != "TOO LONG"

    def supports_pair(self, left, right):
        return self.supports(left) and self.supports(right)

    def embeddings(self, texts):
        self.inputs.extend(texts)
        return [[1.0, 0.0] for text in texts]

    def pair_scores(self, pairs):
        self.inputs.extend(text for pair in pairs for text in pair)
        return [self.scores.get(right, .95) for left, right in pairs]


class SemanticPureTests(SimpleTestCase):
    def test_semantic_mode_is_enabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(semantic.mode(), "auto")

    def test_every_member_checked_and_titles_never_sent(self):
        a = SimpleNamespace(id=1, group_id=4, content="definition", title="TITLE ONLY")
        b = SimpleNamespace(id=2, group_id=4, content="examples", title="TITLE ONLY")
        engine = FakeRuntime({"definition": .98, "examples": .2})
        ranked = semantic.rank_groups("base definition", [a], {4: [a, b]}, runtime_instance=engine,
                                      thresholds={"top_k": 10})
        self.assertEqual(ranked[0]["evidence"]["score"], .2)
        self.assertEqual(ranked[0]["evidence"]["members_checked"], 2)
        self.assertNotIn("TITLE ONLY", engine.inputs)

    def test_long_member_blocks_complete_group_check(self):
        a = SimpleNamespace(id=1, group_id=4, content="definition")
        b = SimpleNamespace(id=2, group_id=4, content="TOO LONG")
        ranked = semantic.rank_groups("base", [a], {4: [a, b]}, runtime_instance=FakeRuntime(), thresholds={"top_k": 10})
        self.assertFalse(ranked[0]["evidence"]["all_members_checked"])

    def test_empty_and_long_source_not_scored(self):
        a = SimpleNamespace(id=1, group_id=4, content="definition")
        for text in ("", "TOO LONG"):
            engine = FakeRuntime()
            self.assertEqual(semantic.rank_groups(text, [a], {4: [a]}, runtime_instance=engine), [])
            self.assertEqual(engine.inputs, [])

    @patch.dict(os.environ, {
        "SEMANTIC_GROUPING_CALIBRATION": "",
        "SEMANTIC_GROUPING_AUTO_THRESHOLD": "",
        "SEMANTIC_GROUPING_REVIEW_THRESHOLD": "",
        "SEMANTIC_GROUPING_MINIMUM_SBERT_COSINE": "",
        "SEMANTIC_GROUPING_MINIMUM_MARGIN": "",
    })
    def test_configured_default_thresholds(self):
        config = semantic.policy()
        self.assertEqual(config["auto_threshold"], .60)
        self.assertEqual(config["review_threshold"], .30)
        self.assertEqual(config["minimum_sbert_cosine"], .60)
        self.assertEqual(config["minimum_margin"], .05)
        self.assertEqual(config["auto_threshold_source"], "configured_default")

    @patch.dict(os.environ, {"SEMANTIC_GROUPING_CALIBRATION": "", "SEMANTIC_GROUPING_AUTO_THRESHOLD": ".70"})
    def test_explicit_auto_threshold_is_marked_unvalidated(self):
        config = semantic.policy()
        self.assertEqual(config["auto_threshold"], .70)
        self.assertEqual(config["auto_threshold_source"], "environment_unvalidated")

    def test_calibration_requires_validation_and_exact_model(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            data = {"model_fingerprint": semantic.FINGERPRINT,
                    "thresholds": {"auto_threshold": .9, "review_threshold": .5, "minimum_margin": .05}}
            path.write_text(json.dumps(data), encoding="utf-8")
            with patch.dict(os.environ, {
                "SEMANTIC_GROUPING_CALIBRATION": str(path),
                "SEMANTIC_GROUPING_AUTO_THRESHOLD": "",
            }):
                self.assertEqual(semantic.policy()["auto_threshold"], .60)
                data.update(approved_for_auto=True, dataset_sha256="test-only", validation={"auto_predictions": 40, "false_auto": 0, "negative_pairs": 20})
                path.write_text(json.dumps(data), encoding="utf-8")
                self.assertEqual(semantic.policy()["auto_threshold"], .9)
                data["model_fingerprint"] = "different model"
                path.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(semantic.SemanticUnavailable):
                    semantic.policy()

    def test_cache_survives_reopen_and_content_changes_get_new_keys(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "cache.sqlite3"
            key = semantic.content_hash("A solid is firm.")
            semantic.ScoreCache(path).put(key, [1.0, 0.0])
            self.assertEqual(semantic.ScoreCache(path).get(key), [1.0, 0.0])
            self.assertIsNone(semantic.ScoreCache(path).get(semantic.content_hash("A solid is not firm.")))

    def test_thresholds_use_development_only(self):
        development = [{"split": "development", "label": "equivalent", "semantic_score": .9} for _ in range(10)]
        development += [{"split": "development", "label": "related", "semantic_score": .8}]
        self.assertEqual(calibrate(development)["auto_threshold"], .9)
        self.assertEqual(calibrate(development + [{"split": "test", "label": "unrelated", "semantic_score": .99}]), calibrate(development))
        self.assertEqual(metrics([{"label": "unrelated", "semantic_score": .99}], .9, .5)["false_auto"], 1)

    def test_unlabeled_data_and_cross_split_leakage_rejected(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "pairs.jsonl"
            row = {"left": {"content": "shared"}, "right": {"content": "other"}, "label": "", "split": "", "reviewed_by": ""}
            path.write_text(json.dumps(row), encoding="utf-8")
            with self.assertRaises(ValueError):
                read_labeled_pairs(path)
            row.update(label="equivalent", split="development", reviewed_by="teacher")
            second = {**row, "split": "test", "right": {"content": "different"}}
            path.write_text(json.dumps(row) + "\n" + json.dumps(second), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "leakage"):
                read_labeled_pairs(path)


class SemanticIntegrationTests(TestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Science")
        self.topic = OutlineNode.objects.create(course=self.course, title="Matter")
        self.material = LearningMaterial.objects.create(course=self.course, outline_node=self.topic, title="First", generated_json={"learning_objects_confirmed": True})
        self.other = LearningMaterial.objects.create(course=self.course, outline_node=self.topic, title="Second", generated_json={"learning_objects_confirmed": True})
        self.source_group = LearningObjectGroup.objects.create(outline_node=self.topic)
        self.group = LearningObjectGroup.objects.create(outline_node=self.topic)
        self.source = LearningObject.objects.create(material=self.material, group=self.source_group, title="Solid", content="base definition")
        self.candidate = LearningObject.objects.create(material=self.other, group=self.group, title="Solid", content="definition")

    def decision(self):
        return _match_decision(self.material, self.source.title, self.source.content, "text", 0, source_object_id=self.source.id)

    @patch.dict(os.environ, {"SEMANTIC_GROUPING_MODE": "review", "SEMANTIC_GROUPING_CALIBRATION": ""})
    def test_review_mode_never_auto_groups(self):
        with patch.object(semantic, "runtime", return_value=FakeRuntime()):
            refresh_learning_object_match_suggestions(self.material)
        self.source.refresh_from_db()
        self.assertEqual(self.source.group_id, self.source_group.pk)
        suggestion = LearningObjectMatchSuggestion.objects.get()
        self.assertEqual(suggestion.confidence, "medium")

    @patch.dict(os.environ, {"SEMANTIC_GROUPING_MODE": "review", "SEMANTIC_GROUPING_CALIBRATION": ""})
    def test_low_score_is_ignored(self):
        with patch.object(semantic, "runtime", return_value=FakeRuntime({"definition": .1})):
            self.assertIsNone(self.decision()["confidence"])
            refresh_learning_object_match_suggestions(self.material)
        self.assertFalse(LearningObjectMatchSuggestion.objects.exists())

    @patch.dict(os.environ, {"SEMANTIC_GROUPING_MODE": "auto"})
    def test_high_score_moves_to_group_only_with_auto_policy(self):
        config = {"review_threshold": .5, "auto_threshold": .9, "minimum_sbert_cosine": .8,
                  "minimum_margin": .05, "top_k": 10, "auto_threshold_source": "test"}
        with patch.object(semantic, "runtime", return_value=FakeRuntime()), patch.object(semantic, "policy", return_value=config):
            refresh_learning_object_match_suggestions(self.material)
        self.source.refresh_from_db()
        self.assertEqual(self.source.group_id, self.group.pk)
        self.assertEqual(LearningObjectMatchSuggestion.objects.get().confidence, "high")

    @patch.dict(os.environ, {"SEMANTIC_GROUPING_MODE": "auto", "SEMANTIC_GROUPING_CALIBRATION": "", "SEMANTIC_GROUPING_AUTO_THRESHOLD": ""})
    def test_auto_uses_configured_default_without_calibration(self):
        with patch.object(semantic, "runtime", return_value=FakeRuntime()):
            self.assertEqual(self.decision()["confidence"], "high")

    @patch.dict(os.environ, {"SEMANTIC_GROUPING_MODE": "review"})
    def test_missing_models_preserve_existing_queue(self):
        pending = LearningObjectMatchSuggestion.objects.create(outline_node=self.topic, source_learning_object=self.source,
                    candidate_learning_object=self.candidate, confidence="medium", status="pending")
        with patch.object(semantic, "runtime", side_effect=semantic.SemanticUnavailable("missing")):
            refresh_learning_object_match_suggestions(self.material)
            self.assertIsNone(self.decision())
        self.assertTrue(LearningObjectMatchSuggestion.objects.filter(pk=pending.pk).exists())

    @patch.dict(os.environ, {"SEMANTIC_GROUPING_MODE": "auto"})
    def test_calibrated_high_groups_and_rejected_member_blocks_it(self):
        config = {"review_threshold": .5, "auto_threshold": .9, "minimum_sbert_cosine": .8,
                  "minimum_margin": .05, "top_k": 10, "auto_threshold_source": "test"}
        with patch.object(semantic, "runtime", return_value=FakeRuntime()), patch.object(semantic, "policy", return_value=config):
            self.assertEqual(self.decision()["confidence"], "high")
            LearningObjectMatchSuggestion.objects.create(outline_node=self.topic, source_learning_object=self.source,
                candidate_learning_object=self.candidate, confidence="teacher_confirmed", status="rejected", evidence={"teacher_reviewed": True})
            self.assertIsNone(self.decision())

    @patch.dict(os.environ, {"SEMANTIC_GROUPING_MODE": "review", "SEMANTIC_GROUPING_CALIBRATION": ""})
    def test_inference_failure_preserves_pending_suggestion(self):
        pending = LearningObjectMatchSuggestion.objects.create(
            outline_node=self.topic, source_learning_object=self.source,
            candidate_learning_object=self.candidate, confidence="medium", status="pending")
        with patch.object(semantic, "runtime", return_value=FakeRuntime()), patch.object(
                semantic, "semantic_decision", side_effect=RuntimeError("inference failed")), self.assertLogs(
                "lessons.services.learning_resource_linker", level="ERROR"):
            refresh_learning_object_match_suggestions(self.material)
        self.assertTrue(LearningObjectMatchSuggestion.objects.filter(pk=pending.pk, status="pending").exists())

    @patch.dict(os.environ, {"SEMANTIC_GROUPING_MODE": "review"})
    def test_unconfirmed_and_same_material_candidates_excluded(self):
        self.other.generated_json = {}
        self.other.save()
        with patch.object(semantic, "runtime", return_value=FakeRuntime()):
            self.assertIsNone(self.decision())

    def test_export_is_unlabeled_read_only_and_refuses_overwrite(self):
        before = list(LearningObject.objects.values_list("id", "group_id"))
        with TemporaryDirectory() as directory:
            path = Path(directory) / "pairs.jsonl"
            call_command("export_grouping_pairs", output=str(path), limit=20)
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["label"], "")
            self.assertEqual(rows[0]["left"]["metadata_id"], str(self.source.metadata_id))
            with self.assertRaises(CommandError):
                call_command("export_grouping_pairs", output=str(path), limit=20)
        self.assertEqual(before, list(LearningObject.objects.values_list("id", "group_id")))

    @patch.dict(os.environ, {"SEMANTIC_GROUPING_MODE": "auto"})
    def test_refresh_cannot_take_member_from_teacher_confirmed_group(self):
        from .services.learning_resource_linker import record_teacher_match_decision
        companion_material = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic,
            generated_json={"learning_objects_confirmed": True})
        companion = LearningObject.objects.create(
            material=companion_material, group=self.source_group, content="companion")
        accepted = record_teacher_match_decision(self.source, companion, accepted=True)
        with patch.object(semantic, "runtime", return_value=FakeRuntime()), patch.object(
                semantic, "semantic_decision", wraps=semantic.semantic_decision) as matcher:
            refresh_learning_object_match_suggestions(self.material)
            matcher.assert_not_called()
            self.assertIsNone(self.decision())
        self.source.refresh_from_db()
        accepted.refresh_from_db()
        self.assertEqual(self.source.group_id, companion.group_id)
        self.assertEqual(accepted.status, "accepted")
        self.assertTrue(accepted.evidence["teacher_reviewed"])

    @patch.dict(os.environ, {"SEMANTIC_GROUPING_MODE": "auto", "SEMANTIC_GROUPING_AUTO_THRESHOLD": ".60",
                           "SEMANTIC_GROUPING_CALIBRATION": ""})
    def test_new_object_auto_connection_is_saved_before_group_is_protected(self):
        from .services.learning_resource_linker import ensure_learning_object_groups
        self.source.group = None
        self.source.save(update_fields=["group"])
        with patch.object(semantic, "runtime", return_value=FakeRuntime()):
            ensure_learning_object_groups(self.material)
        self.source.refresh_from_db()
        self.assertEqual(self.source.group_id, self.group.id)
        automatic = LearningObjectMatchSuggestion.objects.get()
        self.assertEqual(automatic.status, "accepted")
        self.assertEqual(automatic.confidence, "high")

    @patch.dict(os.environ, {"SEMANTIC_GROUPING_MODE": "auto"})
    def test_separating_automatic_connection_persists_rejection_and_is_fast(self):
        from .tests import authenticated_api_client
        self.candidate.group = self.source_group
        self.candidate.save(update_fields=["group"])
        with patch("lessons.views.refresh_material_learning_relationships") as recompute:
            response = authenticated_api_client().post(
                f"/api/courses/{self.course.id}/outline-nodes/{self.topic.id}/separate-learning-object/",
                {"learning_object_id": self.source.id}, format="json")
            self.assertEqual(response.status_code, 200)
            recompute.assert_not_called()
        rejection = LearningObjectMatchSuggestion.objects.get()
        self.assertEqual(rejection.status, "rejected")
        with patch.object(semantic, "runtime", return_value=FakeRuntime()):
            refresh_learning_object_match_suggestions(self.material)
        self.source.refresh_from_db()
        self.candidate.refresh_from_db()
        self.assertNotEqual(self.source.group_id, self.candidate.group_id)
        self.assertFalse(LearningObjectMatchSuggestion.objects.filter(status="pending").exists())

    def test_pending_object_suggestion_does_not_block_publish(self):
        from .tests import authenticated_api_client
        pending = LearningObjectMatchSuggestion.objects.create(
            outline_node=self.topic, source_learning_object=self.source,
            candidate_learning_object=self.candidate, confidence="medium", status="pending")
        # Publishing is a background run now, so the request only has to be
        # accepted -- the pending suggestion must not stop it being started.
        with patch("lessons.views.threading.Thread"):
            response = authenticated_api_client().post(
                f"/api/courses/{self.course.id}/outline-nodes/{self.topic.id}/publish/", format="json")
        self.assertEqual(response.status_code, 202)
        pending.refresh_from_db()
        self.assertEqual(pending.status, "pending")
        self.source.refresh_from_db()
        self.assertEqual(self.source.group_id, self.source_group.id)
