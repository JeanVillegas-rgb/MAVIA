"""Tests for the per-level generation and band-completeness rules."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from lessons.models import CourseGroup, LearningMaterial, LearningObject

from .models import GeneratedQuestion
from .services.pipeline import (
    LEVELS_BY_BAND,
    QUESTION_DISTRIBUTION,
    complete_node_ids,
    finalize_node_questions,
    is_node_complete,
    node_question_status,
)
from .services.question_generator import (
    GENERATION_LEVELS,
    LEVEL_FORMATS,
    _build_prompt,
)


class _StubClassifier:
    def __init__(self, by_text, default=("understand", "LOT", "Meaning")):
        self.by_text = by_text
        self.default = default

    def classify(self, question_text):
        bloom, order, category = self.by_text.get(question_text, self.default)
        return {"bloom_level": bloom, "thinking_order": order, "category": category}


class NodeFixtureMixin:
    def build_node(self):
        self.course = CourseGroup.objects.create(title="Science")
        self.material = LearningMaterial.objects.create(
            course=self.course,
            title="Matter",
            pdf_file="learning_materials/matter.pdf",
            status="completed",
        )
        self.node = LearningObject.objects.create(
            material=self.material,
            title="Matter",
            content="Matter is anything that has mass and occupies space.",
            order=0,
        )

    def _draft(self, text, question_format="MCQ"):
        return GeneratedQuestion.objects.create(
            node=self.node,
            question_text=text,
            question_format=question_format,
            choices={"A": "a", "B": "b", "C": "c", "D": "d"},
            correct_answer="A",
            status="draft",
        )

    def _fill(self, band, count, level=None):
        """Create `count` final questions in a band, for completeness tests."""
        level = level or LEVELS_BY_BAND[band][0]
        for index in range(count):
            GeneratedQuestion.objects.create(
                node=self.node,
                question_text=f"{band} {level} question {index}?",
                question_format="MCQ",
                choices={"A": "a", "B": "b", "C": "c", "D": "d"},
                correct_answer="A",
                bloom_level=level,
                thinking_order=band,
                category="Meaning",
                status="final",
            )


class PromptTests(TestCase):
    """Every deliverable level must have its own prompt — that is the whole
    point of per-level steering."""

    def test_every_generatable_level_builds_a_prompt(self):
        for level in GENERATION_LEVELS:
            for fmt in LEVEL_FORMATS[level]:
                prompt = _build_prompt("Matter has mass.", level, fmt, count=2)
                self.assertIn("Matter has mass.", prompt)
                self.assertIn(fmt, prompt)

    def test_create_level_has_no_prompt(self):
        """MAVIA never asks for a create-level question: MCQ/TF cannot grade one."""
        self.assertNotIn("create", GENERATION_LEVELS)
        with self.assertRaises(ValueError):
            _build_prompt("Matter has mass.", "create", "MCQ")

    def test_prompts_differ_between_levels(self):
        remember = _build_prompt("Matter has mass.", "remember", "MCQ")
        analyze = _build_prompt("Matter has mass.", "analyze", "MCQ")
        self.assertNotEqual(remember, analyze)

    def test_higher_levels_are_multiple_choice_only(self):
        """True/False cannot carry a genuine analyse or evaluate task."""
        for level in ("analyze", "evaluate"):
            self.assertEqual(LEVEL_FORMATS[level], ("MCQ",))

    def test_bands_cover_exactly_the_generatable_levels(self):
        covered = tuple(sorted(sum((list(v) for v in LEVELS_BY_BAND.values()), [])))
        self.assertEqual(covered, tuple(sorted(GENERATION_LEVELS)))


class BandFillTests(NodeFixtureMixin, TestCase):
    def setUp(self):
        self.build_node()

    def test_band_prefers_level_coverage_before_doubling_up(self):
        """Three remember drafts and one analyze-band spread: the LOT pool
        should take one of each available level before a second remember."""
        self._draft("Recall one?")
        self._draft("Recall two?")
        self._draft("Explain it?")
        self._draft("Use it here?")

        classifier = _StubClassifier({
            "Recall one?": ("remember", "LOT", "Facts and Information"),
            "Recall two?": ("remember", "LOT", "Facts and Information"),
            "Explain it?": ("understand", "LOT", "Meaning"),
            "Use it here?": ("apply", "LOT", "Skills"),
        })
        finalize_node_questions(self.node, classifier)

        levels = sorted(
            q.bloom_level for q in self.node.generated_questions.filter(status="final")
        )
        self.assertEqual(levels, ["apply", "remember", "understand"])

    def test_band_doubles_up_when_only_one_level_is_available(self):
        """The classifier is authoritative: if it only ever yields analyze,
        the HOT pool fills with analyze rather than coming up short."""
        for index in range(4):
            self._draft(f"Compare these {index}?")

        classifier = _StubClassifier({}, default=("analyze", "HOT", "Skills"))
        finalize_node_questions(self.node, classifier)

        stored = self.node.generated_questions.filter(status="final")
        self.assertEqual(stored.count(), QUESTION_DISTRIBUTION["HOT"]["count"])
        self.assertEqual({q.bloom_level for q in stored}, {"analyze"})

    def test_create_level_drafts_are_never_promoted(self):
        self._draft("Design an experiment about matter.")
        classifier = _StubClassifier(
            {"Design an experiment about matter.": ("create", None, "Outcome")}
        )
        finalize_node_questions(self.node, classifier)
        self.assertEqual(self.node.generated_questions.filter(status="final").count(), 0)


class CompletenessTests(NodeFixtureMixin, TestCase):
    def setUp(self):
        self.build_node()

    def test_node_with_both_pools_full_is_complete(self):
        self._fill("LOT", QUESTION_DISTRIBUTION["LOT"]["count"])
        self._fill("HOT", QUESTION_DISTRIBUTION["HOT"]["count"], level="analyze")
        self.assertTrue(is_node_complete(self.node))
        self.assertEqual(complete_node_ids([self.node]), {self.node.id})

    def test_node_missing_a_whole_band_is_incomplete(self):
        """A node with no HOT questions can never satisfy the checkpoint rule."""
        self._fill("LOT", QUESTION_DISTRIBUTION["LOT"]["count"])
        self.assertFalse(is_node_complete(self.node))
        self.assertEqual(complete_node_ids([self.node]), set())

    def test_short_band_is_incomplete_not_accepted(self):
        """One short question means no alternate to offer after a wrong answer."""
        self._fill("LOT", QUESTION_DISTRIBUTION["LOT"]["count"])
        self._fill("HOT", QUESTION_DISTRIBUTION["HOT"]["count"] - 1, level="analyze")
        status = node_question_status(self.node)
        self.assertFalse(status["is_complete"])
        self.assertEqual(status["bands"]["HOT"]["short"], 1)

    def test_draft_rows_do_not_count_toward_completeness(self):
        self._fill("LOT", QUESTION_DISTRIBUTION["LOT"]["count"])
        for index in range(QUESTION_DISTRIBUTION["HOT"]["count"]):
            self._draft(f"Unfinished draft {index}?")
        self.assertFalse(is_node_complete(self.node))


class TeacherAuthoredQuestionTests(NodeFixtureMixin, TestCase):
    def setUp(self):
        self.build_node()
        teacher = get_user_model().objects.create_user(
            username="teacher", password="pw", email="t@example.com", role="TEACHER"
        )
        self.client.force_login(teacher)
        self.url = f"/api/generation/nodes/{self.node.id}/questions/"

    def test_teacher_can_author_a_question_to_repair_a_short_pool(self):
        self._fill("LOT", QUESTION_DISTRIBUTION["LOT"]["count"])
        self._fill("HOT", QUESTION_DISTRIBUTION["HOT"]["count"] - 1, level="analyze")
        self.assertFalse(is_node_complete(self.node))

        response = self.client.post(
            self.url,
            {
                "question_text": "Which explanation best justifies why ice floats?",
                "question_format": "MCQ",
                "choices": {"A": "a", "B": "b", "C": "c", "D": "d"},
                "correct_answer": "B",
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            GeneratedQuestion.objects.filter(node=self.node, status="final").count(),
            QUESTION_DISTRIBUTION["LOT"]["count"] + QUESTION_DISTRIBUTION["HOT"]["count"],
        )

    def test_authored_question_is_labelled_by_the_classifier_not_the_request(self):
        response = self.client.post(
            self.url,
            {
                "question_text": "What is matter?",
                "question_format": "MCQ",
                "choices": {"A": "a", "B": "b", "C": "c", "D": "d"},
                "correct_answer": "A",
                "bloom_level": "evaluate",
                "thinking_order": "HOT",
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        question = GeneratedQuestion.objects.get(node=self.node, status="final")
        # Whatever the request asked for, the stored label came from the classifier.
        self.assertIn(question.bloom_level, GENERATION_LEVELS)
        self.assertEqual(
            question.thinking_order,
            "LOT" if question.bloom_level in LEVELS_BY_BAND["LOT"] else "HOT",
        )

    def test_mcq_without_choices_is_rejected(self):
        response = self.client.post(
            self.url,
            {"question_text": "What is matter?", "question_format": "MCQ",
             "correct_answer": "A"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_correct_answer_must_be_one_of_the_choices(self):
        response = self.client.post(
            self.url,
            {
                "question_text": "What is matter?",
                "question_format": "MCQ",
                "choices": {"A": "a", "B": "b"},
                "correct_answer": "D",
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_status_endpoint_reports_what_each_pool_still_needs(self):
        self._fill("LOT", 1)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["is_complete"])
        self.assertEqual(body["bands"]["LOT"]["have"], 1)
        self.assertEqual(
            body["bands"]["HOT"]["short"], QUESTION_DISTRIBUTION["HOT"]["count"]
        )

    def test_unknown_node_returns_404(self):
        response = self.client.get("/api/generation/nodes/999999/questions/")
        self.assertEqual(response.status_code, 404)
