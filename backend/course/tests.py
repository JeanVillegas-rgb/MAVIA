from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests
from django.test import SimpleTestCase

from .testing import without_measurements
from .variant_generator import (
    VariantGenerationError,
    _parse_response,
    _request_variants,
)


def _ollama_reply(text):
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"response": text}
    return response


class VariantRequestRetryTests(SimpleTestCase):
    GOOD_REPLY = (
        '{"simplified": "A solid keeps its shape.",'
        ' "elaborated": "A solid keeps a fixed shape and a fixed volume."}'
    )

    def setUp(self):
        without_measurements(self)
        self.learning_object = SimpleNamespace(
            title="Solid",
            content="A solid has a fixed shape and a fixed volume.",
        )

    @patch("course.variant_generator.requests.post")
    def test_unreadable_reply_is_retried(self, post):
        post.side_effect = [_ollama_reply(""), _ollama_reply(self.GOOD_REPLY)]

        variants = _request_variants(self.learning_object, "gemma3:4b")

        self.assertEqual(variants["SIMPLIFIED"], "A solid keeps its shape.")
        self.assertEqual(post.call_count, 2)

    @patch("course.variant_generator.requests.post")
    def test_gives_up_after_three_unreadable_replies(self, post):
        post.side_effect = [_ollama_reply("not json") for _ in range(3)]

        with self.assertRaisesMessage(VariantGenerationError, "valid JSON"):
            _request_variants(self.learning_object, "gemma3:4b")
        self.assertEqual(post.call_count, 3)

    @patch("course.variant_generator.requests.post")
    def test_unreachable_ollama_is_not_retried(self, post):
        post.side_effect = requests.Timeout("timed out")

        with self.assertRaisesMessage(VariantGenerationError, "gemma3:4b request failed"):
            _request_variants(self.learning_object, "gemma3:4b")
        self.assertEqual(post.call_count, 1)

    def test_rejects_unreasonably_long_elaboration(self):
        raw = '{"simplified":"A solid has fixed shape and volume.","elaborated":"' + (
            "unsupported extra wording " * 20
        ) + '"}'
        with self.assertRaises(VariantGenerationError):
            _parse_response(raw, source_word_count=10)


class GeneratedVersionCheckTests(SimpleTestCase):
    """BUG-002: a generated version is checked, rewritten with the reason, and
    the Standard text is kept for a level no attempt gets right.

    The check result is patched here, so these verify the retry and fallback
    code. The check itself is in GeneratedVersionMeasureTests.
    """

    REPLY = '{"simplified": "Simple text here.", "elaborated": "A much fuller explanation here now."}'

    def setUp(self):
        self.learning_object = SimpleNamespace(title="Solid", content="A solid has a fixed shape and volume.")

    def run_with(self, checks, replies=3):
        with patch("course.variant_generator.requests.post",
                   side_effect=[_ollama_reply(self.REPLY) for _ in range(replies)]) as post, \
                patch("course.variant_generator.check_generated_version", side_effect=checks):
            return _request_variants(self.learning_object, "gemma3:4b"), post

    def test_versions_that_pass_are_kept_after_one_call(self):
        result, post = self.run_with(lambda slot, source, text: [])

        self.assertEqual(result.fallback, {})
        self.assertEqual(result["SIMPLIFIED"], "Simple text here.")
        self.assertEqual(post.call_count, 1)

    def test_a_failing_version_is_written_again_with_the_reason(self):
        calls = {"n": 0}

        def check(slot, source, text):
            calls["n"] += 1
            return ["it is not easier to read"] if slot == "SIMPLIFIED" and calls["n"] == 1 else []

        result, post = self.run_with(check)

        self.assertEqual(post.call_count, 2)
        second_prompt = post.call_args_list[1].kwargs["json"]["prompt"]
        self.assertIn("YOUR PREVIOUS ANSWER WAS REJECTED", second_prompt)
        self.assertIn("simplified: it is not easier to read", second_prompt)
        self.assertEqual(result.fallback, {})

    def test_a_level_no_attempt_gets_right_keeps_the_standard_text(self):
        def check(slot, source, text):
            return ["it leaves out facts"] if slot == "ELABORATED" else []

        result, post = self.run_with(check)

        self.assertEqual(post.call_count, 3)
        self.assertEqual(result["ELABORATED"], "A solid has a fixed shape and volume.")
        self.assertEqual(list(result.fallback), ["ELABORATED"])
        self.assertEqual(result["SIMPLIFIED"], "Simple text here.")


class GeneratedVersionMeasureTests(SimpleTestCase):
    """What the check says about real generated versions (real encoder).

    The versions quoted are the ones BUGS.md recorded from the Solid, Liquid
    and Gas run.
    """

    def check(self, slot, source, version):
        from .variant_generator import check_generated_version

        return check_generated_version(slot, source, version)

    def test_a_simplified_version_harder_than_its_source_fails(self):
        problems = self.check(
            "SIMPLIFIED",
            "An ice cube keeps its shape whether it sits in a bowl or on a plate. A wooden block "
            "stays the same size and shape no matter where you put it.",
            "An ice cube maintains its form regardless of its location. Similarly, a wooden block "
            "retains its size and shape.",
        )

        self.assertTrue(any("not easier" in problem for problem in problems))

    def test_a_simplified_version_with_unfamiliar_new_words_fails(self):
        problems = self.check(
            "SIMPLIFIED",
            "Solids have a fixed shape and volume.",
            "Solids maintain a definite, rigid form.",
        )

        self.assertTrue(any("does not use" in problem for problem in problems))

    def test_outside_terms_in_an_elaborated_version_are_not_checked(self):
        """A known gap: a word list flagged ordinary words ("within",
        "movement") in nearly every elaboration, so it is not applied there.
        This BUGS.md version is therefore not caught."""
        problems = self.check(
            "ELABORATED",
            "Heating a liquid gives its particles enough energy to escape into the air, a process "
            "called evaporation.",
            "When a liquid is heated, the added energy increases the kinetic energy of its particles, "
            "enabling them to overcome the intermolecular forces holding them together, resulting in "
            "evaporation and a phase change to a gaseous state.",
        )

        self.assertFalse(any("does not use" in problem for problem in problems))

    def test_an_elaborated_version_no_fuller_than_its_source_fails(self):
        problems = self.check(
            "ELABORATED",
            "Solids have the least particle energy and movement; gases have the most.",
            "Solids possess the lowest particle energy and movement.",
        )

        self.assertTrue(any("not fuller" in problem for problem in problems))

    def test_a_good_simplified_version_passes(self):
        problems = self.check(
            "SIMPLIFIED",
            "Solids have a fixed shape and volume.",
            "A solid keeps its shape and takes up a fixed amount of space.",
        )

        self.assertEqual(problems, [])
