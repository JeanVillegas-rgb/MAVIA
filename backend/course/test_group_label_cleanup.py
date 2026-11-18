from django.test import SimpleTestCase

from .version_assignment import clean_group_label


class GroupLabelCleanupTests(SimpleTestCase):
    def test_numbered_labels_keep_meaningful_parentheses(self):
        for title in ("Stamen (Male Part)", "Pistil (Female Part)", "Petals and Sepals (Supporting Parts)"):
            with self.subTest(title=title):
                self.assertEqual(clean_group_label(f"1. {title}"), title)

    def test_square_brackets_and_outer_parentheses_are_preserved(self):
        for title in ("Flower [Male Part]", "[Flower parts]", "(Flower parts)"):
            with self.subTest(title=title):
                self.assertEqual(clean_group_label(title), title)

    def test_numbering_part_suffix_and_punctuation_cleanup_still_work(self):
        self.assertEqual(clean_group_label("Lesson 2: Stamen (Male Part) [Part 1 of 2]"), "Stamen (Male Part)")
        self.assertEqual(clean_group_label(" 1.  Stamen   (Male Part). "), "Stamen (Male Part)")
        self.assertEqual(clean_group_label(" 2) Matter: "), "Matter")

    def test_empty_label_and_plain_title_are_unchanged(self):
        self.assertEqual(clean_group_label(None), "")
        self.assertEqual(clean_group_label("Matter"), "Matter")
