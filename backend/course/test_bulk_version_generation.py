from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from lessons.models import CourseGroup, LearningMaterial, LearningObject, LearningObjectGroup, OutlineNode
from question_generation.models import GenerationRun

from .bulk_version_generation import generate_all_missing_versions
from .models import LessonVariant


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
    def test_generates_only_missing_slots_and_reports_progress(self, request_variants):
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

        summary = generate_all_missing_versions(
            self.node,
            on_event=lambda event_type, message, **data: events.append((event_type, data)),
        )

        self.assertEqual(summary["generated_count"], 1)
        self.assertEqual(summary["skipped_count"], 1)
        self.assertEqual(summary["errors"], [])
        self.assertTrue(
            LessonVariant.objects.filter(
                learning_object=self.learning_object,
                variant="ELABORATED",
            ).exists()
        )
        self.assertEqual(events[0][0], "versions_bulk_started")
        self.assertEqual(events[-1][0], "versions_bulk_finished")


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
