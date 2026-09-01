from django.test import TestCase

from course.models import CourseModule, LessonNode
from lessons.models import CourseGroup, LearningMaterial, LearningObject, OutlineNode
from question_generation.models import GeneratedQuestion

from .models import LearningState
from .services import AdaptiveScoringService, resolve_start


def _make_module(course, order):
    outline_node = OutlineNode.objects.create(course=course, title=f"Module {order}", order=order, depth=0)
    return CourseModule.objects.create(source=outline_node)


def _make_lesson_node(module, course, title):
    material = LearningMaterial.objects.create(course=course, title=title, pdf_file="materials/x.pdf")
    return LessonNode.objects.create(module=module, source=material)


def _make_chunk(lesson_node, order, title="Chunk"):
    return LearningObject.objects.create(
        material=lesson_node.source,
        kind=LearningObject.Kind.TEXT,
        title=title,
        content=f"{title} content",
        order=order,
    )


def _make_question(chunk, bloom_level, difficulty="easy", correct="A"):
    return GeneratedQuestion.objects.create(
        node=chunk,
        question_text=f"Question about {chunk.title} ({bloom_level})",
        question_format="MCQ",
        choices={"A": "right", "B": "wrong", "C": "wrong", "D": "wrong"},
        correct_answer=correct,
        bloom_level=bloom_level,
        difficulty=difficulty,
        status="final",
    )


class AdaptiveProgressionTests(TestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Science 7")
        self.module = _make_module(self.course, order=0)
        self.lesson_node = _make_lesson_node(self.module, self.course, "Matter")

    def _start_state(self, chunk, question):
        return LearningState.objects.create(
            learner_id="learner1",
            current_module=self.module,
            current_node=self.lesson_node,
            current_question=question,
            current_bloom=question.bloom_level,
        )

    def test_correct_answer_advances_to_next_tier_in_same_chunk(self):
        chunk = _make_chunk(self.lesson_node, order=0, title="Matter")
        q_remember = _make_question(chunk, "remember")
        q_understand = _make_question(chunk, "understand")
        state = self._start_state(chunk, q_remember)

        result = AdaptiveScoringService.evaluate(state, q_remember, "A", 5.0)

        self.assertTrue(result["is_correct"])
        self.assertFalse(result["chunk_changed"])
        self.assertFalse(result["node_changed"])
        self.assertEqual(result["next_question"], q_understand.id)
        self.assertEqual(result["next_bloom"], "understand")

    def test_correct_answer_at_last_tier_moves_to_next_chunk(self):
        chunk1 = _make_chunk(self.lesson_node, order=0, title="Matter")
        chunk2 = _make_chunk(self.lesson_node, order=1, title="Solid")
        q_analyze = _make_question(chunk1, "analyze", difficulty="medium")
        q_next_first = _make_question(chunk2, "remember")
        state = self._start_state(chunk1, q_analyze)

        result = AdaptiveScoringService.evaluate(state, q_analyze, "A", 5.0)

        self.assertTrue(result["chunk_changed"])
        self.assertEqual(result["next_chunk"], chunk2.id)
        self.assertEqual(result["next_question"], q_next_first.id)

    def test_chunk_missing_a_tier_skips_forward_to_next_chunk(self):
        """Regression test: a chunk that never got an apply/analyze-level
        question generated (a real content gap) must not strand the
        learner — advancing past its exhausted tiers should fall through
        to the next chunk instead of returning no question at all."""
        chunk1 = _make_chunk(self.lesson_node, order=0, title="Matter")
        chunk2 = _make_chunk(self.lesson_node, order=1, title="Solid")
        q_remember = _make_question(chunk1, "remember")
        _make_question(chunk1, "understand")  # chunk1 has NO apply/analyze question
        q_next_first = _make_question(chunk2, "remember")
        state = self._start_state(chunk1, q_remember)
        # answer the "understand" tier correctly next
        understand_q = GeneratedQuestion.objects.get(node=chunk1, bloom_level="understand")

        result = AdaptiveScoringService.evaluate(state, understand_q, "A", 5.0)

        self.assertTrue(result["is_correct"])
        self.assertTrue(result["chunk_changed"])
        self.assertEqual(result["next_chunk"], chunk2.id)
        self.assertEqual(result["next_question"], q_next_first.id)
        self.assertEqual(result["next_bloom"], "remember")

    def test_completes_course_when_no_more_chunks_or_lessons(self):
        chunk = _make_chunk(self.lesson_node, order=0, title="Matter")
        q_analyze = _make_question(chunk, "analyze", difficulty="medium")
        state = self._start_state(chunk, q_analyze)

        result = AdaptiveScoringService.evaluate(state, q_analyze, "A", 5.0)

        self.assertTrue(result["completed"])
        self.assertIsNone(result["next_question"])

    def test_correct_answer_moves_to_next_lesson_node_when_chunks_exhausted(self):
        chunk = _make_chunk(self.lesson_node, order=0, title="Matter")
        q_analyze = _make_question(chunk, "analyze", difficulty="medium")
        next_lesson = _make_lesson_node(self.module, self.course, "Changes of State")
        next_chunk = _make_chunk(next_lesson, order=0, title="Melting")
        q_next = _make_question(next_chunk, "remember")
        state = self._start_state(chunk, q_analyze)

        result = AdaptiveScoringService.evaluate(state, q_analyze, "A", 5.0)

        self.assertTrue(result["node_changed"])
        self.assertEqual(result["next_node"], next_lesson.id)
        self.assertEqual(result["next_question"], q_next.id)

    def test_wrong_answer_remediates_with_easier_unattempted_question(self):
        chunk = _make_chunk(self.lesson_node, order=0, title="Matter")
        q_medium = _make_question(chunk, "understand", difficulty="medium")
        q_easy = _make_question(chunk, "understand", difficulty="easy")
        state = self._start_state(chunk, q_medium)

        result = AdaptiveScoringService.evaluate(state, q_medium, "B", 5.0)

        self.assertFalse(result["is_correct"])
        self.assertFalse(result["chunk_changed"])
        self.assertEqual(result["next_question"], q_easy.id)
        self.assertLess(result["mastery"], 0.30)


class ResolveStartTests(TestCase):
    def test_resolve_start_picks_first_chunk_and_lowest_tier_question(self):
        course = CourseGroup.objects.create(title="Science 7")
        module = _make_module(course, order=0)
        lesson_node = _make_lesson_node(module, course, "Matter")
        chunk = _make_chunk(lesson_node, order=0, title="Matter")
        q_remember = _make_question(chunk, "remember")
        _make_question(chunk, "understand")

        resolved_chunk, question = resolve_start(lesson_node)

        self.assertEqual(resolved_chunk.id, chunk.id)
        self.assertEqual(question.id, q_remember.id)

    def test_resolve_start_returns_none_when_no_text_chunks(self):
        course = CourseGroup.objects.create(title="Science 7")
        module = _make_module(course, order=0)
        lesson_node = _make_lesson_node(module, course, "Empty Lesson")

        resolved_chunk, question = resolve_start(lesson_node)

        self.assertIsNone(resolved_chunk)
        self.assertIsNone(question)
