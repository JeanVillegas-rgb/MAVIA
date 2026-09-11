from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from lessons.models import CourseGroup, LearningMaterial, LearningObject, LearningObjectGroup, OutlineNode
from question_generation.models import GenerationRun

from .bulk_version_generation import classify_all_source_versions
from .models import LessonVariant
from .variant_generator import fill_missing_slots


@override_settings(ADAPTIVE_VARIANT_GENERATION_ENABLED=True)
class BulkVersionGenerationTests(TestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Science")
        self.node = OutlineNode.objects.create(course=self.course, title="Matter", order=0)
        self.material = LearningMaterial.objects.create(
            course=self.course,
            outline_node=self.node,
            title="Lesson",
            generated_json={"learning_objects_confirmed": True},
        )
        self.group = LearningObjectGroup.objects.create(outline_node=self.node)
        self.learning_object = LearningObject.objects.create(
            material=self.material,
            group=self.group,
            title="Solid",
            content="A solid has a definite shape and volume.",
        )

    @patch("course.variant_generator._request_variants")
    def test_classification_does_not_generate_missing_slots(self, request_variants):
        LessonVariant.objects.create(
            learning_object=self.learning_object,
            variant="SIMPLIFIED",
            narration="A solid keeps its shape.",
        )
        request_variants.return_value = {
            "SIMPLIFIED": "unused simplified text",
            "ELABORATED": "A solid retains its dimensions because its particles remain fixed.",
        }
        events = []

        summary = classify_all_source_versions(
            self.node,
            on_event=lambda event_type, message, **data: events.append((event_type, data)),
        )

        self.assertEqual(summary["generated_count"], 0)
        self.assertEqual(summary["skipped_count"], 0)
        self.assertEqual(summary["errors"], [])
        self.assertFalse(
            LessonVariant.objects.filter(
                learning_object=self.learning_object,
                variant="ELABORATED",
            ).exists()
        )
        request_variants.assert_not_called()
        self.assertEqual(events[0][0], "versions_classification_started")
        self.assertEqual(events[-1][0], "versions_bulk_finished")

    @override_settings(CONTENT_VERSION_LLM_ENABLED=True)
    @patch("course.variant_generator._request_variants")
    @patch("course.version_assignment.classify_group_versions")
    def test_three_pdf_variants_fill_roles_and_generate_only_the_missing_role(
        self, classify, request_variants,
    ):
        second_material = LearningMaterial.objects.create(
            course=self.course,
            outline_node=self.node,
            title="Second PDF",
            generated_json={"learning_objects_confirmed": True},
        )
        second = LearningObject.objects.create(
            material=second_material,
            group=self.group,
            title="Solid alternative",
            content="Solid particles stay close together.",
        )
        third_material = LearningMaterial.objects.create(
            course=self.course,
            outline_node=self.node,
            title="Third PDF",
            generated_json={"learning_objects_confirmed": True},
        )
        third = LearningObject.objects.create(
            material=third_material,
            group=self.group,
            title="Solid in other words",
            content="A solid has tightly packed particles and keeps its form.",
        )
        classify.return_value = {
            self.learning_object.id: {
                "slot": "ORIGINAL", "confidence": 0.9, "reason": "Baseline."
            },
            second.id: {
                "slot": "SIMPLIFIED", "confidence": 0.8, "reason": "Simpler wording."
            },
            third.id: {
                "slot": "EXTRA", "confidence": 0.8, "reason": "Alternative."
            },
        }
        request_variants.return_value = {
            "SIMPLIFIED": "A solid keeps its shape.",
            "ELABORATED": "A solid keeps its dimensions as particles remain close.",
        }

        summary = classify_all_source_versions(self.node)

        self.assertEqual(summary["grouped_concept_count"], 1)
        self.assertEqual(summary["classified_group_count"], 1)
        self.assertEqual(summary["source_variant_count"], 2)
        self.assertEqual(summary["extra_count"], 1)
        self.assertEqual(summary["generated_count"], 0)
        self.assertTrue(LessonVariant.objects.filter(
            learning_object=self.learning_object,
            source_learning_object=second,
            variant="SIMPLIFIED",
        ).exists())
        self.assertTrue(LessonVariant.objects.filter(
            learning_object=self.learning_object,
            source_learning_object=third,
            variant="EXTRA",
        ).exists())
        self.assertFalse(LessonVariant.objects.filter(
            learning_object=self.learning_object,
            source_learning_object__isnull=True,
            variant="ELABORATED",
        ).exists())

        generated = fill_missing_slots(
            self.learning_object,
            target_slots=["ELABORATED"],
        )
        self.assertEqual(generated["generated"], ["ELABORATED"])
        self.assertTrue(LessonVariant.objects.filter(
            learning_object=self.learning_object,
            source_learning_object__isnull=True,
            variant="ELABORATED",
        ).exists())


class BulkVersionGenerationEndpointTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            username="bulk_version_teacher",
            role=get_user_model().Role.TEACHER,
            is_verified=True,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=user)
        self.course = CourseGroup.objects.create(title="Science")
        self.node = OutlineNode.objects.create(course=self.course, title="Matter", order=0)

    def _url(self):
        return (
            f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/"
            "generate-all-versions/"
        )

    @patch("lessons.views.threading.Thread")
    def test_starts_background_run_without_blocking_request(self, thread):
        response = self.client.post(self._url(), {}, format="json")

        self.assertEqual(response.status_code, 202)
        run = GenerationRun.objects.get(pk=response.data["run_id"])
        self.assertEqual(run.outline_node, self.node)
        self.assertEqual(run.status, "running")
        thread.assert_called_once()
        thread.return_value.start.assert_called_once()

    @patch("lessons.views.threading.Thread")
    def test_refuses_second_expensive_topic_job(self, thread):
        first = self.client.post(self._url(), {}, format="json")
        second = self.client.post(self._url(), {}, format="json")

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.data["run_id"], first.data["run_id"])
        thread.assert_called_once()

    @patch("lessons.views.threading.Thread")
    def test_replaces_a_stale_running_topic_job(self, thread):
        stale = GenerationRun.objects.create(outline_node=self.node)
        GenerationRun.objects.filter(pk=stale.pk).update(
            started_at=timezone.now() - timedelta(minutes=31),
        )

        response = self.client.post(self._url(), {}, format="json")

        self.assertEqual(response.status_code, 202)
        stale.refresh_from_db()
        self.assertEqual(stale.status, "failed")
        self.assertIsNotNone(stale.finished_at)
        self.assertEqual(stale.events.get().event_type, "topic_task_abandoned")
        self.assertNotEqual(response.data["run_id"], stale.id)
        thread.assert_called_once()
