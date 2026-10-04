"""Decide which PDF pages are the lesson and which are around it.

The block classifier judges one block at a time, so it cannot tell that
"Editor: Rahny S. Pepito" sits on a credits page -- read alone, the line looks
like any "Label: text" definition, and it became a learning object called
"Editor". Measured on a DepEd lumped module: about 35 of its 116 learning
objects were credits, an upside-down answer key, references and the back
cover.

This pass looks at whole pages instead, using only the document's structure,
never a word list:

* **Printed page numbers.** Books number the lesson pages; the cover, credits
  and contents before printed page 1 are front matter, and unnumbered pages
  after the numbering stops are the back cover.
* **Text direction.** A page whose text is mostly upside down was turned over
  on purpose -- how printed modules hide their answer keys. Lesson text never
  is.
* **Link density near the end.** A closing page made largely of web
  addresses is a reference list.

Every signal only ever removes pages, and never most of a document: when the
signals disagree with that, the pass does nothing and the block classifier's
own decisions stand. A PDF without these features is left exactly as before.
"""

from collections import Counter, defaultdict
import re

LESSON = "lesson"
FRONT_MATTER = "front_matter"
BACK_MATTER = "back_matter"
INVERTED = "inverted"
REFERENCES = "references"

# Where a printed page number sits: the top or bottom strip of the page.
_MARGIN_FRACTION = 0.12
_PAGE_NUMBER = re.compile(r"\s*\d{1,4}\s*")
# A numbering is trusted only when this many pages agree on it.
_MIN_NUMBERED_PAGES = 3
_MIN_FRONT_MATTER_PAGES = 2
# Unnumbered pages after the numbering ends that still count as back cover.
_MAX_TRAILING_PAGES = 2
# A back cover is nearly empty -- a contact box, a logo line. An unnumbered
# closing page with more text than this may be lesson that lost its number.
_MAX_BACK_COVER_WORDS = 80
_LINK = re.compile(r"https?://|www\.|doi:", re.IGNORECASE)
_MIN_LINK_BLOCKS = 3
_MIN_LINK_SHARE = 0.25
# Reference lists close a document; only its last quarter is considered.
_REFERENCE_TAIL_FRACTION = 0.25
# Never remove more than this share of a document's pages.
_MAX_REMOVED_SHARE = 0.4

# The block category each page role hands to the builder, all of them ones it
# already leaves out of learning objects.
CATEGORY_FOR_ROLE = {
    FRONT_MATTER: ("document_metadata", "Front matter: comes before printed page 1."),
    BACK_MATTER: ("document_metadata", "Back cover: comes after the printed page numbering ends."),
    INVERTED: ("answer_key", "Page printed upside down, as answer keys are."),
    REFERENCES: ("reference", "Closing page made largely of web addresses."),
}


def _printed_number(block):
    height = block.get("page_height")
    bbox = block.get("bbox")
    text = block.get("text") or ""
    if not height or not bbox or not _PAGE_NUMBER.fullmatch(text):
        return None
    top, bottom = float(bbox[1]), float(bbox[3])
    in_margin = bottom <= height * _MARGIN_FRACTION or top >= height * (1 - _MARGIN_FRACTION)
    return int(text) if in_margin else None


def _numbering(blocks):
    """``(offset, numbered pages)`` for the document's page numbering, or ``None``.

    The offset is PDF page minus printed page. A real numbering has most of
    its numbered pages agreeing on one offset.
    """
    offsets = {}
    for block in blocks:
        number = _printed_number(block)
        if number is not None and block.get("page") is not None:
            offsets.setdefault(block["page"], block["page"] - number)
    if len(offsets) < _MIN_NUMBERED_PAGES:
        return None
    offset, support = Counter(offsets.values()).most_common(1)[0]
    if support < _MIN_NUMBERED_PAGES or support * 2 < len(offsets):
        return None
    return offset, {page for page, value in offsets.items() if value == offset}


def page_roles(blocks):
    """``{page: role}`` for every page with text; ``LESSON`` unless shown otherwise."""
    by_page = defaultdict(list)
    for block in blocks:
        if block.get("page") is not None:
            by_page[block["page"]].append(block)
    if not by_page:
        return {}
    pages = sorted(by_page)
    last_page = pages[-1]
    roles = {page: LESSON for page in pages}

    numbering = _numbering(blocks)
    if numbering:
        offset, numbered = numbering
        first_lesson_page = 1 + offset
        # One unnumbered opening page can be a handout's own title page with
        # the lesson's first lines on it; front matter is a cover and at least
        # one more page (credits, contents) before printed page 1.
        if first_lesson_page - pages[0] >= _MIN_FRONT_MATTER_PAGES:
            for page in pages:
                if page < first_lesson_page and page not in numbered:
                    roles[page] = FRONT_MATTER
        trailing = [page for page in pages if page > max(numbered)]
        if len(trailing) <= _MAX_TRAILING_PAGES:
            for page in trailing:
                words = sum(len((block.get("text") or "").split()) for block in by_page[page])
                if words <= _MAX_BACK_COVER_WORDS:
                    roles[page] = BACK_MATTER

    for page, page_blocks in by_page.items():
        words = [(len((block.get("text") or "").split()), block.get("is_inverted")) for block in page_blocks]
        inverted = sum(count for count, flag in words if flag)
        if inverted * 2 > sum(count for count, _ in words):
            roles[page] = INVERTED

    tail_start = last_page - max(1, round(len(pages) * _REFERENCE_TAIL_FRACTION))
    for page in pages:
        if page <= tail_start or roles[page] != LESSON:
            continue
        page_blocks = by_page[page]
        links = sum(bool(_LINK.search(block.get("text") or "")) for block in page_blocks)
        if links >= _MIN_LINK_BLOCKS and links >= len(page_blocks) * _MIN_LINK_SHARE:
            roles[page] = REFERENCES

    removed = sum(role != LESSON for role in roles.values())
    if removed > len(pages) * _MAX_REMOVED_SHARE:
        # More than a structural fringe: the signals are misreading this
        # document, and dropping most of it would lose the lesson.
        return {page: LESSON for page in pages}
    return roles


def non_lesson_pages(classified_blocks):
    """The pages this pass set aside, read back from the classified blocks."""
    return {
        block["page"] for block in classified_blocks
        if block.get("page_role") not in (None, LESSON) and block.get("page") is not None
    }


def apply_page_roles(blocks, classified_blocks):
    """Re-label every block on a non-lesson page with a category the builder skips."""
    roles = page_roles(blocks)
    if not roles:
        return classified_blocks
    result = []
    for block in classified_blocks:
        role = roles.get(block.get("page"), LESSON)
        if role == LESSON:
            result.append({**block, "page_role": LESSON})
            continue
        category, reason = CATEGORY_FOR_ROLE[role]
        result.append({
            **block,
            "page_role": role,
            "category": category,
            "reason": reason,
            "include_in_narration": False,
            "confidence": 1.0,
        })
    return result
