"""Extract Docling text, pictures and structured tables from lesson PDFs."""

import logging
import warnings
from io import BytesIO


logger = logging.getLogger(__name__)


class LessonExtractionPending(RuntimeError):
    """A caller tried to use the retired line-based PDF extractor."""


def join_text_lines(_text_lines):
    """Deprecated compatibility stub; Docling already supplies text blocks."""
    warnings.warn(
        "join_text_lines() is retired; use extract_lesson_pdf_blocks() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    raise LessonExtractionPending(
        "Line-based extraction has been retired. "
        "Use extract_lesson_pdf_blocks()."
    )


def _page_metadata(item):
    """Retain every provenance page and the compatible first-page field."""
    pages = list(dict.fromkeys(prov.page_no for prov in item.prov))
    return {
        "page": pages[0] if pages else 1,
        "pages": pages,
    }
def _list_metadata(item, document):
    """Preserve source list markers and nested-list ancestry."""
    from docling_core.types.doc import ListGroup, ListItem

    if not isinstance(item, ListItem):
        return {}

    list_path = []
    parent = item.parent

    while parent is not None:
        node = parent.resolve(document)
        if isinstance(node, ListGroup):
            list_path.append(node.self_ref)
        parent = node.parent

    list_path.reverse()

    return {
        "list_marker": item.marker,
        "list_enumerated": item.enumerated,
        "list_path": list_path,
        "list_depth": max(0, len(list_path) - 1),
    }


def extract_lesson_pdf_blocks(pdf_path):
    """Return (blocks, visuals, tables) with Docling document-order markers."""
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling_core.types.doc import PictureItem, TableItem

    options = PdfPipelineOptions(
        generate_picture_images=True,
        generate_page_images=True,
        do_table_structure=True,
        do_ocr=True,
    )

    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_options=options,
            )
        }
    )

    document = converter.convert(str(pdf_path)).document

    blocks = []
    tables = []
    visuals = []
    active_heading = ""
    active_heading_ref = ""

    for document_order, (item, _) in enumerate(document.iterate_items()):
        if isinstance(item, PictureItem):
            image = item.get_image(document)

            if image is None:
                continue

            buffer = BytesIO()
            image.save(buffer, format="PNG")

            caption = "\n".join(
                ref.resolve(document).text.strip()
                for ref in item.captions
            )

            visuals.append({
                **_page_metadata(item),
                "block_index": f"picture_{len(visuals)}",
                "before_block_index": len(blocks),
                "png_bytes": buffer.getvalue(),
                "caption": caption,
                "caption_refs": [
                    ref.cref for ref in item.captions
                ],
                "section_title": active_heading,
                "heading_ref": active_heading_ref,
                "document_order": document_order,
            })
            continue

        if isinstance(item, TableItem):
            caption = "\n".join(
                ref.resolve(document).text.strip()
                for ref in item.captions
            )

            image = item.get_image(document)
            png_bytes = b""

            if image is not None:
                buffer = BytesIO()
                image.save(buffer, format="PNG")
                png_bytes = buffer.getvalue()

            tables.append({
                "table_id": len(tables),
                "caption": caption,
                "caption_refs": [
                    ref.cref for ref in item.captions
                ],
                "section_title": active_heading,
                "heading_ref": active_heading_ref,
                **_page_metadata(item),
                "before_block_index": len(blocks),
                "data": item.data.model_dump(mode="json"),
                "png_bytes": png_bytes,
                "document_order": document_order,
            })
            continue

        text = getattr(item, "text", None)

        if not text or not text.strip():
            continue

        cleaned_text = text.strip()

        if cleaned_text.startswith("\uf06c"):
            cleaned_text = cleaned_text[1:].lstrip()

        if not cleaned_text:
            continue

        label = str(item.label)

        if label in {"title", "section_header", "heading"}:
            active_heading = cleaned_text
            active_heading_ref = item.self_ref

        blocks.append({
            "block_id": len(blocks),
            "block_index": len(blocks),
            **_page_metadata(item),
            "original_text": text,
            "text": cleaned_text,
            "docling_label": label,
            **_list_metadata(item, document),
            "docling_ref": item.self_ref,
            "document_order": document_order,
        })

    logger.debug(
        "Docling extracted %d text blocks, %d pictures and %d tables.",
        len(blocks),
        len(visuals),
        len(tables),
    )

    return blocks, visuals, tables


def preview_docling_pdf(pdf_path):
    import json
    from pathlib import Path

    blocks, visuals, tables = extract_lesson_pdf_blocks(pdf_path)

    def encode_binary(value):
        if isinstance(value, (bytes, bytearray)):
            return f"<PNG: {len(value)} bytes>"
        raise TypeError(f"Cannot serialize {type(value).__name__}")

    output = json.dumps(
        {"blocks": blocks, "visuals": visuals, "tables": tables},
        indent=2,
        ensure_ascii=False,
        default=encode_binary,
    )

    path = Path("lesson_extraction_debug.json").resolve()
    path.write_text(output, encoding="utf-8")
    print(f"Saved: {path}")
    print(f"Blocks: {len(blocks)}, visuals: {len(visuals)}, tables: {len(tables)}")