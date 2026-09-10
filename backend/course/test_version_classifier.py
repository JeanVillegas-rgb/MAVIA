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
