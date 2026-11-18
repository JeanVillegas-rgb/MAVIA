from unittest import TestCase

from lessons.services.learning_object_builder import build_learning_objects, join_part_text


class ListFormattingTests(TestCase):
    def test_separate_text_blocks_do_not_run_together(self):
        parts = [
            {"text": "Introduction."},
            {"text": "Liquid description.", "docling_label": "list_item"},
            {"text": "3. Gas description.", "docling_label": "text"},
        ]
        self.assertEqual(
            join_part_text(parts),
            "Introduction.\nLiquid description.\n3. Gas description.",
        )

    def test_source_markers_are_not_replaced_or_duplicated(self):
        for marker in ("•", "◦", "-", "+", "3.", "(4)", "b)", "iv."):
            with self.subTest(marker=marker):
                part = {
                    "text": "An item.", "docling_label": "list_item",
                    "list_marker": marker,
                }
                self.assertEqual(join_part_text([part]), f"{marker} An item.")
                part["text"] = f"{marker} An item."
                self.assertEqual(join_part_text([part]), part["text"])

    def test_nested_list_keeps_its_indentation(self):
        self.assertEqual(join_part_text([{
            "text": "Nested item.", "docling_label": "list_item",
            "list_marker": "a)", "list_depth": 1,
        }]), "  a) Nested item.")

    def test_builder_passes_source_list_formatting_to_content(self):
        blocks = [
            {"block_id": 0, "page": 1, "text": "Properties", "docling_label": "section_header"},
            {"block_id": 1, "page": 1, "text": "Keeps its shape.", "docling_label": "list_item",
             "list_marker": "•", "list_depth": 0},
            {"block_id": 2, "page": 1, "text": "Nested detail.", "docling_label": "list_item",
             "list_marker": "iv.", "list_depth": 1},
        ]
        drafts = build_learning_objects(blocks)
        self.assertEqual(
            [draft["content"] for draft in drafts],
            ["• Keeps its shape.", "  iv. Nested detail."],
        )
        self.assertEqual([draft["section_title"] for draft in drafts], ["Properties", "Properties"])
        self.assertEqual([draft["source_block_ids"] for draft in drafts], [[1], [2]])
        self.assertEqual(blocks[1]["text"], "Keeps its shape.")

    def test_empty_blocks_and_plain_paragraphs_do_not_get_bullets(self):
        self.assertEqual(join_part_text([
            {"text": "First paragraph."}, {"text": "  "}, {"text": "Second paragraph."},
        ]), "First paragraph.\nSecond paragraph.")
