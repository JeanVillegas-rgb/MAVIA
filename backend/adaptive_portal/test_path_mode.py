"""Learning-path mode: prerequisite-ordered progression and remediation.

See the module docstring on ``services.py`` and ``learning_path/HANDOFF.md``.
Legacy (flat, ``lessons.Question``) mode is covered by ``tests.py``; these
tests build a real published path (the same fixture shape as
``learning_path/tests.py::PublishedPathTests``) and drive it through the
public API, same as the legacy engine tests do.
"""

from django.utils import timezone
from rest_framework.test import APITestCase

from lessons.models import CourseGroup, LearningMaterial, LearningObject, LearningObjectGroup, OutlineNode
from learning_path.models import ConceptPrerequisite, LearningPathStep
from learning_path.services import get_published_path
from question_generation.models import GeneratedQuestion
from user.models import User

from .models import Enrollment, LearningState, StudentResponse
from .services import (
    MAX_STEP_QUESTION_ATTEMPTS,
    resolve_learning_start,
)


def _published_topic(*, with_prerequisite=True):
    """A course with one module and one topic, a two-concept published path
    (Matter -> Solid, "Matter" a prerequisite of "Solid" when requested), one
    final question per concept."""
    course = CourseGroup.objects.create(title="Science 7")
    module = OutlineNode.objects.create(course=course, title="Module 1", order=0, depth=0)
    topic = OutlineNode.objects.create(
        course=course, parent=module, title="States of Matter", order=0, depth=1, published=True
    )
    material = LearningMaterial.objects.create(
        course=course, outline_node=topic, title="Lesson one",
        generated_json={"learning_objects_confirmed": True},
    )
    groups, objects, questions = {}, {}, {}
    for order, (title, correct) in enumerate([("Matter", "True"), ("Solid", "A")]):
        group = LearningObjectGroup.objects.create(outline_node=topic, label=title)
        obj = LearningObject.objects.create(
            material=material, group=group, title=title, content=f"{title} is taught here.", order=order,
        )
        groups[title] = group
        objects[title] = obj

    now = timezone.now()
    LearningPathStep.objects.create(outline_node=topic, concept=groups["Matter"], position=1, depth=0, published_at=now)
    LearningPathStep.objects.create(outline_node=topic, concept=groups["Solid"], position=2, depth=1, published_at=now)
    if with_prerequisite:
        ConceptPrerequisite.objects.create(
            outline_node=topic, prerequisite=groups["Matter"], dependent=groups["Solid"],
            status="accepted", source="derived",
        )

    questions["Matter"] = GeneratedQuestion.objects.create(
        node=objects["Matter"], question_text="Does matter take up space?",
        question_format="TF", correct_answer="True", explanation="Matter has volume.",
        bloom_level="remember", thinking_order="LOT", difficulty="easy", status="final",
    )
    questions["Solid"] = GeneratedQuestion.objects.create(
        node=objects["Solid"], question_text="Which keeps a fixed shape?",
        question_format="MCQ", choices={"A": "Solid", "B": "Liquid"}, correct_answer="A",
        explanation="A solid's particles are locked in place.",
        bloom_level="remember", thinking_order="LOT", difficulty="easy", status="final",
    )
    return course, module, topic, groups, questions


class ResolveLearningStartTests(APITestCase):
    def test_prefers_the_published_path_over_the_flat_walk(self):
        course, module, topic, groups, questions = _published_topic()

        start = resolve_learning_start(course)

        self.assertEqual(start["mode"], "path")
        self.assertEqual(start["node"], topic)
        self.assertEqual(start["step"]["position"], 1)
        self.assertEqual(start["question"]["id"], questions["Matter"].id)

    def test_falls_back_to_legacy_walk_without_a_published_path(self):
        from .tests import _course_with_content

        course, module, topics = _course_with_content()

        start = resolve_learning_start(course)

        self.assertEqual(start["mode"], "legacy")
        self.assertEqual(start["question"], topics[0][2][0])


