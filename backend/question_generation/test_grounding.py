"""Tests for the corrective-RAG grounding gate.

Every case here is network-free: the retrieval index is built by hand and the
judge is patched. The stages are tested separately because they fail
separately.
"""

from unittest.mock import patch

from django.test import TestCase, override_settings

from lessons.models import (
    CourseGroup,
    LearningMaterial,
    LearningObject,
    OutlineNode,
)
from question_generation.models import GeneratedQuestion
from question_generation.services import grounding


SOURCE = (
    "Matter is anything that has mass and occupies space. The image shows "
    "three arrangements of particles. In the first, particles are tightly "
    "packed together in fixed positions, representing a solid. The second "
    "shows particles close together but able to move, illustrating a liquid "
    "state. Finally, the third depicts particles far apart and moving "
    "freely, representing a gas."
)


class GroundingTestCase(TestCase):
    def setUp(self):
        course = CourseGroup.objects.create(title="Science")
        self.node = OutlineNode.objects.create(
            course=course, title="States of Matter", order=0, depth=0,
        )
        self.material = LearningMaterial.objects.create(
            course=course, outline_node=self.node, title="Lesson 1",
            extracted_text=SOURCE, status="completed",
        )
        self.learning_object = LearningObject.objects.create(
            material=self.material, kind="text", title="Matter",
            content=SOURCE, order=0,
        )

    def question(self, text, choices, answer, fmt="MCQ"):
        return GeneratedQuestion.objects.create(
            node=self.learning_object, question_text=text, question_format=fmt,
            choices=choices, correct_answer=answer, status="draft",
            thinking_order="LOT",
        )

    def unsearchable_index(self):
        """A real index with retrieval deliberately unavailable."""
        index = grounding.build_index(self.node)
        index.vectors = None
        index.reason = "encoder disabled for this test"
        return index


class ChunkingTests(GroundingTestCase):
    def test_short_text_is_one_chunk(self):
        self.assertEqual(grounding._chunk_text("a b c"), ["a b c"])

    def test_empty_text_yields_nothing(self):
        self.assertEqual(grounding._chunk_text(""), [])

    def test_long_text_chunks_overlap(self):
        words = [f"w{n}" for n in range(200)]
        chunks = grounding._chunk_text(" ".join(words))
        self.assertGreater(len(chunks), 1)
        # A sentence cut across a boundary must still appear whole somewhere,
        # which is what the overlap buys.
        first, second = chunks[0].split(), chunks[1].split()
        self.assertTrue(set(first) & set(second))

    def test_index_covers_every_material(self):
        index = grounding.build_index(self.node)
        self.assertTrue(index.chunks)


class DegradationTests(GroundingTestCase):
    def test_unavailable_retrieval_is_reported_not_hidden(self):
        question = self.question("What is matter?", {"A": "mass", "B": "space"}, "A")
        result = grounding.verify(question, self.unsearchable_index())
        self.assertEqual(result["stage"], "retrieval_unavailable")
        self.assertEqual(result["verdict"], "unverified")
        self.assertIn("encoder disabled", result["reason"])

    def test_judge_failure_does_not_sink_the_run(self):
        index = grounding.build_index(self.node)
        index.vectors = [[1.0]] * len(index.chunks)
        question = self.question("What is matter?", {"A": "mass", "B": "space"}, "A")
        with patch.object(grounding, "judge", side_effect=RuntimeError("ollama down")), \
             patch.object(grounding.TopicIndex, "search",
                          return_value=[(0.9, index.chunks[0])]):
            result = grounding.verify(question, index)
        self.assertTrue(result["passed"])
        self.assertEqual(result["stage"], "judge_unavailable")
        self.assertEqual(result["verdict"], "unverified")

    def test_missing_material_text_yields_an_empty_index(self):
        LearningMaterial.objects.filter(pk=self.material.pk).update(extracted_text="")
        index = grounding.build_index(self.node)
        self.assertFalse(index.searchable)
        self.assertIn("no extracted PDF text", index.reason)


