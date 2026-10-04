"""Whole pages that are not the lesson are recognised by structure, not words.

Measured on a DepEd lumped module (50 pages): the block classifier, judging one
line at a time, turned the credits page, an upside-down answer key, two pages
of references and the back cover into about 35 learning objects -- "Editor:
Rahny S. Pepito" read exactly like a "Label: text" definition. The page pass
removed all of them and none of the lesson; eight shorter PDFs on disk were
left unchanged.
"""

import fitz
from django.test import SimpleTestCase

from lessons.services.content_generator import build_section_learning_objects
from lessons.services.instructional_content_classifier import (
    classify_instructional_blocks,
    extract_pdf_text_blocks,
)
from lessons.services.page_roles import (
    BACK_MATTER,
    FRONT_MATTER,
    INVERTED,
    LESSON,
    REFERENCES,
    page_roles,
)

HEIGHT = 800.0
_ids = iter(range(1, 100000))


def block(page, text, *, top=200.0, inverted=False):
    return {
        "block_id": next(_ids), "page": page, "text": text, "line_count": 1,
        "is_bold": False, "font_size": 11.0, "bbox": (50.0, top, 500.0, top + 20),
        "page_width": 600.0, "page_height": HEIGHT, "is_figure_text": False,
        "is_inverted": inverted,
    }


def footer(page, number):
    return block(page, str(number), top=HEIGHT - 40)


def lesson_page(page, printed=None):
    blocks = [block(page, f"Solids keep their shape because their particles are packed closely on page {page}.")]
    if printed is not None:
        blocks.append(footer(page, printed))
    return blocks


def module(pages=20, front=3):
    """A module: unnumbered front pages, then pages numbered from 1."""
    blocks = []
    for page in range(1, pages + 1):
        if page <= front:
            blocks.append(block(page, "Editor: Rahny S. Pepito"))
        else:
            blocks.extend(lesson_page(page, page - front))
    return blocks


class PageRoleTests(SimpleTestCase):
    def test_pages_before_printed_page_one_are_front_matter(self):
        roles = page_roles(module())

        self.assertEqual([roles[page] for page in (1, 2, 3)], [FRONT_MATTER] * 3)
        self.assertEqual(roles[4], LESSON)

    def test_one_unnumbered_title_page_is_kept(self):
        """A handout's own title page may carry the lesson's first lines."""
        roles = page_roles(module(pages=10, front=1))

        self.assertEqual(roles[1], LESSON)

    def test_unnumbered_pages_after_the_numbering_are_the_back_cover(self):
        blocks = module(pages=20) + [block(21, "For inquiries or feedback, please write or call.")]

        self.assertEqual(page_roles(blocks)[21], BACK_MATTER)

    def test_an_unnumbered_closing_page_full_of_text_is_kept(self):
        """A lesson's last page may simply have lost its page number."""
        text = " ".join(["Liquids take the shape of their container and flow freely."] * 12)
        blocks = module(pages=20) + [block(21, text)]

        self.assertEqual(page_roles(blocks)[21], LESSON)

    def test_a_long_unnumbered_ending_is_not_called_a_back_cover(self):
        """Several unnumbered closing pages may be lesson pages that lost their numbers."""
        blocks = module(pages=20) + [block(page, "Liquids take the shape of their container.") for page in (21, 22, 23)]

        self.assertTrue(all(page_roles(blocks)[page] == LESSON for page in (21, 22, 23)))

    def test_an_upside_down_page_is_set_aside_anywhere(self):
        blocks = module(pages=20)
        blocks += [block(10, "1. C 6. C 2. D 7. B 3. A 8. A", top=300, inverted=True) for _ in range(3)]

        self.assertEqual(page_roles(blocks)[10], INVERTED)

    def test_a_closing_page_of_web_addresses_is_references(self):
        blocks = module(pages=20)
        blocks += [block(20, f"Author {n}. Accessed 2021. https://example.org/{n}", top=100 + n * 30) for n in range(4)]

        self.assertEqual(page_roles(blocks)[20], REFERENCES)

    def test_links_in_the_middle_of_a_lesson_are_not_references(self):
        blocks = module(pages=20)
        blocks += [block(8, f"Figure {n}. Source: https://example.org/{n}", top=100 + n * 30) for n in range(4)]

        self.assertEqual(page_roles(blocks)[8], LESSON)

    def test_a_pdf_without_numbering_is_left_alone(self):
        blocks = [block(page, "Editor: Rahny S. Pepito") for page in range(1, 6)]

        self.assertTrue(all(role == LESSON for role in page_roles(blocks).values()))

    def test_inconsistent_numbers_are_not_trusted(self):
        blocks = [*lesson_page(1, 7), *lesson_page(2, 3), *lesson_page(3, 12), *lesson_page(4, 1), *lesson_page(5, 9)]

        self.assertTrue(all(role == LESSON for role in page_roles(blocks).values()))

    def test_it_never_removes_most_of_a_document(self):
        """Signals that would drop most pages are misreading the PDF."""
        blocks = []
        for page in range(1, 11):
            blocks += [block(page, "Heat moves from warm air to cool air.", inverted=True)]
            blocks.append(footer(page, page))

        self.assertTrue(all(role == LESSON for role in page_roles(blocks).values()))


class PageRoleClassificationTests(SimpleTestCase):
    def test_credits_on_a_front_page_no_longer_become_learning_objects(self):
        blocks = module(pages=12)

        classified = classify_instructional_blocks(blocks)
        objects = build_section_learning_objects(classified, [])

        self.assertFalse([item for item in objects if item.get("title") == "Editor"])
        credits = [item for item in classified if item["page"] == 1]
        self.assertTrue(all(not item["include_in_narration"] for item in credits))

    def test_lesson_pages_keep_their_own_classification(self):
        blocks = module(pages=12)

        classified = classify_instructional_blocks(blocks)

        page_five = [item for item in classified if item["page"] == 5 and "Solids" in item["text"]]
        self.assertEqual(page_five[0]["page_role"], LESSON)
        self.assertNotEqual(page_five[0]["category"], "answer_key")


class InvertedTextExtractionTests(SimpleTestCase):
    def test_text_printed_upside_down_is_detected(self):
        document = fitz.open()
        page = document.new_page()
        page.insert_text((72, 100), "A solid keeps its shape.", fontsize=12)
        page.insert_text((400, 500), "1. C 6. C 2. D 7. B", fontsize=12, rotate=180)
        path = self._save(document)

        blocks = extract_pdf_text_blocks(path)

        by_text = {item["text"]: item["is_inverted"] for item in blocks}
        self.assertFalse(by_text["A solid keeps its shape."])
        self.assertTrue(by_text["1. C 6. C 2. D 7. B"])

    def _save(self, document):
        import os
        import tempfile

        handle, path = tempfile.mkstemp(suffix=".pdf")
        os.close(handle)
        document.save(path)
        document.close()
        self.addCleanup(os.remove, path)
        return path
