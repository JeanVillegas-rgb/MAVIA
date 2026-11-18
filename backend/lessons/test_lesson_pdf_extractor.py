"""Extractor boundary tests without model downloads or OCR services."""

from contextlib import redirect_stdout
from io import StringIO
from types import ModuleType, SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from lessons.services.lesson_pdf_extractor import extract_lesson_pdf_blocks


class Picture(SimpleNamespace):
    pass


class Table(SimpleNamespace):
    pass


class ListItem(SimpleNamespace):
    pass


class ListGroup(SimpleNamespace):
    pass


def provenance(*pages):
    return [SimpleNamespace(page_no=page) for page in pages]


def text_item(text, *pages):
    return SimpleNamespace(text=text, label="text", prov=provenance(*pages), self_ref="#/texts/test")


class LessonPdfExtractorTests(TestCase):
    def extract(self, items):
        document = Mock()
        document.iterate_items.return_value = iter((item, 0) for item in items)
        converter = Mock()
        converter.convert.return_value.document = document
        modules = {}
        for name in (
            "docling", "docling.datamodel", "docling.datamodel.base_models",
            "docling.datamodel.pipeline_options", "docling.document_converter",
            "docling_core", "docling_core.types", "docling_core.types.doc",
        ):
            modules[name] = ModuleType(name)
        modules["docling.datamodel.base_models"].InputFormat = SimpleNamespace(PDF="pdf")
        options = Mock()
        modules["docling.datamodel.pipeline_options"].PdfPipelineOptions = options
        modules["docling.document_converter"].DocumentConverter = Mock(return_value=converter)
        modules["docling.document_converter"].PdfFormatOption = Mock()
        modules["docling_core.types.doc"].PictureItem = Picture
        modules["docling_core.types.doc"].TableItem = Table
        modules["docling_core.types.doc"].ListItem = ListItem
        modules["docling_core.types.doc"].ListGroup = ListGroup
        output = StringIO()
        with patch.dict("sys.modules", modules), redirect_stdout(output):
            result = extract_lesson_pdf_blocks("lesson.pdf")
        converter.convert.assert_called_once_with("lesson.pdf")
        self.assertEqual(output.getvalue(), "")
        options.assert_called_once_with(
            generate_picture_images=True, generate_page_images=True,
            do_table_structure=True, do_ocr=True,
        )
        return result

    def test_cleaning_preserves_original_and_skips_empty_items(self):
        blocks, visuals, tables = self.extract([
            text_item(None), text_item("  "), text_item("\uf06c "),
            text_item("  \uf06c Solids keep their shape.  ", 2),
            text_item("A. Solid", 2),
        ])
        self.assertEqual([block["text"] for block in blocks], [
            "Solids keep their shape.", "A. Solid",
        ])
        self.assertEqual(blocks[0]["original_text"], "  \uf06c Solids keep their shape.  ")
        self.assertEqual([block["block_id"] for block in blocks], [0, 1])
        self.assertEqual([block["document_order"] for block in blocks], [3, 4])
        self.assertEqual((visuals, tables), ([], []))

    def test_separates_types_and_retains_interleaved_order(self):
        image = Mock()
        image.save.side_effect = lambda buffer, **kwargs: buffer.write(b"png")
        caption = Mock()
        caption.cref = "#/texts/caption"
        caption.resolve.return_value.text = "Particle diagram"
        table_data = {"num_rows": 1, "num_cols": 1, "table_cells": []}
        table = Table(captions=[], prov=provenance(2, 3), data=Mock(), get_image=Mock(return_value=None))
        table.data.model_dump.return_value = table_data
        blocks, visuals, tables = self.extract([
            text_item("Matter", 1),
            Picture(get_image=Mock(return_value=image), captions=[caption], prov=provenance(1)),
            table,
            text_item("Summary", 3),
        ])
        self.assertEqual([block["document_order"] for block in blocks], [0, 3])
        self.assertEqual(visuals[0]["document_order"], 1)
        self.assertEqual(tables[0]["document_order"], 2)
        self.assertEqual(visuals[0]["before_block_index"], 1)
        self.assertEqual(tables[0]["before_block_index"], 1)
        self.assertEqual(visuals[0]["png_bytes"], b"png")
        self.assertEqual(visuals[0]["caption"], "Particle diagram")
        self.assertEqual(tables[0]["data"], table_data)
        self.assertEqual(tables[0]["pages"], [2, 3])
        table.data.model_dump.assert_called_once_with(mode="json")

    def test_retains_all_pages_and_compatible_first_page(self):
        blocks, _, _ = self.extract([text_item("Spanning text", 2, 3, 3), text_item("No provenance")])
        self.assertEqual(blocks[0]["pages"], [2, 3])
        self.assertEqual(blocks[0]["page"], 2)
        self.assertEqual(blocks[1]["pages"], [])
        self.assertEqual(blocks[1]["page"], 1)

    def test_picture_without_image_is_skipped_without_losing_order(self):
        blocks, visuals, _ = self.extract([
            Picture(get_image=Mock(return_value=None)), text_item("After picture", 1),
        ])
        self.assertEqual(visuals, [])
        self.assertEqual(blocks[0]["document_order"], 1)
