"""Parsing regressions for the regex cleanup; no models or API calls."""

from django.test import SimpleTestCase

from lessons.services.question_workflow import (
    _following_choice_text,
    _is_choice_block,
    _is_question_line,
    _starts_new_question,
    detected_question_payloads,
    parse_question_structure,
    question_fingerprint,
)


class QuestionWorkflowParsingTests(SimpleTestCase):
    def test_separate_choice_lines_accept_existing_marker_formats(self):
        for text in ("A. Solid", "b) Liquid", "(C) Gas", "D: Plasma", "A - Ice"):
            with self.subTest(text=text):
                self.assertTrue(_is_choice_block(text))
        for text in ("", "Matter is a substance.", "A.", "E. Unsupported", "A. Ice\nNext question?"):
            with self.subTest(text=text):
                self.assertFalse(_is_choice_block(text))

    def test_lettered_choice_captures_still_resolve_answer_key(self):
        for choices in ("A. Solid\nB. Gas", "(A) Solid\n(B) Gas", "A: Solid B - Gas"):
            with self.subTest(choices=choices):
                result = parse_question_structure("Which state?\n" + choices, "Answer: B")
                self.assertEqual(result["prompt"], "Which state?")
                self.assertEqual(result["choices"], ["Solid", "Gas"])
                self.assertEqual(result["correct_answer"], "Gas")

    def test_numbered_questions_keep_existing_detection_rules(self):
        for text in ("1. Describe matter.", "2) Choose a state.", "3. What is matter?"):
            with self.subTest(text=text):
                self.assertTrue(_is_question_line(text))
                self.assertTrue(_starts_new_question(text))
        self.assertFalse(_is_question_line("4. A solid keeps its shape."))
        self.assertTrue(_is_question_line("4. A solid keeps its shape.", allow_numbered_statement=True))

    def test_supporting_choices_stop_before_the_next_question(self):
        blocks = [{"text": text} for text in (
            "Which state?", "A. Solid", "B. Gas", "Answer: B", "What is volume?",
        )]
        self.assertEqual(_following_choice_text(blocks, 0), "A. Solid\nB. Gas\nAnswer: B")

    def test_true_false_supporting_blocks_are_preserved(self):
        blocks = [{"text": text} for text in ("Does it flow?", "True", "False", "Answer: False")]
        self.assertEqual(_following_choice_text(blocks, 0), "True\nFalse\nAnswer: False")

    def test_headings_and_answer_lines_do_not_become_questions(self):
        result = detected_question_payloads([{
            "category": "assessment", "page": 2, "block_id": 7,
            "text": "Questions\n1. Which state?\nA. Solid\nB. Gas\nAnswer: B",
        }])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["choices"], ["Solid", "Gas"])
        self.assertEqual(result[0]["correct_answer"], "Gas")
        self.assertEqual(result[0]["source_block_id"], 7)

    def test_fingerprints_still_ignore_question_numbers_and_spacing(self):
        self.assertEqual(question_fingerprint("Question 12. What is MATTER?"),
                         question_fingerprint("what   is matter"))
