import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from .version_classifier import (
    VersionClassificationError,
    _parse,
    classify_group_versions,
)


class VersionClassifierTests(SimpleTestCase):
    def test_parser_requires_every_candidate_exactly_once(self):
        raw = json.dumps({
            "assignments": [
                {
                    "learning_object_id": 2,
                    "slot": "simplified",
                    "confidence": 0.91,
                    "reason": "Clearer wording.",
                }
            ]
        })
        self.assertEqual(_parse(raw, {2}, require_original=False)[2]["slot"], "SIMPLIFIED")
        with self.assertRaises(VersionClassificationError):
            _parse(raw, {2, 3}, require_original=False)

    def test_parser_maps_correct_length_rows_when_model_repeats_ids(self):
        raw = json.dumps({
            "assignments": [
                {
                    "learning_object_id": 999,
                    "slot": "ORIGINAL",
                    "confidence": 0.9,
                    "reason": "Balanced baseline.",
                },
                {
                    "learning_object_id": 999,
                    "slot": "EXTRA",
                    "confidence": 0.8,
                    "reason": "Equivalent alternative.",
                },
            ]
        })

        result = _parse(raw, [10, 20], require_original=True)

        self.assertEqual(result[10]["slot"], "ORIGINAL")
        self.assertEqual(result[20]["slot"], "EXTRA")

    @override_settings(
        CONTENT_VERSION_LLM_ENABLED=True,
        CONTENT_VERSION_LLM_MODEL="gemma3:4b",
        CONTENT_VERSION_LLM_TIMEOUT=30,
        OLLAMA_BASE_URL="http://localhost:11434",
        OLLAMA_KEEP_ALIVE="10m",
    )
    @patch("course.version_classifier.requests.post")
    def test_request_uses_structured_low_temperature_output(self, post):
        post.return_value.json.return_value = {
            "response": json.dumps({
                "assignments": [
                    {
                        "learning_object_id": 1,
                        "slot": "ORIGINAL",
                        "confidence": 0.92,
                        "reason": "Balanced baseline.",
                    },
                    {
                        "learning_object_id": 2,
                        "slot": "ELABORATED",
                        "confidence": 0.88,
                        "reason": "Contains a fuller explanation.",
                    },
                ]
            })
        }
        representative = SimpleNamespace(id=1, title="Solid", content="A solid keeps its shape.")
        candidate = SimpleNamespace(
            id=2,
            title="Solid",
            content="A solid keeps its shape because its particles remain closely packed.",
        )

        result = classify_group_versions([representative, candidate])

        self.assertEqual(result[2]["slot"], "ELABORATED")
        request_json = post.call_args.kwargs["json"]
        self.assertEqual(request_json["options"]["temperature"], 0.0)
        self.assertFalse(request_json["stream"])

    @override_settings(
        CONTENT_VERSION_LLM_ENABLED=True,
        CONTENT_VERSION_LLM_MODEL="gemma3:4b",
        CONTENT_VERSION_LLM_TIMEOUT=30,
        OLLAMA_BASE_URL="http://localhost:11434",
        OLLAMA_KEEP_ALIVE="10m",
    )
    @patch("course.version_classifier.requests.post")
    def test_invalid_first_response_is_retried_once(self, post):
        post.return_value.json.side_effect = [
            {"response": json.dumps({"assignments": []})},
            {"response": json.dumps({
                "assignments": [
                    {
                        "position": 1,
                        "slot": "ORIGINAL",
                        "confidence": 0.9,
                        "reason": "Balanced baseline.",
                    },
                ]
            })},
        ]
        member = SimpleNamespace(id=7, title="Matter", content="Matter has mass.")

        result = classify_group_versions([member])

        self.assertEqual(result[7]["slot"], "ORIGINAL")
        self.assertEqual(post.call_count, 2)

    @override_settings(
        CONTENT_VERSION_LLM_ENABLED=True,
        CONTENT_VERSION_LLM_MODEL="gemma3:4b",
        CONTENT_VERSION_LLM_TIMEOUT=30,
        OLLAMA_BASE_URL="http://localhost:11434",
        OLLAMA_KEEP_ALIVE="10m",
    )
    @patch("course.version_classifier.requests.post")
    def test_fixed_original_is_not_offered_as_a_choosable_slot(self, post):
        """A role the parser rejects must not be one the model may return.

        With the Normal bundle already chosen, ``ORIGINAL`` is invalid, so
        neither the schema nor the prompt may present it -- offering it is what
        made a concept fail classification on every retry.
        """
        post.return_value.json.return_value = {
            "response": json.dumps({
                "assignments": [
                    {
                        "position": 1,
                        "slot": "SIMPLIFIED",
                        "confidence": 0.9,
                        "reason": "Shorter sentences.",
                    },
                ]
            })
        }
        representative = SimpleNamespace(id=1, title="Solid", content="A solid keeps its shape.")
        candidate = SimpleNamespace(id=2, title="Solid", content="A solid has a fixed shape.")

        classify_group_versions([candidate], representative=representative)

        request_json = post.call_args.kwargs["json"]
        slots = request_json["format"]["properties"]["assignments"]["items"]["properties"]["slot"]
        self.assertEqual(slots["enum"], ["ELABORATED", "EXTRA", "SIMPLIFIED"])
        self.assertNotIn("- ORIGINAL:", request_json["prompt"])

    @override_settings(
        CONTENT_VERSION_LLM_ENABLED=True,
        CONTENT_VERSION_LLM_MODEL="gemma3:4b",
        CONTENT_VERSION_LLM_TIMEOUT=30,
        OLLAMA_BASE_URL="http://localhost:11434",
        OLLAMA_KEEP_ALIVE="10m",
    )
    @patch("course.version_classifier.requests.post")
    def test_retry_correction_names_the_rejected_slot(self, post):
        """A retry that repeats the first mistake wastes a whole model call."""
        post.return_value.json.side_effect = [
            {"response": json.dumps({
                "assignments": [
                    {
                        "position": 1,
                        "slot": "ORIGINAL",
                        "confidence": 0.9,
                        "reason": "Balanced baseline.",
                    },
                ]
            })},
            {"response": json.dumps({
                "assignments": [
                    {
                        "position": 1,
                        "slot": "EXTRA",
                        "confidence": 0.7,
                        "reason": "Equivalent wording.",
                    },
                ]
            })},
        ]
        representative = SimpleNamespace(id=1, title="Solid", content="A solid keeps its shape.")
        candidate = SimpleNamespace(id=2, title="Solid", content="A solid has a fixed shape.")

        result = classify_group_versions([candidate], representative=representative)

        self.assertEqual(result[2]["slot"], "EXTRA")
        # The correction has to say which slots are allowed. Repeating only the
        # count and position rules invited the same answer a second time.
        correction = post.call_args.kwargs["json"]["prompt"].rsplit("CANDIDATES TO CLASSIFY:", 1)[1]
        self.assertIn("Use only these slots: ELABORATED, EXTRA, SIMPLIFIED.", correction)