class JudgeStageTests(GroundingTestCase):
    def run_with_verdict(self, verdict, supported="", problem=""):
        index = grounding.build_index(self.node)
        index.vectors = [[1.0]] * len(index.chunks)
        question = self.question(
            "What is the state of particles far apart and moving freely?",
            {"A": "solid", "B": "gas"}, "A",
        )
        with patch.object(grounding, "judge", return_value=(verdict, supported, problem)), \
             patch.object(grounding.TopicIndex, "search",
                          return_value=[(0.9, index.chunks[0])]):
            return grounding.verify(question, index)

    def test_supported_passes(self):
        self.assertTrue(self.run_with_verdict("supported")["passed"])

    def test_question_wording_the_lesson_never_uses_is_left_to_the_judge(self):
        """Ordinary stems ("according to", "which item") reach the judge rather
        than being rejected for their words."""
        index = grounding.build_index(self.node)
        index.vectors = [[1.0]] * len(index.chunks)
        question = self.question(
            "According to the lesson, which item describes particles far apart?",
            {"A": "a gas", "B": "a solid"}, "A",
        )
        with patch.object(grounding, "judge", return_value=("supported", "", "")) as judge, \
             patch.object(grounding.TopicIndex, "search", return_value=[(0.9, index.chunks[0])]):
            result = grounding.verify(question, index)
        judge.assert_called_once()
        self.assertTrue(result["passed"])

    def test_contradicted_names_the_answer_the_source_supports(self):
        result = self.run_with_verdict("contradicted", supported="B) gas", problem="source says gas")
        self.assertFalse(result["passed"])
        self.assertIn("B) gas", result["reason"])

    def test_unsupported_is_rejected(self):
        """The gate is the specification: a question the source does not
        support does not reach a learner, even if the judge is only unsure."""
        self.assertFalse(self.run_with_verdict("unsupported")["passed"])

    def test_unknown_verdict_is_treated_as_unsupported(self):
        """A malformed judge reply must not become an accidental pass.

        Patched at the HTTP boundary rather than at ``judge``, because the
        normalisation under test lives inside ``judge`` itself.
        """
        question = self.question("Which state?", {"A": "solid", "B": "gas"}, "A")
        with patch.object(
            grounding, "_judge_call",
            return_value={"verdict": "probably fine", "supported_answer": "", "problem": ""},
        ):
            verdict, _supported, _problem = grounding.judge(question, [{
                "material_title": "Lesson 1", "text": SOURCE,
            }])
        self.assertEqual(verdict, "unsupported")

    def test_missing_verdict_is_treated_as_unsupported(self):
        question = self.question("Which state?", {"A": "solid", "B": "gas"}, "A")
        with patch.object(grounding, "_judge_call", return_value={}):
            verdict, _supported, _problem = grounding.judge(question, [{
                "material_title": "Lesson 1", "text": SOURCE,
            }])
        self.assertEqual(verdict, "unsupported")


class QueryRenderingTests(GroundingTestCase):
    def test_mcq_answer_is_written_out_not_left_as_a_letter(self):
        question = self.question("Which?", {"A": "solid", "B": "gas"}, "B")
        self.assertEqual(grounding.asserted_answer(question), "B) gas")
        self.assertIn("B) gas", grounding.question_query(question))

    def test_true_false_answer_renders_as_itself(self):
        question = self.question("A pencil is a solid.", None, "True", fmt="TF")
        self.assertEqual(grounding.asserted_answer(question), "True")

    def test_query_carries_every_choice(self):
        question = self.question("Which?", {"A": "solid", "B": "gas"}, "A")
        query = grounding.question_query(question)
        self.assertIn("solid", query)
        self.assertIn("gas", query)


class CorrectionNoteTests(TestCase):
    def test_empty_failures_produce_no_note(self):
        self.assertEqual(grounding.correction_note([]), "")

    def test_note_quotes_the_rejected_question_and_its_reason(self):
        note = grounding.correction_note([("Is plasma a state?", "uses wording absent")])
        self.assertIn("Is plasma a state?", note)
        self.assertIn("uses wording absent", note)

    def test_a_fact_rejection_keeps_the_facts_message(self):
        note = grounding.correction_note([("Which one is it?", "the source does not support the answer 'B'")])
        self.assertIn("Ask only about facts stated in the content", note)

    def test_note_is_capped_so_the_prompt_cannot_run_away(self):
        note = grounding.correction_note([(f"Q{n}", "bad") for n in range(20)])
        self.assertEqual(note.count("was rejected"), 6)
