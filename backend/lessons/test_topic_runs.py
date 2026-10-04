"""A topic task that fails must never be left looking like it is still running.

Measured 2026-09-27: "generate all versions" hit "database is locked"; its
error handler then tried to record the failure, which hit the same lock, and
that skipped the line marking the run failed. The run was saved with a finish
time and status "running", and the topic refused every new task with
"Another topic task is already running."
"""

from unittest.mock import patch

from django.db import OperationalError
from django.test import TestCase
from django.utils import timezone

from lessons import views
from lessons.models import CourseGroup, OutlineNode
from question_generation.models import GenerationEvent, GenerationRun


class TopicRunTests(TestCase):
    def setUp(self):
        course = CourseGroup.objects.create(title="Science")
        self.node = OutlineNode.objects.create(course=course, title="Matter", order=0, depth=0)

    def _run(self, **fields):
        return GenerationRun.objects.create(
            outline_node=self.node, kind=GenerationRun.Kind.VERSIONS, **fields,
        )

    def test_a_run_whose_failure_cannot_be_recorded_is_still_closed_as_failed(self):
        run = self._run()
        locked = OperationalError("database is locked")

        with patch.object(views, "classify_all_source_versions", side_effect=locked), \
                patch.object(GenerationEvent.objects, "create", side_effect=locked):
            views._run_all_versions_in_background(run.id, self.node.id)

        run.refresh_from_db()
        self.assertEqual(run.status, "failed")
        self.assertIsNotNone(run.finished_at)
        self.assertIsNone(views._active_topic_run(self.node))

    def test_a_failure_is_recorded_when_the_database_allows_it(self):
        run = self._run()

        with patch.object(views, "classify_all_source_versions", side_effect=RuntimeError("boom")):
            views._run_all_versions_in_background(run.id, self.node.id)

        run.refresh_from_db()
        self.assertEqual(run.status, "failed")
        self.assertTrue(run.events.filter(event_type="versions_bulk_failed").exists())

    def test_a_run_with_a_finish_time_does_not_block_the_topic(self):
        self._run(status="running", finished_at=timezone.now())

        self.assertIsNone(views._active_topic_run(self.node))

    def test_a_live_run_still_blocks_the_topic(self):
        run = self._run(status="running")

        self.assertEqual(views._active_topic_run(self.node), run)
