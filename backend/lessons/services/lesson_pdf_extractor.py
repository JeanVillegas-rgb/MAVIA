from pathlib import Path
from tempfile import mkdtemp
import re
import fitz as pymupdf


LIST_ITEM_START = re.compile(
    r"^\s*(?:[-*\u2022\u2023\u25E6\u2043\u2219\u25AA\u25CF]\s+|"
    r"\d+[.)]\s+|\(\d+\)\s+|[A-Za-z][.)]\s+|\([A-Za-z]\)\s+)"
)


def join_text_lines(text_lines):
    """Join visual wraps but retain authored list-item boundaries."""
    logical_lines = []
    for raw_line in text_lines:
        line = " ".join((raw_line or "").split())
        if not line:
            continue
        if LIST_ITEM_START.match(line) or not logical_lines:
            logical_lines.append(line)
        else:
            logical_lines[-1] = f"{logical_lines[-1]} {line}"
    return "\n".join(logical_lines)


def find_image_blocks(page_dict, page_number):
    images = []

    for block_index, pdf_block in enumerate(page_dict.get("blocks", [])):
        if pdf_block.get("type") == 1:
            images.append({
                "page": page_number,
                "block_index": block_index,
                "bbox": list(pdf_block["bbox"]),
            })

    return images


def save_image_previews(page, image_candidates):
    if not image_candidates:
        return []

    preview_dir = Path(mkdtemp(prefix="lesson_image_preview_"))
    saved_images = []

    for image in image_candidates:
        clip = pymupdf.Rect(image["bbox"])
        pixmap = page.get_pixmap(dpi=150, clip=clip, alpha=False)

        path = preview_dir / (
            f"page_{image['page']}_block_{image['block_index']}.png"
        )
        pixmap.save(str(path))

        saved_images.append({
            **image,
            "png_bytes": pixmap.tobytes("png"),
            "preview_path": str(path),
        })

    return saved_images
def find_vector_figures(page, page_number):
    captions = [
        (pymupdf.Rect(block[:4]), block[4].strip())
        for block in page.get_text("blocks")
        if block[6] == 0
        and re.match(
            r"^\s*(?:(?:figure|fig\.?)\s*\d+\b|diagram\s+description\s*:)",
            block[4],
            re.IGNORECASE,
        )
    ]
    if not captions:
        return []

    # Remove a large background drawing before grouping.
    drawings = [
        drawing
        for drawing in page.get_drawings()
        if drawing["rect"].get_area() < page.rect.get_area() * 0.5
    ]


    table_rects = [
        pymupdf.Rect(table.bbox)
        for table in page.find_tables(strategy="lines_strict").tables
    ]

    drawing_groups = (
        page.cluster_drawings(drawings=drawings)
        if drawings else []
    )
    drawing_groups.extend(
        pymupdf.Rect(image["bbox"])
        for image in page.get_image_info()
    )
    figures = []

    for caption, caption_text in captions:
        # Find drawing groups shortly above this caption, excluding tables.
        parts = [
            rect
            for rect in drawing_groups
            if not any(rect.intersects(table) for table in table_rects)
            # A caption's text box can overlap the drawing border slightly.
            and -3 <= caption.y0 - rect.y1 <= page.rect.height * 0.2
        ]
        if not parts:
            continue

        # Combine all parts into ONE figure boundary.
        combined = pymupdf.Rect(parts[0])
        for part in parts[1:]:
            combined |= part

        if re.match(r"^\s*diagram\s+description\s*:", caption_text, re.IGNORECASE):
            # These diagrams are self-contained drawing groups; do not crop
            # the explanatory paragraph above or the description below.
            clip = pymupdf.Rect(
                combined.x0 - 3, combined.y0 - 3,
                combined.x1 + 3, combined.y1,
            ) & page.rect
        else:
            # Numbered figures may have labels outside the drawing group.
            clip = pymupdf.Rect(
                combined.x0 - combined.width * 0.1,
                combined.y0 - 3,
                combined.x1 + combined.width * 0.1,
                max(combined.y1, caption.y0 - 2),
            ) & page.rect

        figures.append({
            "type": "figure",
            "page": page_number,
            "block_index": f"figure_{len(figures) + 1}",
            "bbox": list(clip),
            "caption": caption_text,
            "png_bytes": page.get_pixmap(
                dpi=150, clip=clip, alpha=False
            ).tobytes("png"),
        })

    return figures

