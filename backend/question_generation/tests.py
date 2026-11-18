"""Current question-bank generation contract: no legacy mirrors or fingerprints."""
from unittest.mock import Mock, patch
from django.test import TestCase, override_settings
from lessons.models import CourseGroup, LearningMaterial, LearningObject, OutlineNode
from lessons.test_utils import authenticated_api_client
from .models import Question, GenerationRun
from .services.pipeline import finalize_node_questions, generate_questions_for_material, QUESTION_DISTRIBUTION

class GenerationFixture(TestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Science")
        self.topic = OutlineNode.objects.create(course=self.course, title="Matter")
        self.material = LearningMaterial.objects.create(course=self.course, outline_node=self.topic,
            title="Lesson", status="completed", generated_json={"learning_objects_confirmed": True})
        self.first = LearningObject.objects.create(material=self.material, title="Solid",
            content="Solids have a fixed shape.", order=0)
        self.second = LearningObject.objects.create(material=self.material, title="Liquid",
            content="Liquids flow.", order=1)
        self.classifier = Mock()
        self.classifier.classify.return_value = {
            "bloom_level": "remember", "thinking_order": "LOT", "category": "Facts and Information"}

    def question(self, text, **changes):
        values = dict(node=self.first, outline_node=self.topic, question_text=text,
                      question_format="TF", correct_answer="True", status="draft",
                      source="generated", check_result="supported")
        values.update(changes)
        return Question.objects.create(**values)

    def finalize(self, **kwargs):
        return finalize_node_questions(self.first, self.classifier, **kwargs)

class QuestionGenerationTests(GenerationFixture):
    def test_finalize_labels_and_pairs_supported_draft(self):
        row = self.question("Solids keep their shape?")
        self.finalize()
        row.refresh_from_db()
        self.assertEqual((row.status, row.paired_by, row.thinking_order), ("final", "auto", "LOT"))
        self.assertTrue(row.content_fingerprint)

    def test_unchecked_draft_is_held_for_review(self):
        row = self.question("Solids keep their shape?", check_result="unverified")
        self.finalize()
        row.refresh_from_db()
        self.assertEqual(row.status, "review")

    def test_create_level_is_excluded_from_mcq_tf_bank(self):
        row = self.question("Design a new material?")
        self.classifier.classify.return_value = {
            "bloom_level": "create", "thinking_order": None, "category": "Outcome"}
        self.finalize()
        self.assertFalse(Question.objects.filter(pk=row.pk).exists())

    def test_normalized_duplicate_drafts_are_not_saved_twice(self):
        self.question("What is matter?")
        self.question("what is MATTER")
        self.finalize()
        self.assertEqual(Question.objects.filter(status="final").count(), 1)

    def test_cap_comes_from_generation_configuration(self):
        for index in range(QUESTION_DISTRIBUTION["LOT"]["count"] + 2):
            self.question(f"Solid property number {index}?")
        self.finalize()
        self.assertEqual(Question.objects.filter(status="final").count(),
                         QUESTION_DISTRIBUTION["LOT"]["count"])

    def test_replacement_preserves_other_concepts_and_teacher_edited_questions(self):
        old = self.question("Old question?", status="final", thinking_order="LOT", paired_by="auto")
        other = self.question("Liquid question?", node=self.second, status="final",
                              thinking_order="LOT", paired_by="auto")
        edited = self.question("Teacher corrected question?", status="final",
                               thinking_order="LOT", paired_by="teacher", teacher_edited=True)
        self.question("New question?")
        self.finalize()
        self.assertFalse(Question.objects.filter(pk=old.pk).exists())
        self.assertTrue(Question.objects.filter(pk=other.pk).exists())
        self.assertTrue(Question.objects.filter(pk=edited.pk).exists())

    def test_append_preserves_existing_bank(self):
        old = self.question("Old question?", status="final", thinking_order="LOT", paired_by="auto")
        self.question("New question?")
        self.finalize(append=True)
        self.assertTrue(Question.objects.filter(pk=old.pk).exists())

    @override_settings(QUESTION_GENERATION_ROUNDS=1)
    @patch("question_generation.services.pipeline._get_classifier")
    @patch("question_generation.services.pipeline.generate_questions_for_node")
    def test_material_generation_stays_scoped_to_requested_node(self, generate, classifier):
        row = self.question("Existing liquid question?", node=self.second)
        generate.return_value = ([], "", False)
        result = generate_questions_for_material(self.material, [self.first.id])
        self.assertEqual(result, [])
        self.assertEqual(generate.call_args.args[0].id, self.first.id)
        self.assertTrue(Question.objects.filter(pk=row.pk).exists())

class StartGenerationTests(GenerationFixture):
    @patch("question_generation.views.threading.Thread")
    def test_node_start_schedules_only_selected_object(self, thread):
        response = authenticated_api_client().post(
            f"/api/generation/materials/{self.material.id}/nodes/{self.first.id}/start/", {},
            format="json")
        self.assertEqual(response.status_code, 201)
        run = GenerationRun.objects.get(pk=response.data["run_id"])
        self.assertEqual(run.node_id, self.first.id)
        self.assertEqual(thread.call_args.kwargs["args"], (run.id, self.first.id))
        thread.return_value.start.assert_called_once()

    @patch("question_generation.views.threading.Thread")
    def test_another_material_cannot_start_this_node(self, thread):
        other = LearningMaterial.objects.create(course=self.course, title="Other", status="completed")
        response = authenticated_api_client().post(
            f"/api/generation/materials/{other.id}/nodes/{self.first.id}/start/", {})
        self.assertEqual(response.status_code, 400)
        thread.assert_not_called()

    @patch("question_generation.views.threading.Thread")
    def test_student_cannot_start_generation(self, thread):
        from django.contrib.auth import get_user_model
        from rest_framework.test import APIClient
        student = get_user_model().objects.create(username="student-test", role="STUDENT")
        client = APIClient()
        client.force_authenticate(student)
        response = client.post(
            f"/api/generation/materials/{self.material.id}/nodes/{self.first.id}/start/", {})
        self.assertEqual(response.status_code, 403)
        thread.assert_not_called()
