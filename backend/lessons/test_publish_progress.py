from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from question_generation.models import GenerationEvent, GenerationRun

from .models import CourseGroup, LearningMaterial, LearningObject, LearningObjectGroup, OutlineNode
from .services.topic_publish import run_topic_publish


@override_settings(
    ADAPTIVE_VARIANT_GENERATION_ENABLED=True,
    ADAPTIVE_VARIANT_LLM_MODEL="gemma3:4b",
)
class TopicPublishServiceTests(TestCase):
    """The work itself, run synchronously. The view only owns the threading."""

    def setUp(self):
        self.course = CourseGroup.objects.create(title="Grade 1 Science")
        self.node = OutlineNode.objects.create(
            course=self.course, title="Matter", order=0, depth=0
        )
        self.material = LearningMaterial.objects.create(
            course=self.course,
            outline_node=self.node,
            title="Lesson 1",
            generated_json={"learning_objects_confirmed": True},
        )
        group = LearningObjectGroup.objects.create(outline_node=self.node)
        self.obj = LearningObject.objects.create(
            material=self.material,
            group=group,
            title="Solid",
            content="A solid holds its shape and does not flow away from its place.",
            order=0,
        )
        self.events = []
        version_audio_patch = patch("lessons.services.topic_publish.generate_version_audio", return_value={"generated_count": 0})
        self.version_audio = version_audio_patch.start()
        self.addCleanup(version_audio_patch.stop)

    def _record(self, event_type, message, **data):
        self.events.append((event_type, message, data))

    def _run(self):
        return run_topic_publish(
            self.course, self.node, set_confirmed=lambda material: None, on_event=self._record
        )

    @patch("lessons.services.topic_publish.populate_missing_image_descriptions")
    @patch("lessons.services.topic_publish.generate_material_audio_playlist")
    @patch("course.variant_generator._request_variants")
    def test_marks_the_topic_published(self, request_variants, audio, images):
        images.return_value = {"generated_count": 0, "errors": []}
        audio.return_value = {"generated_count": 1}
        request_variants.return_value = {"SIMPLIFIED": "a", "ELABORATED": "b"}

        summary = self._run()

        self.node.refresh_from_db()
        self.assertTrue(self.node.published)
        self.assertTrue(summary["published"])
        self.assertEqual(summary["audio_generated_count"], 1)

    @patch("lessons.services.topic_publish.populate_missing_image_descriptions")
    @patch("lessons.services.topic_publish.generate_material_audio_playlist")
    @patch("course.variant_generator._request_variants")
    def test_reports_incomplete_versions(self, request_variants, audio, images):
        from course.variant_generator import VariantGenerationError
        images.return_value = {"generated_count": 0, "errors": []}
        audio.return_value = {"generated_count": 0}
        request_variants.side_effect = VariantGenerationError("Gemma did not return valid JSON.")

        summary = self._run()

        self.assertIn(self.obj.id, summary["incomplete_versions"])
        self.assertFalse(summary["published"])
        self.node.refresh_from_db()
        self.assertFalse(self.node.published)
        self.assertIsNone(self.node.published_at)

    @patch("lessons.services.topic_publish.populate_missing_image_descriptions")
    @patch("lessons.services.topic_publish.generate_material_audio_playlist")
    @patch("course.variant_generator._request_variants")
    def test_audio_failure_does_not_publish(self, request_variants, audio, images):
        from .services.audio_generator import AudioGenerationError
        images.return_value = {"generated_count": 0, "errors": []}
        request_variants.return_value = {"SIMPLIFIED": "a", "ELABORATED": "b"}
        audio.side_effect = AudioGenerationError("Audio unavailable")
        summary = self._run()
        self.assertFalse(summary["published"])
        self.assertTrue(summary["audio_errors"])
        self.node.refresh_from_db()
        self.assertFalse(self.node.published)
        self.assertEqual(self.events[-1][0], "publish_failed")

    @patch("lessons.services.topic_publish.populate_missing_image_descriptions")
    @patch("lessons.services.topic_publish.generate_material_audio_playlist")
    @patch("course.variant_generator._request_variants")
    def test_already_settled_content_costs_nothing(self, request_variants, audio, images):
        from course.models import LessonVariant
        images.return_value = {"generated_count": 0, "errors": []}
        audio.return_value = {"generated_count": 0}
        for slot in ("SIMPLIFIED", "ELABORATED"):
            LessonVariant.objects.create(
                learning_object=self.obj, variant=slot,
                narration=f"{slot} text", origin="source_pdf",
            )

        summary = self._run()

        request_variants.assert_not_called()
        self.assertEqual(summary["incomplete_versions"], [])

    @patch("lessons.services.topic_publish.populate_missing_image_descriptions")
    @patch("lessons.services.topic_publish.generate_material_audio_playlist")
    @patch("course.variant_generator._request_variants")
    def test_emits_a_phase_event_for_each_stage(self, request_variants, audio, images):
        images.return_value = {"generated_count": 0, "errors": []}
        audio.return_value = {"generated_count": 0}
        request_variants.return_value = {"SIMPLIFIED": "a", "ELABORATED": "b"}

        self._run()

        kinds = [event_type for event_type, _, _ in self.events]
        self.assertIn("publish_started", kinds)
        self.assertIn("publish_finished", kinds)
        for phase in ("image_descriptions", "versions", "audio"):
            self.assertTrue(
                any(phase in event_type for event_type in kinds),
                msg=f"no event for phase {phase}: {kinds}",
            )

    @patch("lessons.services.topic_publish.populate_missing_image_descriptions")
    @patch("lessons.services.topic_publish.generate_material_audio_playlist")
    @patch("course.variant_generator._request_variants")
    def test_progress_events_carry_position_and_total(self, request_variants, audio, images):
        images.return_value = {"generated_count": 0, "errors": []}
        audio.return_value = {"generated_count": 0}
        request_variants.return_value = {"SIMPLIFIED": "a", "ELABORATED": "b"}

        self._run()

        progress = [data for _, _, data in self.events if "index" in data]
        self.assertTrue(progress, "no event reported its position")
        for data in progress:
            self.assertIn("total", data)
            self.assertLessEqual(data["index"], data["total"])


