"""Focused checks for the optional Groq trial path."""

import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from config import groq_client
from config.groq_client import GroqRateLimitError, _strict_schema, generate
from course.variant_generator import _request_variants_once
from course.version_classifier import classify_group_versions
from question_generation.services import grounding, question_generator


class GroqClientTests(SimpleTestCase):
    def setUp(self):
        groq_client._ACTIVE_KEYS = ()
        groq_client._CURRENT_KEY_INDEX = 0
        groq_client._KEY_COOLDOWNS.clear()
        groq_client._KEY_REJECTIONS.clear()

    @override_settings(
        GROQ_API_KEY="test-key",
        GROQ_ADDITIONAL_API_KEYS=(),
        GROQ_BASE_URL="https://api.groq.com/openai/v1",
        GROQ_MAX_RATE_LIMIT_WAIT=120,
    )
    @patch("config.groq_client.time.sleep")
    @patch("config.groq_client.requests.post")
    def test_waits_for_groq_retry_after_then_retries_same_request(self, post, sleep):
        limited = SimpleNamespace(
            ok=False, status_code=429, headers={"retry-after": "5"},
            json=lambda: {"error": {"code": "rate_limit_exceeded"}},
        )
        accepted = SimpleNamespace(
            ok=True, status_code=200,
            json=lambda: {"choices": [{"message": {"content": "ready"}}]},
            raise_for_status=lambda: None,
        )
        post.side_effect = [limited, accepted]
        waits = []
        clock = [0.0]
        sleep.side_effect = lambda seconds: clock.__setitem__(0, clock[0] + seconds)

        with patch("config.groq_client.time.monotonic", side_effect=lambda: clock[0]):
            raw, _ = generate(
                "prompt", model="openai/gpt-oss-20b", timeout=30,
                on_rate_limit_wait=lambda seconds, retry: waits.append((seconds, retry)),
            )

        self.assertEqual(raw, "ready")
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args_list[0].kwargs["json"], post.call_args_list[1].kwargs["json"])
        sleep.assert_called_once_with(5.25)
        self.assertEqual(waits, [(5.25, 1)])

    @override_settings(
        GROQ_API_KEY="primary-key",
        GROQ_ADDITIONAL_API_KEYS=("backup-key",),
        GROQ_BASE_URL="https://api.groq.com/openai/v1",
        GROQ_MAX_RATE_LIMIT_WAIT=120,
    )
    @patch("config.groq_client.time.sleep")
    @patch("config.groq_client.requests.post")
    def test_uses_another_key_when_primary_is_rate_limited(self, post, sleep):
        limited = SimpleNamespace(
            ok=False, status_code=429, headers={"retry-after": "60"},
            json=lambda: {"error": {"code": "rate_limit_exceeded"}},
        )
        accepted = SimpleNamespace(
            ok=True, status_code=200,
            json=lambda: {"choices": [{"message": {"content": "ready"}}]},
            raise_for_status=lambda: None,
        )
        post.side_effect = [limited, accepted]

        raw, _ = generate("prompt", model="openai/gpt-oss-20b", timeout=30)

        self.assertEqual(raw, "ready")
        self.assertEqual(
            [call.kwargs["headers"]["Authorization"] for call in post.call_args_list],
            ["Bearer primary-key", "Bearer backup-key"],
        )
        sleep.assert_not_called()

    @override_settings(
        GROQ_API_KEY="primary-key",
        GROQ_ADDITIONAL_API_KEYS=("second-key", "third-key"),
        GROQ_BASE_URL="https://api.groq.com/openai/v1",
        GROQ_MAX_RATE_LIMIT_WAIT=120,
    )
    @patch("config.groq_client.time.sleep")
    @patch("config.groq_client.requests.post")
    def test_moves_through_three_keys_when_two_are_rate_limited(self, post, sleep):
        limited = SimpleNamespace(
            ok=False, status_code=429, headers={"retry-after": "60"},
            json=lambda: {"error": {"code": "rate_limit_exceeded"}},
        )
        accepted = SimpleNamespace(
            ok=True, status_code=200,
            json=lambda: {"choices": [{"message": {"content": "ready"}}]},
            raise_for_status=lambda: None,
        )
        post.side_effect = [limited, limited, accepted]

        raw, _ = generate("prompt", model="openai/gpt-oss-20b", timeout=30)

        self.assertEqual(raw, "ready")
        self.assertEqual(
            [call.kwargs["headers"]["Authorization"] for call in post.call_args_list],
            ["Bearer primary-key", "Bearer second-key", "Bearer third-key"],
        )
        sleep.assert_not_called()

    @override_settings(
        GROQ_API_KEY="test-key",
        GROQ_ADDITIONAL_API_KEYS=(),
        GROQ_BASE_URL="https://api.groq.com/openai/v1",
        GROQ_MAX_RATE_LIMIT_WAIT=1,
    )
    @patch("config.groq_client.time.sleep")
    @patch("config.groq_client.requests.post")
    def test_does_not_wait_past_configured_limit(self, post, sleep):
        post.return_value = SimpleNamespace(
            ok=False, status_code=429, headers={"retry-after": "60"},
            json=lambda: {"error": {"code": "rate_limit_exceeded", "message": "Try later"}},
        )
        with self.assertRaisesRegex(GroqRateLimitError, "1s wait limit"):
            generate("prompt", model="openai/gpt-oss-20b", timeout=30)
        post.assert_called_once()
        sleep.assert_not_called()

    @patch("question_generation.services.question_generator._ollama_generate")
    def test_question_generation_does_not_restart_exhausted_rate_wait(self, request):
        request.side_effect = GroqRateLimitError("Groq HTTP 429")
        errors = []
        self.assertEqual(
            question_generator.generate_questions(
                "Matter has mass.", "LOT", {"MCQ": 1},
                on_error=lambda attempt, reason: errors.append((attempt, reason)),
            ),
            [],
        )
        request.assert_called_once()
        self.assertEqual(errors, [(1, "Groq HTTP 429")])

    @override_settings(GROQ_API_KEY="test-key", GROQ_BASE_URL="https://api.groq.com/openai/v1")
    @patch("config.groq_client.requests.post")
    def test_retries_groq_json_validation_failure(self, post):
        rejected = type("Response", (), {
            "ok": False,
            "status_code": 400,
            "json": lambda self: {"error": {"code": "json_validate_failed"}},
        })()
        accepted = type("Response", (), {
            "ok": True,
            "status_code": 200,
            "json": lambda self: {"choices": [{"message": {"content": "{}"}}]},
            "raise_for_status": lambda self: None,
        })()
        post.side_effect = [rejected, accepted]
        raw, _metrics = generate("prompt", model="openai/gpt-oss-20b", timeout=30)
        self.assertEqual(raw, "{}")
        self.assertEqual(post.call_count, 2)

    @override_settings(GROQ_API_KEY="test-key", GROQ_ADDITIONAL_API_KEYS="", GROQ_BASE_URL="https://api.groq.com/openai/v1")
    @patch("config.groq_client.requests.post")
    def test_a_rejected_reply_can_be_checked_by_the_caller_instead(self, post):
        reply = '{"questions": [{"question": "Ice is a solid.", "format": "TF", "correct_answer": "True"}]}'
        rejected = type("Response", (), {
            "ok": False,
            "status_code": 400,
            "json": lambda self: {"error": {"code": "json_validate_failed", "failed_generation": reply}},
        })()
        post.side_effect = [rejected]

        raw, _metrics = generate(
            "prompt", model="openai/gpt-oss-20b", timeout=30, use_failed_generation=True,
        )

        self.assertEqual(raw, reply)
        self.assertEqual(post.call_count, 1)

    @override_settings(GROQ_API_KEY="test-key", GROQ_ADDITIONAL_API_KEYS="", GROQ_BASE_URL="https://api.groq.com/openai/v1")
    @patch("config.groq_client.requests.post")
    def test_without_opting_in_a_rejected_reply_is_still_retried(self, post):
        rejected = type("Response", (), {
            "ok": False,
            "status_code": 400,
            "json": lambda self: {"error": {"code": "json_validate_failed", "failed_generation": "{}"}},
        })()
        post.side_effect = [rejected] * 4

        with self.assertRaises(ValueError):
            generate("prompt", model="openai/gpt-oss-20b", timeout=30)
        self.assertEqual(post.call_count, 4)

    def test_nested_question_schema_is_closed_for_strict_mode(self):
        schema = _strict_schema(question_generator.build_response_schema({"MCQ": 1, "TF": 1}))
        self.assertFalse(schema["additionalProperties"])
        variants = schema["properties"]["questions"]["items"]["anyOf"]
        self.assertEqual(len(variants), 2)
        self.assertTrue(all(shape["additionalProperties"] is False for shape in variants))
        mcq = next(shape for shape in variants if "choices" in shape["properties"])
        self.assertFalse(mcq["properties"]["choices"]["additionalProperties"])

    def test_nullable_choices_are_closed_for_strict_mode(self):
        schema = _strict_schema(question_generator._groq_response_schema({"MCQ": 2, "TF": 1}))
        choices = schema["properties"]["questions"]["items"]["properties"]["choices"]
        self.assertEqual(choices["type"], ["object", "null"])
        self.assertFalse(choices["additionalProperties"])

    @override_settings(
        GROQ_API_KEY="test-key",
        GROQ_BASE_URL="https://api.groq.com/openai/v1",
    )
    @patch("config.groq_client.requests.post")
    def test_json_request_and_usage(self, post):
        post.return_value.json.return_value = {
            "choices": [{"message": {"content": '{"ok":true}'}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 4},
        }

        raw, metrics = generate(
            "Return a result", model="openai/gpt-oss-20b", timeout=30,
            schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
        )

        self.assertEqual(json.loads(raw), {"ok": True})
        self.assertEqual(metrics["prompt_tokens"], 12)
        self.assertEqual(metrics["output_tokens"], 4)
        self.assertEqual(post.call_args.args[0], "https://api.groq.com/openai/v1/chat/completions")
        response_format = post.call_args.kwargs["json"]["response_format"]
        self.assertEqual(response_format["type"], "json_schema")
        self.assertTrue(response_format["json_schema"]["strict"])
        self.assertFalse(response_format["json_schema"]["schema"]["additionalProperties"])

    @override_settings(GROQ_API_KEY="", GROQ_ADDITIONAL_API_KEYS="")
    def test_missing_key_stops_before_request(self):
        with patch("config.groq_client.requests.post") as post:
            with self.assertRaisesRegex(ValueError, "GROQ_API_KEY"):
                generate("prompt", model="openai/gpt-oss-20b", timeout=30)
            post.assert_not_called()


class GroqRoutingTests(SimpleTestCase):
    @override_settings(
        LLM_PROVIDER="groq",
        GROQ_VARIANT_MAX_TOKENS=1024,
        ADAPTIVE_VARIANT_TIMEOUT=30,
    )
    @patch("course.variant_generator.groq_generate")
    @patch("course.variant_generator.requests.post")
    def test_adaptive_versions_use_groq_completion_budget(self, post, groq_generate):
        groq_generate.return_value = (
            '{"simplified":"Matter has mass.","elaborated":"Matter has mass and takes up space."}',
            {},
        )
        item = SimpleNamespace(title="Matter", content="Matter has mass and takes up space.")
        _request_variants_once(item, "openai/gpt-oss-20b")
        self.assertEqual(groq_generate.call_args.kwargs["max_tokens"], 1024)
        post.assert_not_called()

    @override_settings(
        LLM_PROVIDER="groq",
        CONTENT_VERSION_LLM_ENABLED=True,
        CONTENT_VERSION_LLM_MODEL="openai/gpt-oss-20b",
        CONTENT_VERSION_LLM_TIMEOUT=30,
    )
    @patch("course.version_classifier.groq_generate")
    @patch("course.version_classifier.requests.post")
    def test_version_classification_skips_ollama(self, post, groq_generate):
        groq_generate.return_value = (
            json.dumps({"assignments": [{
                "position": 1,
                "slot": "SIMPLIFIED",
                "confidence": 0.9,
                "reason": "Complete definition",
            }]}),
            {},
        )
        standard = SimpleNamespace(id=1, title="Matter", content="Matter has mass.")
        item = SimpleNamespace(id=2, title="Matter", content="Matter has mass.")
        self.assertEqual(classify_group_versions([item], representative=standard)[2]["slot"], "SIMPLIFIED")
        post.assert_not_called()

    @override_settings(LLM_PROVIDER="groq", QUESTION_LLM_MODEL="openai/gpt-oss-20b")
    @patch("question_generation.services.question_generator.groq_generate")
    @patch("question_generation.services.question_generator.requests.post")
    def test_question_generation_skips_ollama(self, post, groq_generate):
        groq_generate.return_value = ('{"questions":[]}', {"prompt_tokens": 3})
        seen = []
        raw = question_generator._ollama_generate(
            "prompt", schema={"type": "object"}, on_metrics=seen.append,
        )
        self.assertEqual(raw, '{"questions":[]}')
        self.assertEqual(seen, [{"prompt_tokens": 3}])
        question_schema = groq_generate.call_args.kwargs["schema"]
        self.assertNotIn("anyOf", question_schema["properties"]["questions"]["items"])
        self.assertEqual(
            question_schema["properties"]["questions"]["items"]["properties"]["choices"]["type"],
            ["object", "null"],
        )
        post.assert_not_called()

    @override_settings(LLM_PROVIDER="groq", QUESTION_LLM_MODEL="openai/gpt-oss-20b")
    @patch("question_generation.services.question_generator.requests.post")
    def test_groq_has_no_local_model_warmup(self, post):
        self.assertEqual(question_generator.warm_question_model()["load_ms"], 0)
        post.assert_not_called()

    @override_settings(LLM_PROVIDER="groq", QUESTION_JUDGE_MODEL="openai/gpt-oss-20b")
    @patch("question_generation.services.grounding.groq_generate")
    @patch("question_generation.services.grounding.requests.post")
    def test_grounding_judge_skips_ollama(self, post, groq_generate):
        groq_generate.return_value = ('{"verdict":"supported"}', {})
        self.assertEqual(grounding._judge_call("prompt"), {"verdict": "supported"})
        post.assert_not_called()
