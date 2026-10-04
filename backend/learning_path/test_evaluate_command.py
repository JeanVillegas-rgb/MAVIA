"""The evaluation command's guard around the final check (v7 spec section 8)."""

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase


class FinalCheckGuardTests(SimpleTestCase):
    def test_a_final_check_topic_needs_the_flag(self):
        with self.assertRaisesMessage(CommandError, "--final-check"):
            call_command("evaluate_gold_paths", topics=["341"])

    def test_the_new_topics_are_locked_too(self):
        for topic in ("351", "353", "365"):
            with self.assertRaisesMessage(CommandError, "--final-check"):
                call_command("evaluate_gold_paths", topics=[topic])

    def test_the_default_topics_are_the_design_set(self):
        from learning_path.management.commands.evaluate_gold_paths import DESIGN_TOPICS

        self.assertEqual(DESIGN_TOPICS, ["340", "357"])
