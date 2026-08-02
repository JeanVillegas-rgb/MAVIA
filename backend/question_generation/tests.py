from unittest.mock import Mock, patch

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from lessons.models import CourseGroup, LearningMaterial, LearningObject
from lessons.services.audio_generator import generate_material_audio_playlist

from .models import GeneratedQuestion, GenerationRun
from .services.pipeline import generate_questions_for_material, save_node_questions


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
            difficulty="easy",
            category="Facts and Information",
            intended_difficulty="easy",
            difficulty_match=True,
        )

    def test_node_scoped_generation_does_not_touch_other_nodes(self):
        generated = [
            {
                "node": self.first_node,
                "question": "Generated first question?",
                "format": "TF",
                "correct_answer": "True",
                "explanation": "",
                "bloom_level": "remember",
                "difficulty": "easy",
                "category": "Facts and Information",
                "intended_difficulty": "easy",
                "difficulty_match": True,
            }
        ]

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

    def test_saved_question_uses_corrected_classifier_difficulty(self):
        save_node_questions(
            self.first_node,
            [
                {
                    "node": self.first_node,
                    "question": "What is matter?",
                    "format": "TF",
                    "correct_answer": "True",
                    "explanation": "",
                    "bloom_level": "remember",
                    "difficulty": "easy",
                    "category": "Facts and Information",
                    "intended_difficulty": "hard",
                    "difficulty_match": False,
                }
            ],
        )

        stored = self.first_node.generated_questions.get()
        self.assertEqual(stored.difficulty, "easy")
        self.assertEqual(stored.intended_difficulty, "easy")
        self.assertTrue(stored.difficulty_match)

    def test_saving_questions_marks_existing_audio_stale(self):
        save_node_questions(
            self.first_node,
            [
                {
                    "node": self.first_node,
                    "question": "What is matter?",
                    "format": "TF",
                    "correct_answer": "True",
                    "explanation": "",
                    "bloom_level": "remember",
                    "difficulty": "easy",
                    "category": "Facts and Information",
                    "intended_difficulty": "easy",
                    "difficulty_match": True,
                }
            ],
        )

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
            difficulty="easy",
            category="Facts and Information",
            intended_difficulty="easy",
            difficulty_match=True,
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
