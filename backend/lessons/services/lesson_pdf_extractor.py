"""Lesson PDF extraction boundary awaiting the Docling adapter."""


class LessonExtractionPending(RuntimeError):
    """The old format-dependent extractor has been removed."""


def join_text_lines(_text_lines):
    """Keep legacy imports explicit until Docling supplies structured blocks."""
    raise LessonExtractionPending("Docling lesson extraction has not been implemented yet.")


def extract_lesson_pdf_blocks(pdf_path):
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from io import BytesIO
    from docling_core.types.doc import TableItem, PictureItem
    from pprint import pprint

    options = PdfPipelineOptions(
        generate_picture_images=True,
        do_table_structure=True,
    )
    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=options)
        }
    )
    document = converter.convert(str(pdf_path)).document


    blocks=[]
    tables =[]
    visuals =[]

    for document_order, (item, _) in enumerate(document.iterate_items()):

        if isinstance(item, PictureItem):
            image = item.get_image(document)
            if image is None:
                continue

            buffer = BytesIO()
            image.save(buffer, format="PNG")

            caption = (
                item.captions[0].resolve(document).text
                if item.captions else ""
            )

            visuals.append({
                "page": item.prov[0].page_no if item.prov else 1,
                "block_index": f"picture_{len(visuals)}",
                "before_block_index": len(blocks),
                "png_bytes": buffer.getvalue(),
                "caption": caption,
                "document_order": document_order,
            })
            continue

        if isinstance(item, TableItem):
            caption = "\n".join(
                ref.resolve(document).text
                for ref in item.captions
            )
            tables.append({
                "table_id": len(tables),
                "caption": caption,
                "page": item.prov[0].page_no if item.prov else 1,
                "before_block_index": len(blocks),
                "data": item.data.model_dump(mode="json"),
                "document_order": document_order,
            })
            continue
        text = getattr(item, "text", None)

        if not text or not text.strip():
            continue

        cleaned_text= text.strip()

        if cleaned_text.startswith("\uf06c"):
            cleaned_text=cleaned_text[1:].lstrip()

        if not cleaned_text:
            continue

        blocks.append({
            "block_id": len(blocks),
            "block_index": len(blocks),
            "page": item.prov[0].page_no if item.prov else 1,
            "original_text": text,
            "text": cleaned_text,
            "docling_label": str(item.label),
            "document_order": document_order,
        })
    print("\n=== TEXT BLOCKS ===")
    pprint(blocks, sort_dicts=False)

    print("\n=== TABLES ===")
    pprint(tables, sort_dicts=False)

    print("\n=== VISUALS ===")
    for visual in visuals:
        pprint(
            {
                **visual,
                "png_bytes": f"<PNG image: {len(visual['png_bytes'])} bytes>",
            },
            sort_dicts=False,
        )


    return blocks, visuals, tables

def preview_docling_pdf(pdf_path):
    from docling.document_converter import DocumentConverter

    document = DocumentConverter().convert(str(pdf_path)).document

    print("Pages:", len(document.pages))
    print("Tables:", len(document.tables))
    print("Pictures:", len(document.pictures))

    shown = 0
    for document_order, (item, _) in enumerate(document.iterate_items()):
        text = getattr(item, "text", None)
        if not text:
            continue

        page = item.prov[0].page_no if item.prov else "?"
        print(f"PAGE {page} | {item.label} | {text[:120]}")

        shown += 1
        if shown == 200:
            break