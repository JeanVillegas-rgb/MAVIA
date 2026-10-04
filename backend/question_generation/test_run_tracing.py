"""The tracer feeds the progress dialog and the terminal from one call.

Progress reporting used to live in two halves that could not see each other:
extraction logged and stored nothing, the pipelines stored and logged nothing.
These tests pin the property that makes the halves agree -- one call produces
both -- and the sequence behaviour the polling client depends on.
"""

from unittest.mock import patch

from django.test import TestCase
from rest_framework import status

from lessons.models import CourseGroup, LearningMaterial, LearningObject
from lessons.tests import authenticated_api_client

from .models import GenerationEvent, GenerationRun
from .tracing import run_tracer


class RunTracerTests(TestCase):
    def setUp(self):
        self.run = GenerationRun.objects.create(kind=GenerationRun.Kind.EXTRACTION)

    def test_each_call_stores_one_event_in_order(self):
        record = run_tracer(self.run)

        record("started", "Reading the PDF")
        record("finished", "Done")

        events = list(self.run.events.order_by("seq"))
        self.assertEqual([item.seq for item in events], [1, 2])
        self.assertEqual([item.event_type for item in events], ["started", "finished"])
        self.assertEqual(events[0].message, "Reading the PDF")

    def test_sequence_continues_from_events_already_stored(self):
        # A retry, or a second stage appending to the same run, must not reuse
        # sequence numbers the polling client has already consumed.
        GenerationEvent.objects.create(run=self.run, seq=7, event_type="earlier")

        run_tracer(self.run)("later", "Next step")

        self.assertEqual(self.run.events.order_by("-seq").first().seq, 8)

    def test_position_data_is_stored_for_the_progress_bar(self):
        run_tracer(self.run)("step", "Chunk 2", index=2, total=4)

        event = self.run.events.get()
        self.assertEqual(event.data, {"index": 2, "total": 4})

    def test_an_event_without_data_stores_none(self):
        run_tracer(self.run)("step", "No numbers here")

        self.assertIsNone(self.run.events.get().data)

    def test_the_same_call_reaches_the_terminal(self):
        with self.assertLogs("question_generation.tracing", level="INFO") as captured:
            run_tracer(self.run)("step", "Reading the PDF", index=3, total=12)

        line = "\n".join(captured.output)
        self.assertIn("Reading the PDF", line)
        self.assertIn("(3/12)", line)

    def test_the_terminal_line_names_the_pipeline(self):
        with self.assertLogs("question_generation.tracing", level="INFO") as captured:
            run_tracer(self.run)("step", "Working")

        self.assertIn("PDF extraction", "\n".join(captured.output))


class TraceEndpointKindTests(TestCase):
    """One dialog serves every pipeline, so it has to know which one it shows."""

    def setUp(self):
        self.client = authenticated_api_client()

    def test_trace_reports_the_kind_of_run(self):
        run = GenerationRun.objects.create(kind=GenerationRun.Kind.PUBLISH)

        response = self.client.get(f"/api/generation/runs/{run.id}/events/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["run"]["kind"], "publish")
        self.assertEqual(response.data["run"]["kind_label"], "Topic publish")

    def test_a_run_defaults_to_question_generation(self):
        # Existing rows predate the field; they were all question runs.
        run = GenerationRun.objects.create()

        response = self.client.get(f"/api/generation/runs/{run.id}/events/")

        self.assertEqual(response.data["run"]["kind"], "questions")


class ConcurrentRunGuardTests(TestCase):
    """Question generation refuses to start while another *question* run is live.

    The check had nothing to scope itself by, so any run at all blocked it. Once
    extraction reports progress through a run of its own, an unscoped check would
    refuse to generate questions because a PDF was still being read -- a conflict
    the teacher can neither see the cause of nor act on.
    """

    def setUp(self):
        self.client = authenticated_api_client()
        self.course = CourseGroup.objects.create(title="Science")
        self.material = LearningMaterial.objects.create(
            course=self.course,
            title="Matter",
            pdf_file="learning_materials/matter.pdf",
            status=LearningMaterial.Status.COMPLETED,
        )
        self.node = LearningObject.objects.create(
            material=self.material,
            kind=LearningObject.Kind.TEXT,
            title="Gas",
            content="A gas spreads out.",
            order=0,
        )

    def _start(self):
        return self.client.post(
            f"/api/generation/materials/{self.material.id}/nodes/{self.node.id}/start/",
            {},
            format="json",
        )

    @patch("question_generation.views.threading.Thread")
    def test_a_live_question_run_still_blocks(self, thread):
        live = GenerationRun.objects.create(kind=GenerationRun.Kind.QUESTIONS)

        response = self._start()

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["run_id"], live.id)
        thread.assert_not_called()

    @patch("question_generation.views.threading.Thread")
    def test_a_live_extraction_run_does_not_block(self, thread):
        GenerationRun.objects.create(kind=GenerationRun.Kind.EXTRACTION)

        response = self._start()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        thread.assert_called_once()

    @patch("question_generation.views.threading.Thread")
    def test_a_live_publish_run_does_not_block(self, thread):
        GenerationRun.objects.create(kind=GenerationRun.Kind.PUBLISH)

        response = self._start()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    @patch("question_generation.views.threading.Thread")
    def test_the_run_it_creates_is_tagged_as_a_question_run(self, thread):
        response = self._start()

        run = GenerationRun.objects.get(id=response.data["run_id"])
        self.assertEqual(run.kind, GenerationRun.Kind.QUESTIONS)
