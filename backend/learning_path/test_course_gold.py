"""Course-level gold report (course spec section 7)."""

from django.test import SimpleTestCase

from .services.gold import course_gold_report
from .testing import concept


def decision(before, after, verdict, contradicts=False):
    return {"prerequisite": before, "dependent": after, "verdict": verdict,
            "evidence": {"contradicts_outline": contradicts}}


class CourseGoldReportTests(SimpleTestCase):
    def setUp(self):
        self.liquid = concept(1, "Liquid", "x", key="liquid")
        self.solid = concept(2, "Solid", "x", key="solid")
        self.evaporation = concept(3, "Evaporation", "x", key="evaporation")
        self.melting = concept(4, "Melting", "x", key="melting")
        self.topics = [[self.liquid, self.solid], [self.evaporation, self.melting]]
        self.data = {"required": [["liquid", "evaporation"], ["solid", "melting"]], "unrelated": False}

    def test_precision_and_reach(self):
        report = course_gold_report(self.data, self.topics, [
            decision(self.liquid, self.evaporation, "accepted"),
            decision(self.liquid, self.melting, "accepted"),
            decision(self.solid, self.melting, "pending"),
        ])

        self.assertEqual(report["accepted_precision"], 0.5)
        self.assertEqual((report["reachable_count"], report["required_count"]), (2, 2))

    def test_an_unrelated_pair_counts_every_accepted_link(self):
        report = course_gold_report({"required": [], "unrelated": True}, self.topics, [
            decision(self.liquid, self.evaporation, "accepted"),
        ])

        self.assertEqual(report["unrelated_accepted"], 1)

    def test_an_outline_flag_is_right_only_when_the_key_agrees(self):
        report = course_gold_report(self.data, self.topics, [
            decision(self.evaporation, self.liquid, "pending", contradicts=True),
        ])

        self.assertEqual(report["outline_flags"], {"right": 0, "wrong": 1})
