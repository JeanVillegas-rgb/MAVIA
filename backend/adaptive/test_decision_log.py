"""The decision log and per-concept mastery.

Every graded answer stores one full transition on ``StudentResponse``: where the
learner was and what they were shown, what the engine decided, and where that
left them. ``LearningState.concept_mastery`` tracks knowledge per concept beside
the course-wide ``mastery``. Both exist to give later analysis (and the planned
RL work) real (state, action, reward, next state) records -- and neither may
change what a student's device receives.
"""

from rest_framework.test import APITestCase

from adaptive_config.models import AdaptiveConfig
from user.models import User

from .models import Enrollment, LearningState, StudentResponse
from .services import MAX_STEP_QUESTION_ATTEMPTS, _bkt_update
from .test_path_mode import _published_topic, _published_topic_with_alternate
from .tests import _course_with_content

Action = StudentResponse.Action


class DecisionLogFixture(APITestCase):
    def setUp(self):
        self.student = User.objects.create_user(
            username="logged", password="pw", role=User.Role.STUDENT, is_verified=True,
        )
        self.client.force_authenticate(self.student)

    def _enrol_and_start(self, course):
        Enrollment.objects.create(student=self.student, course=course)
        return self.client.post("/api/adaptive/start/", {"course_id": course.id}, format="json").data

    def _submit(self, state_id, question_id, answer):
        response = self.client.post(
            "/api/adaptive/submit-response/",
            {"learning_state_id": state_id, "question_id": question_id, "selected_answer": answer},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        return response

    def _last_row(self):
        return StudentResponse.objects.order_by("-id").first()


class PathModeDecisionLogTests(DecisionLogFixture):
    def setUp(self):
        super().setUp()
        # Matter (position 1) is a prerequisite of Solid (position 2).
        self.course, _module, _topic, self.groups, self.questions = _published_topic()
        start = self._enrol_and_start(self.course)
        self.state_id = start["learning_state"]["id"]

    def test_a_miss_logs_the_whole_transition(self):
        self._submit(self.state_id, self.questions["Matter"].id, "__wrong__")
        row = self._last_row()

        starting = AdaptiveConfig.load().starting_mastery
        self.assertEqual(
            (row.step_position, row.variant, row.chunk_id, row.attempt_number, row.remediation_depth),
            (1, "normal", None, 1, 0),
        )
        self.assertEqual(row.action, Action.ESCALATE_VARIANT)
        self.assertEqual((row.next_step_position, row.next_variant, row.next_chunk_id), (1, "simplified", None))
        self.assertEqual(row.concept_key, f"concept:{self.groups['Matter'].id}")
        self.assertAlmostEqual(row.concept_mastery_before, starting)
        self.assertAlmostEqual(row.concept_mastery_after, _bkt_update(starting, False))
        self.assertFalse(row.is_correct)

    def test_attempt_number_counts_up_the_ladder(self):
        question = self.questions["Matter"].id
        for _ in range(MAX_STEP_QUESTION_ATTEMPTS):
            self._submit(self.state_id, question, "__wrong__")

        rows = list(StudentResponse.objects.order_by("id"))
        self.assertEqual([row.attempt_number for row in rows], [1, 2, 3])
        self.assertEqual([row.variant for row in rows], ["normal", "simplified", "elaborated"])

    def test_a_root_concept_with_no_remedy_logs_its_second_pass(self):
        question = self.questions["Matter"].id
        for _ in range(MAX_STEP_QUESTION_ATTEMPTS):
            self._submit(self.state_id, question, "__wrong__")

        self.assertEqual(self._last_row().action, Action.SECOND_PASS)
        self.assertEqual(self._last_row().next_variant, "normal")

    def test_a_detour_and_its_resume_are_both_logged(self):
        self._submit(self.state_id, self.questions["Matter"].id, "True")
        self.assertEqual(self._last_row().action, Action.ADVANCE)

        solid = self.questions["Solid"].id
        for _ in range(MAX_STEP_QUESTION_ATTEMPTS):
            self._submit(self.state_id, solid, "B")
        detour = self._last_row()
        self.assertEqual(detour.action, Action.DETOUR_PREREQUISITE)
        self.assertEqual((detour.step_position, detour.next_step_position), (2, 1))
        self.assertEqual(detour.remediation_depth, 0)

        self._submit(self.state_id, self.questions["Matter"].id, "True")
        resume = self._last_row()
        self.assertEqual(resume.action, Action.RESUME)
        self.assertEqual(resume.remediation_depth, 1)
        self.assertEqual((resume.step_position, resume.next_step_position), (1, 2))

    def test_finishing_logs_complete(self):
        self._submit(self.state_id, self.questions["Matter"].id, "True")
        self._submit(self.state_id, self.questions["Solid"].id, "A")

        self.assertEqual(self._last_row().action, Action.COMPLETE)
        self.assertIsNone(self._last_row().next_step_position)

    def test_mastery_moves_only_for_the_concept_answered(self):
        self._submit(self.state_id, self.questions["Matter"].id, "__wrong__")

        state = LearningState.objects.get(pk=self.state_id)
        matter, solid = f"concept:{self.groups['Matter'].id}", f"concept:{self.groups['Solid'].id}"
        starting = AdaptiveConfig.load().starting_mastery
        self.assertAlmostEqual(state.concept_mastery[matter], _bkt_update(starting, False))
        self.assertNotIn(solid, state.concept_mastery)

    def test_course_wide_mastery_is_unchanged_by_the_new_bookkeeping(self):
        answers = [("Matter", "__wrong__"), ("Matter", "__wrong__")]
        expected = LearningState.objects.get(pk=self.state_id).mastery
        for concept, answer in answers:
            self._submit(self.state_id, self.questions[concept].id, answer)
            expected = _bkt_update(expected, False)

        self.assertAlmostEqual(LearningState.objects.get(pk=self.state_id).mastery, expected)

    def test_the_device_reply_carries_none_of_the_log(self):
        body = self._submit(self.state_id, self.questions["Matter"].id, "__wrong__").data

        for key in ("action", "concept_key", "concept_mastery_before", "concept_mastery_after"):
            self.assertNotIn(key, body)


class SwitchSourceDecisionLogTests(DecisionLogFixture):
    def test_switching_to_another_source_is_logged(self):
        course, _m, _t, _g, questions, alt_object, _alt_q = _published_topic_with_alternate()
        start = self._enrol_and_start(course)
        state_id = start["learning_state"]["id"]
        self._submit(state_id, questions["Matter"].id, "True")

        for _ in range(MAX_STEP_QUESTION_ATTEMPTS):
            self._submit(state_id, questions["Solid"].id, "B")

        row = self._last_row()
        self.assertEqual(row.action, Action.SWITCH_SOURCE)
        self.assertIsNone(row.chunk_id)
        self.assertEqual(row.next_chunk_id, alt_object.id)


class LegacyModeDecisionLogTests(DecisionLogFixture):
    def setUp(self):
        super().setUp()
        self.course, _module, self.topics = _course_with_content(topics=1, questions_per_topic=2)
        start = self._enrol_and_start(self.course)
        self.state_id = start["learning_state"]["id"]
        self.topic, _material, self.questions = self.topics[0]

    def test_a_miss_is_a_retry_on_the_topic_key(self):
        self._submit(self.state_id, self.questions[0].id, "b")
        row = self._last_row()

        self.assertEqual(row.action, Action.RETRY)
        self.assertEqual(row.concept_key, f"topic:{self.topic.id}")
        self.assertIsNone(row.step_position)
        self.assertEqual(row.attempt_number, 1)

    def test_a_correct_answer_advances(self):
        self._submit(self.state_id, self.questions[0].id, "a")
        self.assertEqual(self._last_row().action, Action.ADVANCE)
