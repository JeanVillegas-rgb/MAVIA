"""Tests for the corrective-RAG grounding gate.

Every case here is network-free: the retrieval index is built by hand and the
judge is patched. The stages are tested separately because they fail
separately -- the lexical stage caught the "plasma" answer key that the 4B
judge model waved through, and a test that only exercised the whole pipeline
would not have shown that.
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

    def lexical_index(self):
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
        self.assertIn("gas", index.vocabulary)


class LexicalStageTests(GroundingTestCase):
    def test_generic_question_wording_is_not_treated_as_new_science(self):
        question = self.question(
            "Which of the following best shows the content about particles?",
            {"A": "particles far apart", "B": "plasma"},
            "A",
        )
        self.assertEqual(grounding.ungrounded_terms(question, self.lexical_index()), [])

    def test_out_of_corpus_answer_is_rejected(self):
        """The exact failure this gate was written for: 'plasma' marked correct
        where the source says 'gas'. No model is consulted."""
        question = self.question(
            "What is the state of particles that are far apart and moving freely?",
            {"A": "solid", "B": "liquid", "C": "gas", "D": "plasma"},
            "D",
        )
        result = grounding.verify(question, self.lexical_index())
        self.assertFalse(result["passed"])
        self.assertEqual(result["stage"], "lexical")
        self.assertIn("plasma", result["novel_terms"])

    def test_grounded_question_survives_the_lexical_stage(self):
        question = self.question(
            "What is the state of particles that are far apart and moving freely?",
            {"A": "solid", "B": "liquid", "C": "gas"},
            "C",
        )
        result = grounding.verify(question, self.lexical_index())
        self.assertTrue(result["passed"])

    def test_novel_terms_scan_the_marked_answer_not_only_the_stem(self):
        """A stem can be impeccable while the answer invents a word."""
        question = self.question(
            "Which arrangement is shown first?",
            {"A": "solid", "B": "intermolecular forces"},
            "B",
        )
        self.assertIn("intermolecular", grounding.ungrounded_terms(question, self.lexical_index()))

    def test_a_distractor_may_use_words_the_lesson_never_says(self):
        """A wrong option is wrong on purpose, and wrong often means unfamiliar.

        Found live: the gate rejected a question whose stem and key were both
        straight from the source, because one distractor said "none". Scanning
        every option makes the gate punish a usable distractor.
        """
        question = self.question(
            "What is the arrangement of particles in a solid?",
            {"A": "tightly packed in fixed positions", "B": "none", "C": "indefinite"},
            "A",
        )
        self.assertEqual(grounding.ungrounded_terms(question, self.lexical_index()), [])

    def test_an_invented_key_is_still_caught_beside_a_grounded_distractor(self):
        """The exact Q107 shape: real options, fabricated one marked correct."""
        question = self.question(
            "What is the state of particles far apart and moving freely?",
            {"A": "solid", "B": "liquid", "C": "gas", "D": "plasma"},
            "D",
        )
        self.assertIn("plasma", grounding.ungrounded_terms(question, self.lexical_index()))

    @override_settings(QUESTION_VALIDATION_MAX_NOVEL_TERMS=5)
    def test_tolerance_is_configurable(self):
        question = self.question(
            "Is plasma a state?", {"A": "yes", "B": "no"}, "B",
        )
        result = grounding.verify(question, self.lexical_index())
        # Lexical now tolerates it; retrieval is off, so it reports as such
        # rather than claiming a verification that never happened.
        self.assertTrue(result["passed"])
        self.assertEqual(result["verdict"], "unverified")


class DegradationTests(GroundingTestCase):
    def test_unavailable_retrieval_is_reported_not_hidden(self):
        question = self.question("What is matter?", {"A": "mass", "B": "space"}, "A")
        result = grounding.verify(question, self.lexical_index())
        self.assertEqual(result["stage"], "lexical_only")
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

    def test_note_is_capped_so_the_prompt_cannot_run_away(self):
        note = grounding.correction_note([(f"Q{n}", "bad") for n in range(20)])
        self.assertEqual(note.count("was rejected"), 6)


class InflectionTests(TestCase):
    """The lexical stage must not reject a word the source uses in another form.

    Found live: the lesson says particles "depicts"; a generated question said
    "depicted"; exact matching rejected a question that was word-for-word
    grounded. Merging forms must not blunt the stage -- the invented words are
    re-asserted here alongside.
    """

    def assertSameWord(self, left, right):
        self.assertTrue(
            grounding._variants(left) & grounding._variants(right),
            f"{left!r} and {right!r} should count as one word",
        )

    def test_verb_inflections_meet(self):
        self.assertSameWord("depicted", "depicts")
        self.assertSameWord("moving", "move")
        self.assertSameWord("packed", "pack")
        self.assertSameWord("stopped", "stop")

    def test_plurals_meet(self):
        self.assertSameWord("arrangement", "arrangements")
        self.assertSameWord("states", "state")
        self.assertSameWord("gases", "gas")
        self.assertSameWord("properties", "property")

    def test_short_words_do_not_erode(self):
        """'gas' must never become 'ga' and collide with unrelated words."""
        self.assertEqual(grounding._variants("gas"), {"gas"})
        self.assertEqual(grounding._variants("ice"), {"ice"})

    def test_invented_words_still_stand_alone(self):
        """Merging forms must not give a fabricated term something to match."""
        for word in ("plasma", "temperature", "intermolecular", "pressure"):
            self.assertEqual(
                grounding._variants(word), {word},
                f"{word!r} should have no inflected form to hide behind",
            )


class InflectionAgainstCorpusTests(GroundingTestCase):
    def test_source_inflection_grounds_the_question(self):
        """SOURCE says 'depicts'; the question says 'depicted'."""
        question = self.question(
            "What is the state depicted by particles far apart and moving freely?",
            {"A": "solid", "B": "gas"}, "B",
        )
        self.assertEqual(grounding.ungrounded_terms(question, self.lexical_index()), [])

    def test_plural_in_source_grounds_a_singular_question(self):
        """SOURCE says 'arrangements'; the question says 'arrangement'."""
        question = self.question(
            "Which arrangement shows particles in fixed positions?",
            {"A": "solid", "B": "liquid"}, "A",
        )
        self.assertEqual(grounding.ungrounded_terms(question, self.lexical_index()), [])

    def test_invented_word_is_still_rejected_after_folding(self):
        question = self.question(
            "Which state is shown?", {"A": "gas", "B": "plasma"}, "B",
        )
        self.assertIn("plasma", grounding.ungrounded_terms(question, self.lexical_index()))