def find_table_images(page, page_number):
    tables = []

    for table_number, table in enumerate(
        page.find_tables(strategy="lines_strict").tables, start=1
    ):
        bbox = pymupdf.Rect(table.bbox)
        if bbox.is_empty:
            continue

        tables.append({
            "type": "table",
            "page": page_number,
            "page_height": page.rect.height,
            "column_count": table.col_count,
            "block_index": f"table_{table_number}",
            "bbox": list(bbox),
            "png_bytes": page.get_pixmap(
                dpi=150, clip=bbox, alpha=False
            ).tobytes("png"),
        })

    return tables

def extract_lesson_pdf_blocks(pdf_path):
    document = pymupdf.open(pdf_path)
    extracted_blocks = []
    extracted_images = []

    try:
        for page_index, page in enumerate(document):
            page_number = page_index + 1
            page_dict = page.get_text("dict", sort=True)

            figure_images = find_vector_figures(page, page_number)
            image_candidates = find_image_blocks(page_dict, page_number)
            # A vector figure may contain a small raster component. Keep the
            # complete figure, not an extra image object for that component.
            image_candidates = [
                image for image in image_candidates
                if not any(
                    pymupdf.Rect(figure["bbox"]).contains(
                        pymupdf.Rect(image["bbox"])
                    )
                    for figure in figure_images
                )
            ]


            page_images = save_image_previews(page, image_candidates)
            extracted_images.extend(page_images)
            extracted_images.extend(figure_images)

            table_images = find_table_images(page, page_number)
            extracted_images.extend(table_images)


            for block_index, pdf_block in enumerate(
                page_dict.get("blocks", [])
            ):
                # PyMuPDF type 0 is a text block.
                if pdf_block.get("type") != 0:
                    continue

                text_lines = []
                leading_bold_parts = []
                collecting_leading_bold = True
                font_sizes = []
                characters_by_font_size = {}
                bold_character_count = 0
                total_character_count = 0

                for line in pdf_block.get("lines", []):
                    line_parts = []

                    for span in line.get("spans", []):
                        span_text = span.get("text", "")
                        font_name = span.get("font", "").casefold()

                        if not span_text:
                            continue

                        if font_name == "zapfdingbats" and span_text == "G":
                            span_text = "\u2022"
                        line_parts.append(span_text)

                        font_flags = int(span.get("flags", 0))
                        font_size = float(span.get("size", 0))
                        character_count = len(span_text.strip())

                        total_character_count += character_count

                        if font_size > 0:
                            font_sizes.append(font_size)

                            if character_count > 0:
                                characters_by_font_size[font_size] = (
                                    characters_by_font_size.get(font_size, 0)
                                    + character_count
                                )

                        is_bold_span = (
                            "bold" in font_name
                            or bool(font_flags & 16)
                        )

                        if is_bold_span:
                            bold_character_count += character_count

                        if collecting_leading_bold:
                            if is_bold_span:
                                leading_bold_parts.append(span_text)
                            elif span_text.strip():
                                collecting_leading_bold = False
                            elif leading_bold_parts:
                                leading_bold_parts.append(span_text)

                    # Put the spans together into one line.
                    line_text = "".join(line_parts).strip()

                    if line_text:
                        text_lines.append(line_text)

                # Join ordinary visual wraps, but keep each authored list item
                # on its own logical line for storage and UI rendering.
                text = join_text_lines(text_lines)

                if not text:
                    continue

                average_font_size = (
                    sum(font_sizes) / len(font_sizes)
                    if font_sizes
                    else None
                )

                dominant_font_size = (
                    max(
                        characters_by_font_size,
                        key=characters_by_font_size.get,
                    )
                    if characters_by_font_size
                    else None
                )

                is_bold = (
                    total_character_count > 0
                    and bold_character_count / total_character_count >= 0.5
                )

                bbox = pdf_block.get("bbox", (0, 0, 0, 0))

                extracted_block = {
                    "block_id": len(extracted_blocks),
                    "page": page_number,
                    "block_index": block_index,
                    "text": text,
                    "bbox": list(bbox),
                    "line_count": len(text_lines),
                    "font_size": average_font_size,
                    "dominant_font_size": dominant_font_size,
                    "is_bold": is_bold,
                    "leading_bold_text": (
                        " ".join("".join(leading_bold_parts).split()) or None
                    ),
                }

                extracted_blocks.append(extracted_block)

    finally:
        document.close()

    return extracted_blocks, extracted_images
