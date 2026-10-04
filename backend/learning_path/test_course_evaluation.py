"""Course-level evaluation: shortlist hits and the final-check lock (course spec 2026-10-02, section 5)."""

from types import SimpleNamespace

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from .services.criteria import ACCEPTED, PENDING
from .services.gold import course_shortlist_hits


def concept(id, key):
    return SimpleNamespace(id=id, key=key)


class ShortlistHitTests(SimpleTestCase):
    def test_a_dependent_counts_when_any_key_prerequisite_is_offered(self):
        liquid, solid, solutions, air = concept(1, "liquid"), concept(2, "solid"), concept(3, "solutions"), concept(4, "air")
        data = {"required": [["liquid", "solutions"], ["solid", "air"]]}
        decisions = [
            {"prerequisite": liquid, "dependent": solutions, "verdict": PENDING},
            {"prerequisite": liquid, "dependent": air, "verdict": ACCEPTED},
        ]

        hits = course_shortlist_hits(data, [[liquid, solid], [solutions, air]], decisions)

        self.assertEqual(hits, {"dependents": 2, "hit": 1, "offered": 2})


class CourseFinalCheckLockTests(SimpleTestCase):
    def test_a_final_pair_needs_the_flag(self):
        with self.assertRaisesMessage(CommandError, "--final-check"):
            call_command("evaluate_course_paths", pairs=["351-353"])
