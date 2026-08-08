from unittest.mock import Mock, patch

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from lessons.models import CourseGroup, LearningMaterial, LearningObject
from lessons.services.audio_generator import generate_material_audio_playlist

from .models import GeneratedQuestion, GenerationRun
from .services.pipeline import (
    finalize_node_questions,
    generate_questions_for_material,
)


class _StubClassifier:
    """Returns a canned classification per question text."""

    def __init__(self, by_text, default=("understand", "LOT", "Meaning")):
        self.by_text = by_text
        self.default = default

    def classify(self, question_text):
        bloom, order, category = self.by_text.get(question_text, self.default)
        return {"bloom_level": bloom, "thinking_order": order, "category": category}


def _draft(node, question_text, question_format="TF", correct_answer="True"):
    return GeneratedQuestion.objects.create(
        node=node,
        question_text=question_text,
        question_format=question_format,
        correct_answer=correct_answer,
        status="draft",
    )


class QuestionGenerationScopeTests(TestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Science")
        self.material = LearningMaterial.objects.create(
            course=self.course,
            title="Matter",
            pdf_file="learning_materials/matter.pdf",
            status=LearningMaterial.Status.COMPLETED,
            generated_json={
                "audio_playlist_generated": True,
                "lesson_playlist": [
                    {
                        "title": "First Node",
                        "audio_url": "/media/audio_lessons/material_1/playlist_item_1.mp3",
                    }
                ],
            },
        )
        self.first_node = LearningObject.objects.create(
            material=self.material,
            kind=LearningObject.Kind.TEXT,
            title="First Node",
            content="First content",
            order=0,
        )
        self.second_node = LearningObject.objects.create(
            material=self.material,
            kind=LearningObject.Kind.TEXT,
            title="Second Node",
            content="Second content",
            order=1,
        )
        self.existing_second_question = GeneratedQuestion.objects.create(
            node=self.second_node,
            question_text="Existing second question?",
            question_format="TF",
            correct_answer="True",
            bloom_level="remember",
            thinking_order="LOT",
            category="Facts and Information",
            status="final",
        )

    def test_node_scoped_generation_does_not_touch_other_nodes(self):
        generated = [
            GeneratedQuestion(
                node=self.first_node,
                question_text="Generated first question?",
                question_format="TF",
                correct_answer="True",
                bloom_level="remember",
                thinking_order="LOT",
                category="Facts and Information",
                status="final",
            )
        ]
        generated[0].save()

        with patch(
            "question_generation.services.pipeline._get_classifier",
            return_value=Mock(),
        ), patch(
            "question_generation.services.pipeline.generate_questions_for_node",
            return_value=generated,
        ) as generate_node:
            result = generate_questions_for_material(
                self.material,
                node_ids=[self.first_node.id],
            )

        self.assertEqual(result, generated)
        generate_node.assert_called_once()
        self.assertEqual(
            list(self.first_node.generated_questions.values_list("question_text", flat=True)),
            ["Generated first question?"],
        )
        self.assertEqual(
            list(self.second_node.generated_questions.values_list("id", flat=True)),
            [self.existing_second_question.id],
        )

    def test_finalize_labels_drafts_with_thinking_order_and_promotes_them(self):
        _draft(self.first_node, "What is matter?")
        _draft(self.first_node, "Why is ice less dense than water?")

        classifier = _StubClassifier({
            "What is matter?": ("remember", "LOT", "Facts and Information"),
            "Why is ice less dense than water?": ("analyze", "HOT", "Skills"),
        })
        finalize_node_questions(self.first_node, classifier)

        stored = {q.question_text: q for q in self.first_node.generated_questions.all()}
        self.assertEqual(len(stored), 2)
        self.assertEqual(stored["What is matter?"].thinking_order, "LOT")
        self.assertEqual(stored["What is matter?"].bloom_level, "remember")
        self.assertEqual(stored["Why is ice less dense than water?"].thinking_order, "HOT")
        self.assertEqual(stored["Why is ice less dense than water?"].bloom_level, "analyze")
        self.assertTrue(all(q.status == "final" for q in stored.values()))

    def test_finalize_excludes_create_level_drafts(self):
        _draft(self.first_node, "Design an experiment about matter.")
        _draft(self.first_node, "What is matter?")

        classifier = _StubClassifier({
            "Design an experiment about matter.": ("create", None, "Outcome"),
            "What is matter?": ("remember", "LOT", "Facts and Information"),
        })
        finalize_node_questions(self.first_node, classifier)

        self.assertEqual(
            list(self.first_node.generated_questions.values_list("question_text", flat=True)),
            ["What is matter?"],
        )

    def test_finalize_removes_duplicate_drafts(self):
        _draft(self.first_node, "What is matter?")
        _draft(self.first_node, "what is  MATTER")

        classifier = _StubClassifier({}, default=("remember", "LOT", "Facts and Information"))
        finalize_node_questions(self.first_node, classifier)

        self.assertEqual(self.first_node.generated_questions.count(), 1)

    def test_finalize_trims_surplus_beyond_the_target_count(self):
        for index in range(8):
            _draft(self.first_node, f"Recall question number {index}?")

        classifier = _StubClassifier({}, default=("remember", "LOT", "Facts and Information"))
        finalize_node_questions(self.first_node, classifier)

        # QUESTION_DISTRIBUTION caps LOT at 5
        self.assertEqual(self.first_node.generated_questions.count(), 5)

    def test_finalize_replaces_the_previous_runs_questions(self):
        old = GeneratedQuestion.objects.create(
            node=self.first_node,
            question_text="Question from an earlier run?",
            question_format="TF",
            correct_answer="True",
            bloom_level="remember",
            thinking_order="LOT",
            category="Facts and Information",
            status="final",
        )
        _draft(self.first_node, "What is matter?")

        classifier = _StubClassifier({}, default=("remember", "LOT", "Facts and Information"))
        finalize_node_questions(self.first_node, classifier)

        self.assertFalse(GeneratedQuestion.objects.filter(id=old.id).exists())
        self.assertEqual(
            list(self.first_node.generated_questions.values_list("question_text", flat=True)),
            ["What is matter?"],
        )

    def test_finalize_marks_existing_audio_stale(self):
        _draft(self.first_node, "What is matter?")

        classifier = _StubClassifier({}, default=("remember", "LOT", "Facts and Information"))
        finalize_node_questions(self.first_node, classifier)

        self.material.refresh_from_db()
        self.assertFalse(self.material.generated_json["audio_playlist_generated"])

    @patch("lessons.services.audio_generator.synthesize_text_to_audio")
    def test_lesson_audio_scope_preserves_existing_question_tracks(self, mock_synthesize):
        mock_synthesize.side_effect = lambda _text, path: path.with_suffix(".mp3")
        self.material.generated_json = {
            "narration_script": [
                {"order": 1, "content": "First lesson narration."},
            ],
            "lesson_playlist": [
                {"title": "First Node", "type": None, "narration_item_order": 1},
                {
                    "title": "Question 1",
                    "type": "practice_question",
                    "question_id": 10,
                    "audio_url": "/media/audio_lessons/material_1/question_10.mp3",
                    "audio_file": "audio_lessons/material_1/question_10.mp3",
                },
            ],
            "question_audio_generated": True,
        }
        self.material.save(update_fields=["generated_json"])

        result = generate_material_audio_playlist(self.material, scope="lessons")

        self.assertEqual(result["generated_count"], 1)
        self.material.refresh_from_db()
        playlist = self.material.generated_json["lesson_playlist"]
        self.assertEqual(
            playlist[0]["audio_url"],
            f"/media/audio_lessons/material_{self.material.id}/playlist_item_1.mp3",
        )
        self.assertEqual(playlist[1]["audio_url"], "/media/audio_lessons/material_1/question_10.mp3")
        self.assertTrue(self.material.generated_json["lesson_audio_generated"])
        self.assertTrue(self.material.generated_json["question_audio_generated"])


class StartGenerationViewScopeTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.course = CourseGroup.objects.create(title="Science")
        self.material = LearningMaterial.objects.create(
            course=self.course,
            title="Matter",
            pdf_file="learning_materials/matter.pdf",
            status=LearningMaterial.Status.COMPLETED,
        )
        self.first_node = LearningObject.objects.create(
            material=self.material,
            kind=LearningObject.Kind.TEXT,
            title="Gas",
            content="A gas spreads out.",
            order=0,
        )
        self.second_node = LearningObject.objects.create(
            material=self.material,
            kind=LearningObject.Kind.TEXT,
            title="Liquid",
            content="A liquid flows.",
            order=1,
        )

    def test_editing_question_marks_existing_audio_stale(self):
        self.material.generated_json = {
            "audio_playlist_generated": True,
            "lesson_playlist": [
                {
                    "title": "Question 1",
                    "type": "practice_question",
                    "audio_url": "/media/audio_lessons/material_1/question_1.mp3",
                }
            ],
        }
        self.material.save(update_fields=["generated_json"])
        question = GeneratedQuestion.objects.create(
            node=self.first_node,
            question_text="Old question?",
            question_format="TF",
            correct_answer="True",
            bloom_level="remember",
            thinking_order="LOT",
            category="Facts and Information",
            status="final",
        )

        response = self.client.patch(
            f"/api/generation/questions/{question.id}/",
            {"question_text": "Updated question?"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.material.refresh_from_db()
        self.assertFalse(self.material.generated_json["audio_playlist_generated"])

    @patch("question_generation.views.threading.Thread")
    def test_node_start_url_scopes_run_to_only_that_node(self, mock_thread):
        response = self.client.post(
            f"/api/generation/materials/{self.material.id}/nodes/{self.first_node.id}/start/",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["node_id"], self.first_node.id)
        run = GenerationRun.objects.get(id=response.data["run_id"])
        self.assertEqual(run.node_id, self.first_node.id)
        self.assertEqual(mock_thread.call_args.kwargs["args"], (run.id, self.material.id, [self.first_node.id]))
