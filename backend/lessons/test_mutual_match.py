"""Mutual best match: a pair is proposed only when both objects choose each other.

The matcher asks one direction only — every learning object picks its closest
partner in the other PDF. When two PDFs are lopsided (59 objects against 10),
many objects pick the same partner, and those nominations are mutually
exclusive: at most one of them can be the same concept. Requiring reciprocity
is what stops one real question becoming a dozen forced declines.
"""

from types import SimpleNamespace
from unittest.mock import Mock

from django.test import SimpleTestCase

from lessons.services.learning_resource_linker import (
    _is_mutual_best_match,
    _nominated_candidate,
)


def _object(object_id):
    return SimpleNamespace(
        id=object_id,
        title=f"Object {object_id}",
        content=f"Content for object {object_id}",
        kind="text",
        order=object_id,
        section_title="",
        material=SimpleNamespace(id=100 + object_id),
    )


class MutualBestMatchTests(SimpleTestCase):
    def setUp(self):
        self.source = _object(1)
        self.candidate = _object(2)
        self.other = _object(3)

    def _matcher_returning(self, candidate):
        return Mock(return_value={"candidate": candidate, "confidence": "medium"})

    def test_pair_is_proposed_when_the_nomination_runs_both_ways(self):
        matcher = self._matcher_returning(self.source)
        self.assertTrue(
            _is_mutual_best_match(matcher, self.candidate, self.source, {})
        )

    def test_pair_is_dropped_when_the_candidate_prefers_someone_else(self):
        """The flood case: many objects nominate one popular partner, which
        nominates only its own single best back."""
        matcher = self._matcher_returning(self.other)
        self.assertFalse(
            _is_mutual_best_match(matcher, self.candidate, self.source, {})
        )

    def test_pair_is_dropped_when_the_candidate_nominates_nothing(self):
        matcher = Mock(return_value=None)
        self.assertFalse(
            _is_mutual_best_match(matcher, self.candidate, self.source, {})
        )

    def test_pair_is_dropped_when_the_candidate_decision_has_no_candidate(self):
        matcher = Mock(return_value={"candidate": None, "confidence": None})
        self.assertFalse(
            _is_mutual_best_match(matcher, self.candidate, self.source, {})
        )

    def test_a_failed_lookup_keeps_the_pair_rather_than_emptying_the_queue(self):
        """A transient model failure is not evidence against a pair. Failing
        closed here would silently wipe the teacher's review queue."""
        matcher = Mock(side_effect=RuntimeError("model unavailable"))
        with self.assertLogs("lessons.services.learning_resource_linker", "ERROR"):
            self.assertTrue(
                _is_mutual_best_match(matcher, self.candidate, self.source, {})
            )

    def test_the_reciprocal_lookup_is_cached_per_object(self):
        """The popular partner is asked about once, not once per suitor —
        which is what keeps the extra work proportional to objects, not pairs.
        """
        matcher = self._matcher_returning(self.source)
        cache = {}
        for _ in range(5):
            _is_mutual_best_match(matcher, self.candidate, self.source, cache)
        self.assertEqual(matcher.call_count, 1)

    def test_the_lookup_asks_about_the_candidates_own_material(self):
        matcher = self._matcher_returning(self.source)
        _nominated_candidate(matcher, self.candidate, {})
        args, kwargs = matcher.call_args
        self.assertIs(args[0], self.candidate.material)
        self.assertEqual(kwargs["source_object_id"], self.candidate.id)

    def test_an_ambiguous_rival_nomination_does_not_veto_the_pair(self):
        """Three PDFs carrying the same concept: the candidate's pick between
        two equally good partners is arbitrary, so it is not evidence against
        this pair. The teacher still needs to see the duplicate."""
        matcher = Mock(return_value={
            "candidate": self.other,
            "confidence": "medium",
            "evidence": {"winner_margin": 0.0},
        })
        self.assertTrue(
            _is_mutual_best_match(matcher, self.candidate, self.source, {})
        )

    def test_a_decisive_rival_nomination_does_veto_the_pair(self):
        matcher = Mock(return_value={
            "candidate": self.other,
            "confidence": "high",
            "evidence": {"winner_margin": 0.21},
        })
        self.assertFalse(
            _is_mutual_best_match(matcher, self.candidate, self.source, {})
        )
