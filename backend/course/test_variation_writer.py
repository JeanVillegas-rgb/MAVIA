import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from .generate_variation import (
    VariationWriterError,
    generate_level,
    generate_versions,
    parse_result,
)


class VariationWriterTests(SimpleTestCase):
    def test_unchanged_standard_is_rejected(self):
        with self.assertRaises(VariationWriterError):
            parse_result(
                '{"content":"Matter has mass.","reason":"Simpler."}',
                "Matter has mass.",
            )

    @override_settings(
        ADAPTIVE_VARIANT_LLM_MODEL="test-model",
        ADAPTIVE_VARIANT_TIMEOUT=10,
    )
    @patch("course.generate_variation.groq_generate")
    def test_generates_each_mavia_slot_from_the_standard(self, generate):
        generate.side_effect = [
            (json.dumps({"content": "Matter is stuff.", "reason": "Clearer."}), {}),
            (json.dumps({"content": "Matter has mass and occupies space.", "reason": "Fuller."}), {}),
        ]

        result = generate_versions(
            SimpleNamespace(title="Matter", content="Matter has mass.")
        )

        self.assertEqual(result["SIMPLIFIED"], "Matter is stuff.")
        self.assertEqual(
            result["ELABORATED"], "Matter has mass and occupies space."
        )

    def test_rejects_unsupported_target(self):
        with self.assertRaises(ValueError):
            generate_level(
                concept_label="Matter",
                baseline_text="Matter has mass.",
                target_level="normal",
            )

    @override_settings(
        ADAPTIVE_VARIANT_LLM_MODEL="test-model",
        ADAPTIVE_VARIANT_TIMEOUT=10,
    )
    @patch("course.generate_variation.groq_generate")
    def test_shared_object_headings_are_not_repeated_in_generation_input(self, generate):
        from lessons.services.concept_bundles import bundle_text

        objects = [
            SimpleNamespace(title="Matter", section_title="Matter", content=content)
            for content in (
                "Matter has mass.",
                "1. Solid keeps its shape.",
                "2. Liquid flows.",
                "3. Gas fills its container.",
            )
        ]
        generate.return_value = (
            json.dumps({"content": "A clearer explanation.", "reason": "Clearer."}), {},
        )
        generate_level(
            concept_label="Matter",
            baseline_text=bundle_text(objects),
            target_level="simplified",
        )
        payload = json.loads(generate.call_args.args[0].split("\n", 1)[1])
        self.assertEqual(payload["concept"], "Matter")
        self.assertEqual(
            payload["normal_baseline"],
            "Matter has mass.\n1. Solid keeps its shape.\n2. Liquid flows.\n3. Gas fills its container.",
        )
        self.assertTrue(all(item.title == item.section_title == "Matter" for item in objects))
