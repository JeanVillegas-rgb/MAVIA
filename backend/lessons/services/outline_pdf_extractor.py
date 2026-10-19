"""Jean's ordered PDF-block extractor for course outlines."""

import fitz

from .topic_hierarchy import identify_outline_item


def _clean_lines(text):
    return [
        cleaned
        for line in (text or "").splitlines()
        if (cleaned := " ".join(line.split()))
    ]


def _rows_in_block(text):
    """Join wrapped text while preserving multiple numbered rows in one block."""
    rows = []
    for line in _clean_lines(text):
        if identify_outline_item(line) is not None:
            rows.append(line)
        elif rows:
            rows[-1] = f"{rows[-1]} {line}"
        else:
            rows.append(line)
    return rows


def _continues_previous(previous, current):
    """Recognize a wrapped outline title split into a neighboring PDF block."""
    if (
        previous["page"] != current["page"]
        or identify_outline_item(previous["text"]) is None
        or identify_outline_item(current["text"]) is not None
    ):
        return False
    px0, py0, _px1, py1 = previous["bbox"]
    cx0, cy0, _cx1, _cy1 = current["bbox"]
    previous_height = max(1.0, py1 - py0)
    return (
        abs(cx0 - px0) <= 12.0
        and 0 <= cy0 - py1 <= max(24.0, previous_height * 2.0)
    )


def rows_from_page_blocks(page_number, page_blocks):
    """Convert positioned PDF blocks into complete outline-row candidates."""
    rows = []
    for block_index, block in enumerate(page_blocks):
        x0, y0, x1, y1, text, *_ = block
        for line_index, text_row in enumerate(_rows_in_block(text)):
            row = {
                "block_id": len(rows),
                "page": page_number,
                "block_index": f"{block_index}.{line_index}",
                "text": text_row,
                "bbox": [x0, y0, x1, y1],
            }
            if rows and _continues_previous(rows[-1], row):
                rows[-1]["text"] = f"{rows[-1]['text']} {text_row}"
                rows[-1]["bbox"][2] = max(rows[-1]["bbox"][2], x1)
                rows[-1]["bbox"][3] = max(rows[-1]["bbox"][3], y1)
            else:
                rows.append(row)

    return rows

#1
def extract_pdf_blocks(pdf_path):
    document = fitz.open(pdf_path)
    extracted_blocks = []
    try:
        for page_index, page in enumerate(document):
            page_number = page_index + 1

            page_rows = rows_from_page_blocks(
                page_number,
                page.get_text("blocks", sort=True),
            )

            for row in page_rows:
                row["block_id"] = len(extracted_blocks)
                extracted_blocks.append(row)
    finally:
        document.close()
    return extracted_blocks
