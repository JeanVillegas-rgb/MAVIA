"""Images never consume neighboring lesson text, regardless of its structure."""

from copy import deepcopy
import json
from unittest.mock import patch

import requests
from django.test import SimpleTestCase

from lessons.services.visual_describer import describe_image, describe_visual_objects


class VisualTextPreservationTests(SimpleTestCase):
    def drafts(self, text="An introduction. 1. First term - explanation. 2. Second term - explanation."):
        return [
            {"draft_id": "before", "type": "text", "content": "Earlier lesson text."},
            {"draft_id": "image", "type": "image", "caption": "Figure 1. Diagram.",
             "content": "Figure 1. Diagram.", "source_block_ids": [],
             "parts": [{"type": "image", "image_url": "/diagram.png"}]},
            {"draft_id": "after", "type": "text", "title": "Lesson",
             "content": text, "source_block_ids": [9]},
        ]

    @patch("lessons.services.visual_describer.describe_image")
    def test_even_a_model_selected_paragraph_is_not_moved_or_deleted(self, describe):
        drafts = self.drafts()
        original = deepcopy(drafts)
        describe.return_value = {
            "source_id": "after", "description": original[2]["content"],
            "generated_description": "The diagram shows two labeled parts.",
        }
        describe_visual_objects(drafts, {"/diagram.png": b"png"})
        self.assertEqual([draft["draft_id"] for draft in drafts], ["before", "image", "after"])
        self.assertEqual(drafts[0], original[0])
        self.assertEqual(drafts[2], original[2])
        self.assertEqual(drafts[1]["content"], "The diagram shows two labeled parts.")
        self.assertEqual(drafts[1]["source_block_ids"], [])
        provenance = drafts[1]["parts"][0]["description_selection"]
        self.assertIsNone(provenance["source_draft_id"])
        self.assertIsNone(provenance["pdf_source"])
        self.assertEqual(provenance["original_content"], "Figure 1. Diagram.")
        self.assertNotIn("candidates", describe.call_args.kwargs)
        self.assertEqual(describe.call_args.args, (b"png",))

    @patch("lessons.services.visual_describer.describe_image")
    def test_preservation_is_not_specific_to_flower_terms_or_numbered_text(self, describe):
        describe.return_value = {"source_id": None, "generated_description": "Independent description."}
        for text in ("An ordinary paragraph.", "Pollination: process steps.", "Solid - explanation."):
            with self.subTest(text=text):
                drafts = self.drafts(text)
                original = deepcopy(drafts[2])
                describe_visual_objects(drafts, {"/diagram.png": b"png"})
                self.assertEqual(drafts[2], original)

    @patch("lessons.services.visual_describer.describe_image")
    def test_tables_keep_structured_data_and_adjacent_text(self, describe):
        drafts = self.drafts()
        drafts[1]["type"] = "table"
        drafts[1]["parts"][0].update(type="table", data={"num_rows": 1})
        original = deepcopy(drafts[2])
        describe.return_value = {"generated_description": "The table compares two properties."}
        describe_visual_objects(drafts, {})
        self.assertEqual(drafts[2], original)
        self.assertEqual(describe.call_args.kwargs["table_data"], {"num_rows": 1})
        self.assertEqual(drafts[1]["parts"][0]["data"], {"num_rows": 1})

    @patch("lessons.services.visual_describer.describe_image")
    def test_empty_description_does_not_replace_visual_with_pdf_text(self, describe):
        drafts = self.drafts()
        original = deepcopy(drafts)
        describe.return_value = {"source_id": "after", "description": drafts[2]["content"], "generated_description": ""}
        describe_visual_objects(drafts, {})
        self.assertEqual(drafts, original)

    @patch("lessons.services.visual_describer.describe_image")
    def test_service_failure_preserves_all_drafts(self, describe):
        drafts = self.drafts()
        original = deepcopy(drafts)
        describe.side_effect = requests.RequestException("Unavailable")
        with self.assertRaises(requests.RequestException):
            describe_visual_objects(drafts, {})
        self.assertEqual(drafts, original)

    @patch("lessons.services.visual_describer.groq_generate")
    def test_legacy_candidate_argument_is_ignored_even_if_reply_selects_it(self, generate):
        generate.return_value = (json.dumps({
            "source_id": "paragraph", "missing_details": [],
            "generated_description": "Independent image explanation.",
        }), {})
        result = describe_image(b"png", candidates=[{"id": "paragraph", "text": "PRIVATE LESSON PARAGRAPH"}])
        self.assertEqual(result["description"], "Independent image explanation.")
        self.assertIsNone(result["source_id"])
        self.assertNotIn("PRIVATE LESSON PARAGRAPH", generate.call_args.args[0])
        self.assertEqual(generate.call_args.kwargs["schema"]["required"], ["generated_description"])
