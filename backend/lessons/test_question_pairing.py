"""Answer-supported pairing, replacing the retired SBERT link-table tests."""
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch
from question_generation.services.pairing import pair_question

class QuestionPairingTests(TestCase):
    def setUp(self):
        self.question = SimpleNamespace(question_text="Do roots absorb water?", question_format="TF",
                                        choices=None, correct_answer="True")
        self.chunks = [{"concept": name, "object_id": index, "text": name + " passage."}
                       for index, name in enumerate(("Leaves", "Roots"), 1)]
        self.index = Mock(chunks=self.chunks)
        self.index.rank.return_value = [(0.9, self.chunks[0]), (0.8, self.chunks[1])]

    @patch("question_generation.services.pairing.judge")
    def test_answer_support_decides_not_highest_similarity(self, judge):
        judge.side_effect = [("unsupported", "", "Not in passage"), ("supported", "", "Roots absorb water")]
        result = pair_question(self.question, self.index)
        self.assertEqual(result["status"], "paired")
        self.assertEqual(result["concept"]["concept"], "Roots")
        self.assertEqual(judge.call_count, 2)

    @patch("question_generation.services.pairing.judge")
    def test_conflicting_answer_is_held(self, judge):
        judge.return_value = ("contradicted", "False", "The key is contradicted")
        result = pair_question(self.question, self.index)
        self.assertEqual(result["status"], "key_conflict")
        self.assertEqual(result["supported_answer"], "False")

    @patch("question_generation.services.pairing.judge")
    def test_missing_answer_does_not_call_checker(self, judge):
        self.question.correct_answer = ""
        self.assertEqual(pair_question(self.question, self.index)["status"], "no_answer")
        judge.assert_not_called()

    @patch("question_generation.services.pairing.judge", side_effect=RuntimeError("offline"))
    def test_checker_failure_never_auto_approves(self, judge):
        self.assertEqual(pair_question(self.question, self.index)["status"], "unverified")

    @patch("question_generation.services.pairing.judge")
    def test_unsupported_answer_stays_unmatched(self, judge):
        judge.return_value = ("unsupported", "", "Not supported")
        self.assertEqual(pair_question(self.question, self.index)["status"], "unmatched")

    @patch("question_generation.services.pairing.judge")
    def test_without_embedding_ranking_each_concept_can_still_be_checked(self, judge):
        self.index.rank.return_value = []
        judge.return_value = ("supported", "", "Supported")
        self.assertEqual(pair_question(self.question, self.index)["concept"], self.chunks[0])