class PathModeApiTests(APITestCase):
    def setUp(self):
        self.student = User.objects.create_user(
            username="stu", password="pw", role=User.Role.STUDENT, is_verified=True
        )
        self.course, self.module, self.topic, self.groups, self.questions = _published_topic()
        Enrollment.objects.create(student=self.student, course=self.course)
        self.client.force_authenticate(self.student)

    def _start(self):
        return self.client.post("/api/adaptive-portal/start/", {"course_id": self.course.id})

    def _submit(self, state_id, question_id, answer):
        return self.client.post(
            "/api/adaptive-portal/submit-response/",
            {"learning_state_id": state_id, "question_id": question_id, "selected_answer": answer},
        )

    def test_start_enters_path_mode_on_the_first_step(self):
        start = self._start()

        self.assertEqual(start.status_code, 201)
        state = start.data["learning_state"]
        self.assertEqual(state["current_step_position"], 1)
        self.assertEqual(state["current_generated_question"], self.questions["Matter"].id)
        self.assertIsNone(state["current_question"])
        self.assertEqual(start.data["current_step"]["concept_id"], self.groups["Matter"].id)
        # Answers never reach the student.
        self.assertNotIn("correct_answer", start.data["current_step"]["questions"][0])

    def test_correct_answer_advances_to_the_next_step(self):
        start = self._start()
        state_id = start.data["learning_state"]["id"]

        result = self._submit(state_id, self.questions["Matter"].id, "True")

        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.data["is_correct"])
        self.assertEqual(result.data["next_step_position"], 2)
        self.assertEqual(result.data["next_question"], self.questions["Solid"].id)
        self.assertFalse(result.data["completed"])

    def test_finishing_every_step_completes_the_course(self):
        start = self._start()
        state_id = start.data["learning_state"]["id"]
        self._submit(state_id, self.questions["Matter"].id, "True")

        result = self._submit(state_id, self.questions["Solid"].id, "A")

        self.assertTrue(result.data["is_correct"])
        self.assertTrue(result.data["completed"])
        self.assertIsNone(result.data["next_question"])

    def test_repeated_failure_detours_through_the_nearest_prerequisite(self):
        start = self._start()
        state_id = start.data["learning_state"]["id"]
        self._submit(state_id, self.questions["Matter"].id, "True")  # -> Solid

        result = None
        for _ in range(MAX_STEP_QUESTION_ATTEMPTS):
            result = self._submit(state_id, self.questions["Solid"].id, "B")  # wrong every time

        self.assertFalse(result.data["is_correct"])
        # Sent back to the prerequisite step ("Matter"), remembering where to return.
        self.assertEqual(result.data["next_step_position"], 1)
        self.assertEqual(result.data["remediation_target_position"], 2)
        self.assertEqual(result.data["next_question"], self.questions["Matter"].id)

        state = LearningState.objects.get(pk=state_id)
        self.assertEqual(state.current_question_attempts, 0)

    def test_clearing_the_detour_resumes_the_step_that_was_struggled_on(self):
        start = self._start()
        state_id = start.data["learning_state"]["id"]
        self._submit(state_id, self.questions["Matter"].id, "True")  # -> Solid
        for _ in range(MAX_STEP_QUESTION_ATTEMPTS):
            self._submit(state_id, self.questions["Solid"].id, "B")  # -> detoured to Matter

        result = self._submit(state_id, self.questions["Matter"].id, "True")  # clears the detour

        self.assertTrue(result.data["is_correct"])
        self.assertIsNone(result.data["remediation_target_position"])
        self.assertEqual(result.data["next_step_position"], 2)
        self.assertEqual(result.data["next_question"], self.questions["Solid"].id)

    def test_no_prerequisite_escalates_the_variant_instead_of_detouring(self):
        course, module, topic, groups, questions = _published_topic(with_prerequisite=False)
        Enrollment.objects.create(student=self.student, course=course)
        start = self.client.post("/api/adaptive-portal/start/", {"course_id": course.id})
        state_id = start.data["learning_state"]["id"]
        self._submit(state_id, questions["Matter"].id, "True")  # -> Solid, no prerequisite behind it

        result = None
        for _ in range(MAX_STEP_QUESTION_ATTEMPTS):
            result = self._submit(state_id, questions["Solid"].id, "B")

        self.assertEqual(result.data["current_variant"], "simplified")
        self.assertEqual(result.data["next_step_position"], 2)  # stayed on the same step
        self.assertEqual(result.data["next_question"], questions["Solid"].id)

    def test_mastery_moves_with_each_answer(self):
        start = self._start()
        state_id = start.data["learning_state"]["id"]
        before = start.data["learning_state"]["mastery"]

        result = self._submit(state_id, self.questions["Matter"].id, "True")

        self.assertGreater(result.data["mastery"], before)

    def test_cannot_answer_a_question_that_is_not_current(self):
        start = self._start()
        state_id = start.data["learning_state"]["id"]

        result = self._submit(state_id, self.questions["Solid"].id, "A")

        self.assertEqual(result.status_code, 400)

    def test_student_response_recorded_against_the_generated_question(self):
        start = self._start()
        state_id = start.data["learning_state"]["id"]

        self._submit(state_id, self.questions["Matter"].id, "True")

        response = StudentResponse.objects.get(learning_state_id=state_id)
        self.assertEqual(response.generated_question_id, self.questions["Matter"].id)
        self.assertIsNone(response.question_id)
        self.assertTrue(response.is_correct)


class LessonPackageIncludesPathTests(APITestCase):
    def test_package_carries_the_published_path(self):
        from lessons.services.lesson_package import build_lesson_payload

        course, module, topic, groups, questions = _published_topic()

        payload = build_lesson_payload(topic)

        self.assertIsNotNone(payload["learning_path"])
        steps = payload["learning_path"]["steps"]
        self.assertEqual([s["title"] for s in steps], ["Matter", "Solid"])
        self.assertEqual(steps[1]["prerequisites"], [groups["Matter"].id])

    def test_package_omits_the_path_when_unpublished(self):
        from lessons.services.lesson_package import build_lesson_payload
        from .tests import _course_with_content

        _course, _module, topics = _course_with_content()

        payload = build_lesson_payload(topics[0][0])

        self.assertIsNone(payload["learning_path"])
