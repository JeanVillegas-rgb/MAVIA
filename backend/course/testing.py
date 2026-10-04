"""Test helpers for content-version assignment."""

from unittest.mock import patch

from .content_measures import MeasurementUnavailable


def without_measurements(testcase):
    """Run a test as if the sentence encoder were unavailable.

    Tests of slot collisions, bundles and generation give Gemma's label as a
    mocked answer over short made-up texts. The measured check would judge
    those texts, which is not what such a test is about, so it is switched
    off the way production switches it off without the encoder: Gemma's label
    is kept and ``measurements_unavailable`` is recorded. The check itself is
    tested in ``test_version_assignment`` (VersionAssignmentTests and
    MeasuredCheckTests).
    """
    patcher = patch(
        "course.content_measures.measure_versions",
        side_effect=MeasurementUnavailable("switched off in this test"),
    )
    patcher.start()
    testcase.addCleanup(patcher.stop)
