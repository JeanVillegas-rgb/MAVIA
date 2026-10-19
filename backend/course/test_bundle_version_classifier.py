import json
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from .bundle_version_classifier import (
    BundleClassificationError,
    classify_bundle,
    parse_result,
)


class BundleVersionClassifierTests(SimpleTestCase):
    def test_levels_translate_to_mavia_slots(self):
        self.assertEqual(
            parse_result('{"level":"simplified","reason":"Clearer."}')["slot"],
            "SIMPLIFIED",
        )
        self.assertEqual(
            parse_result('{"level":"elaborated","reason":"More explanation."}')["slot"],
            "ELABORATED",
        )

    def test_normal_candidate_is_recorded_without_replacing_standard(self):
        result = parse_result('{"level":"normal","reason":"Comparable depth."}')
        self.assertEqual(result["slot"], "NORMAL")

    def test_invalid_level_is_rejected(self):
        with self.assertRaises(BundleClassificationError):
            parse_result('{"level":"advanced","reason":"Harder."}')

    @override_settings(
        CONTENT_VERSION_LLM_MODEL="test-model",
        CONTENT_VERSION_LLM_TIMEOUT=10,
    )
    @patch("course.bundle_version_classifier.groq_generate")
    def test_complete_pdf_bundles_are_sent_to_the_model(self, generate):
        generate.return_value = (
            json.dumps({"level": "simplified", "reason": "Uses clearer words."}),
            {},
        )

        result = classify_bundle(
            concept_label="Matter",
            baseline_text="Baseline definition and example.",
            candidate_text="Candidate definition and example.",
        )

        self.assertEqual(result["slot"], "SIMPLIFIED")
        prompt = generate.call_args.args[0]
        self.assertIn("Baseline definition and example.", prompt)
        self.assertIn("Candidate definition and example.", prompt)