@override_settings(
    ADAPTIVE_VARIANT_GENERATION_ENABLED=True,
    ADAPTIVE_VARIANT_LLM_MODEL="gemma3:4b",
)
class PublishEndpointTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="teacher", password="pw", role="TEACHER"
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.course = CourseGroup.objects.create(title="Grade 1 Science")
        self.node = OutlineNode.objects.create(
            course=self.course, title="Matter", order=0, depth=0
        )
        self.material = LearningMaterial.objects.create(
            course=self.course,
            outline_node=self.node,
            title="Lesson 1",
            generated_json={"learning_objects_confirmed": True},
        )
        LearningObject.objects.create(
            material=self.material, title="Solid", content="A solid holds its shape.", order=0
        )

    def _publish(self):
        return self.client.post(
            f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/publish/"
        )

    @patch("lessons.views.threading.Thread")
    def test_publish_starts_a_run_and_returns_its_id(self, thread):
        response = self._publish()

        self.assertEqual(response.status_code, 202)
        run = GenerationRun.objects.get(id=response.data["run_id"])
        self.assertEqual(run.outline_node_id, self.node.id)
        self.assertEqual(run.status, "running")
        thread.assert_called_once()

    @patch("lessons.views.threading.Thread")
    def test_publish_does_not_block_on_the_work(self, thread):
        self._publish()
        # The work happens on the thread; the request must not have run it.
        self.node.refresh_from_db()
        self.assertFalse(self.node.published)

    @patch("lessons.views.threading.Thread")
    def test_second_publish_while_one_is_running_is_refused(self, thread):
        first = self._publish()
        second = self._publish()

        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.data["run_id"], first.data["run_id"])

    @patch("lessons.views.threading.Thread")
    def test_unconfirmed_topic_is_still_rejected_before_a_run_is_made(self, thread):
        self.material.generated_json = {"learning_objects_confirmed": False}
        self.material.save(update_fields=["generated_json"])

        response = self._publish()

        self.assertEqual(response.status_code, 400)
        self.assertFalse(GenerationRun.objects.exists())
        thread.assert_not_called()


class PublishRunTraceTests(TestCase):
    """A publish run has no material, so the trace endpoint must tolerate that."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="teacher2", password="pw", role="TEACHER"
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        course = CourseGroup.objects.create(title="Grade 1 Science")
        self.node = OutlineNode.objects.create(
            course=course, title="Matter", order=0, depth=0
        )
        self.run = GenerationRun.objects.create(outline_node=self.node)
        GenerationEvent.objects.create(
            run=self.run, seq=1, event_type="publish_started", message="Publishing Matter"
        )

    def test_trace_endpoint_serves_a_run_without_a_material(self):
        response = self.client.get(f"/api/generation/runs/{self.run.id}/events/")

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data["run"]["material_id"])
        self.assertEqual(response.data["run"]["outline_node_id"], self.node.id)
        self.assertEqual(response.data["events"][0]["event_type"], "publish_started")
