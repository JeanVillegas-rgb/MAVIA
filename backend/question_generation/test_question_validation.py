"""Structural validation of a generated question.

What is decidable from the question alone. Whether the answer is *correct*
needs the source text and belongs to the CRAG gate (services/grounding.py);
nothing here consults it.
"""

from django.test import SimpleTestCase

from question_generation.services.question_generator import _validate_question


class MultipleChoiceValidationTests(SimpleTestCase):
    def test_a_letter_present_in_the_choices_is_accepted(self):
        q = {"question": "Which state?", "correct_answer": "C",
             "choices": {"A": "solid", "B": "liquid", "C": "gas"}}
        self.assertTrue(_validate_question(q, "MCQ"))
        self.assertEqual(q["correct_answer"], "C")

    def test_an_answer_given_as_choice_text_is_recovered(self):
        """The model often answers with the option rather than its letter.
        This recovery already existed and must survive the rewrite."""
        q = {"question": "Which state?", "correct_answer": "gas",
             "choices": {"A": "solid", "B": "liquid", "C": "gas"}}
        self.assertTrue(_validate_question(q, "MCQ"))
        self.assertEqual(q["correct_answer"], "C")

    def test_a_letter_absent_from_the_choices_is_rejected(self):
        q = {"question": "Which state?", "correct_answer": "D",
             "choices": {"A": "solid", "B": "liquid", "C": "gas"}}
        self.assertFalse(_validate_question(q, "MCQ"))

    def test_repeated_options_are_rejected(self):
        """Two identical options mean the learner cannot be wrong, or cannot
        be right -- either way it is not a question."""
        q = {"question": "Which state?", "correct_answer": "A",
             "choices": {"A": "gas", "B": "gas", "C": "gas"}}
        self.assertFalse(_validate_question(q, "MCQ"))

    def test_a_single_option_is_rejected(self):
        q = {"question": "Which state?", "correct_answer": "A",
             "choices": {"A": "gas"}}
        self.assertFalse(_validate_question(q, "MCQ"))

    def test_a_blank_option_is_rejected(self):
        q = {"question": "Which state?", "correct_answer": "A",
             "choices": {"A": "gas", "B": "   ", "C": "solid"}}
        self.assertFalse(_validate_question(q, "MCQ"))

    def test_missing_choices_are_rejected(self):
        q = {"question": "Which state?", "correct_answer": "A"}
        self.assertFalse(_validate_question(q, "MCQ"))


class TrueFalseValidationTests(SimpleTestCase):
    def test_a_statement_is_accepted(self):
        q = {"question": "A pencil has a definite shape.", "correct_answer": "true"}
        self.assertTrue(_validate_question(q, "TF"))
        self.assertEqual(q["correct_answer"], "True")

    def test_a_wh_question_is_rejected(self):
        """Q108 on topic 276, verbatim: a true/false item whose stem asks an
        open question. There is no proposition for True to be true of."""
        q = {"question": "In the arrangement that shows particles close "
                         "together but able to move, what is the shape of "
                         "the particles?",
             "correct_answer": "True"}
        self.assertFalse(_validate_question(q, "TF"))

    def test_every_wh_opener_is_rejected(self):
        for opener in ("What", "Which", "How", "Why", "Who", "Where", "When"):
            with self.subTest(opener=opener):
                q = {"question": f"{opener} is the state of matter?",
                     "correct_answer": "True"}
                self.assertFalse(_validate_question(q, "TF"))

    def test_an_unpunctuated_open_question_is_rejected(self):
        """The two markers are independent. Every other wh case here also
        ends in "?", so without this the opener list could be deleted and
        the suite would stay green."""
        q = {"question": "What is the state of matter", "correct_answer": "True"}
        self.assertFalse(_validate_question(q, "TF"))

    def test_a_statement_merely_containing_a_wh_word_is_kept(self):
        """Only the opener decides. 'Water takes the shape of whatever
        container holds it' is a statement."""
        q = {"question": "Water takes the shape of whatever container holds it.",
             "correct_answer": "True"}
        self.assertTrue(_validate_question(q, "TF"))

    def test_a_non_boolean_answer_is_rejected(self):
        q = {"question": "A pencil has a definite shape.", "correct_answer": "A"}
        self.assertFalse(_validate_question(q, "TF"))


class SharedValidationTests(SimpleTestCase):
    def test_a_question_with_no_text_is_rejected(self):
        q = {"correct_answer": "True"}
        self.assertFalse(_validate_question(q, "TF"))

    def test_a_question_with_no_answer_is_rejected(self):
        q = {"question": "A pencil has a definite shape."}
        self.assertFalse(_validate_question(q, "TF"))
