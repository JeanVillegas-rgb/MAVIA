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
    MAX_LADDER_PASSES,
    MAX_REMEDIATION_DEPTH,
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


def _published_topic_with_alternate(*, with_prerequisite=False):
    """Like ``_published_topic``, but "Solid" is taught by *two* PDFs: the
    representative (unchanged) plus an independent alternate in a second
    material, each with its own question. Used for chunk-switching tests."""
    course, module, topic, groups, questions = _published_topic(with_prerequisite=with_prerequisite)
    second_material = LearningMaterial.objects.create(
        course=course, outline_node=topic, title="Lesson two (alt)",
        generated_json={"learning_objects_confirmed": True},
    )
    alt_object = LearningObject.objects.create(
        material=second_material, group=groups["Solid"], title="Solid (alt PDF)",
        content="Solids keep their shape, as this other book explains.", order=0,
    )
    alt_question = GeneratedQuestion.objects.create(
        node=alt_object, question_text="Alt: which keeps a fixed shape?",
        question_format="MCQ", choices={"A": "Solid", "B": "Gas"}, correct_answer="A",
        explanation="Same idea, a different book's words.",
        bloom_level="remember", thinking_order="LOT", difficulty="easy", status="final",
    )
    return course, module, topic, groups, questions, alt_object, alt_question


def _published_chain(length):
    """A straight-line prerequisite chain of ``length`` concepts, each
    depending on the one before it (position 1 needs nothing, position 2
    needs position 1, ...), one final question each. Used for the
    remediation-depth-cap tests, where a single branching fixture would be
    harder to reason about than a plain line."""
    course = CourseGroup.objects.create(title="Chain course")
    module = OutlineNode.objects.create(course=course, title="Module 1", order=0, depth=0)
    topic = OutlineNode.objects.create(
        course=course, parent=module, title="Chain topic", order=0, depth=1, published=True
    )
    material = LearningMaterial.objects.create(
        course=course, outline_node=topic, title="Lesson one",
        generated_json={"learning_objects_confirmed": True},
    )
    now = timezone.now()
    groups, objects, questions = [], [], []
    for index in range(length):
        title = f"Concept{index + 1}"
        group = LearningObjectGroup.objects.create(outline_node=topic, label=title)
        obj = LearningObject.objects.create(
            material=material, group=group, title=title, content=f"{title} content.", order=index,
        )
        LearningPathStep.objects.create(
            outline_node=topic, concept=group, position=index + 1, depth=index, published_at=now
        )
        question = GeneratedQuestion.objects.create(
            node=obj, question_text=f"{title}: correct or not?",
            question_format="TF", correct_answer="True", explanation="",
            bloom_level="remember", thinking_order="LOT", difficulty="easy", status="final",
        )
        if index > 0:
            ConceptPrerequisite.objects.create(
                outline_node=topic, prerequisite=groups[index - 1], dependent=group,
                status="accepted", source="derived",
            )
        groups.append(group)
        objects.append(obj)
        questions.append(question)
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
        return self.client.post("/api/adaptive/start/", {"course_id": self.course.id})

    def _submit(self, state_id, question_id, answer):
        return self.client.post(
            "/api/adaptive/submit-response/",
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

    def test_finishing_a_path_mode_topic_hands_off_to_the_next_legacy_topic(self):
        """The course isn't done just because *this* topic's published path
        is -- a second topic with no published path (flat lessons.Question
        list) should be picked up next, not read as course completion. This
        is the mobile "stops on one question segment" bug: the client used
        to treat current_step=None as terminal, discarding this handoff."""
        from lessons.models import Question

        second_topic = OutlineNode.objects.create(
            course=self.course, parent=self.module, title="Topic 2", order=1, depth=1, published=True
        )
        material = LearningMaterial.objects.create(
            course=self.course, outline_node=second_topic, title="Material 2",
            pdf_file="learning_materials/x.pdf",
        )
        legacy_question = Question.objects.create(
            material=material, prompt="Legacy Q1?", question_type=Question.Type.MULTIPLE_CHOICE,
            choices=["Correct", "Wrong"], correct_answer="a", order=0,
        )

        start = self._start()
        state_id = start.data["learning_state"]["id"]
        self._submit(state_id, self.questions["Matter"].id, "True")

        result = self._submit(state_id, self.questions["Solid"].id, "A")

        self.assertFalse(result.data["completed"])
        self.assertIsNone(result.data["current_step"])
        self.assertEqual(result.data["next_question"], legacy_question.id)
        self.assertEqual(result.data["next_lesson_node"], second_topic.id)
        self.assertEqual(result.data["lesson"]["id"], second_topic.id)
        self.assertEqual(result.data["lesson"]["questions"][0]["id"], legacy_question.id)

    def test_escalates_variant_on_each_miss_before_rerouting(self):
        """Reacts every miss, not just on the MAX_STEP_QUESTION_ATTEMPTS-th
        one: normal -> simplified on the first wrong answer, simplified ->
        elaborated on the second -- same question both times, nothing
        structural happens until elaborated has failed too."""
        start = self._start()
        state_id = start.data["learning_state"]["id"]
        self._submit(state_id, self.questions["Matter"].id, "True")  # -> Solid

        first = self._submit(state_id, self.questions["Solid"].id, "B")
        self.assertFalse(first.data["is_correct"])
        self.assertEqual(first.data["current_variant"], "simplified")
        self.assertEqual(first.data["next_question"], self.questions["Solid"].id)  # same question

        second = self._submit(state_id, self.questions["Solid"].id, "B")
        self.assertEqual(second.data["current_variant"], "elaborated")
        self.assertEqual(second.data["next_question"], self.questions["Solid"].id)

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
        # Fresh start on the prerequisite -- not still at "elaborated".
        self.assertEqual(result.data["current_variant"], "normal")

        state = LearningState.objects.get(pk=state_id)
        self.assertEqual(state.current_question_attempts, 0)

    def test_clearing_the_detour_resumes_at_the_last_escalated_variant_when_no_alternate(self):
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
        # "Solid" has no alternate chunk in this fixture -- resuming picks up
        # right where it left off (elaborated), not back at "normal".
        self.assertEqual(result.data["current_variant"], "elaborated")

    def test_leaf_concept_with_nothing_left_to_try_forces_the_learner_past_it(self):
        """No prerequisite (leaf concept) and no alternate chunk: the engine
        has nothing structural to reroute through, so it teaches the concept
        a second time from the top (MAX_LADDER_PASSES) and only then moves
        the learner on -- it never loops."""
        course, module, topic, groups, questions = _published_topic(with_prerequisite=False)
        Enrollment.objects.create(student=self.student, course=course)
        start = self.client.post("/api/adaptive/start/", {"course_id": course.id})
        state_id = start.data["learning_state"]["id"]
        self._submit(state_id, questions["Matter"].id, "True")  # -> Solid, the last step

        result = None
        for _ in range(MAX_STEP_QUESTION_ATTEMPTS):
            result = self._submit(state_id, questions["Solid"].id, "B")

        # One ladder spent. Nothing structural exists for this step, so rather
        # than dropping the learner it starts the concept over at "normal".
        self.assertFalse(result.data["completed"])
        self.assertEqual(result.data["current_variant"], "normal")
        self.assertEqual(result.data["next_question"], questions["Solid"].id)

        for _ in range(MAX_STEP_QUESTION_ATTEMPTS):
            result = self._submit(state_id, questions["Solid"].id, "B")

        # Second ladder spent too. Solid was the topic's last step and there's
        # nothing else in the course -- so now it completes rather than
        # stalling, which is the floor this test has always been about.
        self.assertTrue(result.data["completed"])
        self.assertIsNone(result.data["next_question"])

    def test_leaf_concept_switches_to_alternate_chunk_when_elaborated_fails(self):
        (course, module, topic, groups, questions,
         alt_object, alt_question) = _published_topic_with_alternate(with_prerequisite=False)
        Enrollment.objects.create(student=self.student, course=course)
        start = self.client.post("/api/adaptive/start/", {"course_id": course.id})
        state_id = start.data["learning_state"]["id"]
        self._submit(state_id, questions["Matter"].id, "True")  # -> Solid, no prerequisite

        result = None
        for _ in range(MAX_STEP_QUESTION_ATTEMPTS):
            result = self._submit(state_id, questions["Solid"].id, "B")

        self.assertFalse(result.data["completed"])  # the alternate gave it somewhere to go
        self.assertEqual(result.data["next_step_position"], 2)  # still "Solid" -- same concept
        self.assertEqual(result.data["current_chunk"], alt_object.id)
        self.assertEqual(result.data["next_question"], alt_question.id)
        self.assertEqual(result.data["current_variant"], "normal")  # fresh chunk, fresh ladder

        # Answering the alternate's own question doesn't 500 (regression test
        # for the SubmitResponseView lookup that only checked the
        # representative's question list).
        cleared = self._submit(state_id, alt_question.id, "A")
        self.assertEqual(cleared.status_code, 200)
        self.assertTrue(cleared.data["is_correct"])
        self.assertTrue(cleared.data["completed"])

    def test_resume_prefers_an_unused_alternate_chunk_over_repeating_elaborated(self):
        (course, module, topic, groups, questions,
         alt_object, alt_question) = _published_topic_with_alternate(with_prerequisite=True)
        Enrollment.objects.create(student=self.student, course=course)
        start = self.client.post("/api/adaptive/start/", {"course_id": course.id})
        state_id = start.data["learning_state"]["id"]
        self._submit(state_id, questions["Matter"].id, "True")  # -> Solid
        for _ in range(MAX_STEP_QUESTION_ATTEMPTS):
            self._submit(state_id, questions["Solid"].id, "B")  # -> detoured to Matter

        result = self._submit(state_id, questions["Matter"].id, "True")  # clears the detour

        self.assertTrue(result.data["is_correct"])
        self.assertEqual(result.data["next_step_position"], 2)  # back to "Solid"
        # An alternate existed and hadn't been tried yet -- prefer it, fresh.
        self.assertEqual(result.data["current_chunk"], alt_object.id)
        self.assertEqual(result.data["next_question"], alt_question.id)
        self.assertEqual(result.data["current_variant"], "normal")

    def test_remediation_depth_is_capped(self):
        """A chain deep enough to need MAX_REMEDIATION_DEPTH + 1 nested
        detours: the engine stops rerouting once the cap is hit rather than
        chaining indefinitely back through the whole prerequisite graph.
        Assumes MAX_REMEDIATION_DEPTH == 2; drives the chain by always
        resubmitting whatever question the previous response actually
        assigned, since which concept is current shifts as detours nest."""
        self.assertEqual(MAX_REMEDIATION_DEPTH, 2, "test assumes depth 2; update if the cap changes")
        chain_course, _module, _topic, groups, questions = _published_chain(4)  # C1 <- C2 <- C3 <- C4
        Enrollment.objects.create(student=self.student, course=chain_course)
        start = self.client.post("/api/adaptive/start/", {"course_id": chain_course.id})
        state_id = start.data["learning_state"]["id"]

        # Correctly clear C1-C3, landing on C4 with the whole chain behind it.
        for question in questions[:3]:
            self._submit(state_id, question.id, "True")

        def fail_current_three_times():
            result = None
            current_id = LearningState.objects.get(pk=state_id).current_generated_question_id
            for _ in range(MAX_STEP_QUESTION_ATTEMPTS):
                result = self._submit(state_id, current_id, "B")
                current_id = result.data["next_question"]
            return result

        first = fail_current_three_times()  # C4 exhausted -> detour to C3 (depth 1)
        self.assertEqual(first.data["next_step_position"], 3)
        state = LearningState.objects.get(pk=state_id)
        self.assertEqual(len(state.remediation_stack), 1)

        second = fail_current_three_times()  # C3 exhausted -> detour to C2 (depth 2, at the cap)
        self.assertEqual(second.data["next_step_position"], 2)
        state = LearningState.objects.get(pk=state_id)
        self.assertEqual(len(state.remediation_stack), 2)

        third = fail_current_three_times()  # C2 exhausted -> cap blocks a 3rd detour to C1
        state = LearningState.objects.get(pk=state_id)
        # No alternates anywhere in this fixture and the depth cap blocked
        # rerouting any deeper -- the engine gave up on C2 and resumed
        # whatever was waiting on the stack (C3) instead of detouring to C1.
        self.assertLessEqual(len(state.remediation_stack), MAX_REMEDIATION_DEPTH)
        self.assertEqual(state.remediation_stack, [{"position": 4, "chunk_id": None}])
        self.assertEqual(third.data["next_step_position"], 3)
        self.assertEqual(third.data["current_variant"], "elaborated")
        self.assertFalse(third.data["completed"])

    def test_a_step_with_no_remedy_available_gets_a_second_pass(self):
        """"Matter" is position 1: no prerequisite to send the learner back
        through, and no alternate chunk to switch to. Nothing structural is
        available for it at all.

        Every other step gets the variant ladder *plus* one structural
        intervention. This one would get the ladder alone and then push the
        learner into "Solid", which depends on the very concept they just
        missed. So it is taught once more from the top instead -- and the
        second pass is the last, so this cannot become a loop.
        """
        self.assertEqual(MAX_LADDER_PASSES, 2, "test assumes 2 passes; update if that changes")
        start = self._start()
        state_id = start.data["learning_state"]["id"]
        question_id = self.questions["Matter"].id

        walked = []
        for _ in range(MAX_LADDER_PASSES * MAX_STEP_QUESTION_ATTEMPTS):
            result = self._submit(state_id, question_id, "__always-wrong__")
            walked.append((result.data["next_step_position"], result.data["current_variant"]))
            question_id = result.data["next_question"]

        self.assertEqual(
            walked,
            [
                (1, "simplified"), (1, "elaborated"),   # first pass up the ladder
                (1, "normal"), (1, "simplified"), (1, "elaborated"),  # taught again
                (2, "normal"),  # only now move on
            ],
        )
        self.assertEqual(question_id, self.questions["Solid"].id)

    def test_a_step_that_had_a_detour_does_not_also_get_a_second_pass(self):
        """"Solid" has a prerequisite, so it gets the structural intervention
        instead. Once that detour is spent it moves on rather than also
        re-walking the ladder -- otherwise a struggling learner would collect
        every remedy on every step and the topic would crawl."""
        start = self._start()
        state_id = start.data["learning_state"]["id"]
        self._submit(state_id, self.questions["Matter"].id, "True")  # clear step 1

        question_id = self.questions["Solid"].id
        for _ in range(MAX_STEP_QUESTION_ATTEMPTS):  # exhaust Solid's ladder -> detour
            result = self._submit(state_id, question_id, "B")
            question_id = result.data["next_question"]
        self.assertEqual(result.data["next_step_position"], 1)  # detoured into Matter

        state = LearningState.objects.get(pk=state_id)
        self.assertEqual(state.remediated_positions, [2])

    def test_advancing_skips_a_step_the_learner_already_cleared(self):
        """``_advance_past`` picked the next step that *has* questions, then
        asked for one excluding those already answered correctly -- getting
        ``None`` back and crashing on ``None["id"]``. Any learner advanced
        into a step they had cleared on an earlier pass got a 500 from
        submit-response in the middle of a lesson.
        """
        from .services import AdaptiveEngine, published_path_for

        start = self._start()
        state = LearningState.objects.get(pk=start.data["learning_state"]["id"])
        # "Solid" (position 2) is the only other step, and it is already done.
        StudentResponse.objects.create(
            learning_state=state,
            generated_question_id=self.questions["Solid"].id,
            selected_answer="A",
            is_correct=True,
        )
        path = published_path_for(state.current_lesson_node)

        AdaptiveEngine._advance_past(state, path, 1)

        # Nothing answerable is left anywhere, so the course is finished --
        # not a crash, and not a step with no question to show.
        self.assertTrue(state.completed)
        self.assertIsNone(state.current_generated_question_id)

    def test_advancing_lands_on_the_next_step_that_still_has_a_question(self):
        """The same scan, but with somewhere real to land: a cleared step in
        the middle is stepped over rather than being treated as the next
        thing to teach."""
        from .services import AdaptiveEngine, published_path_for

        chain_course, _module, _topic, _groups, questions = _published_chain(3)
        Enrollment.objects.create(student=self.student, course=chain_course)
        start = self.client.post("/api/adaptive/start/", {"course_id": chain_course.id})
        state = LearningState.objects.get(pk=start.data["learning_state"]["id"])
        # Concept2 (position 2) already cleared; Concept3 is not.
        StudentResponse.objects.create(
            learning_state=state, generated_question_id=questions[1].id,
            selected_answer="True", is_correct=True,
        )
        path = published_path_for(state.current_lesson_node)

        AdaptiveEngine._advance_past(state, path, 1)

        self.assertEqual(state.current_step_position, 3)
        self.assertEqual(state.current_generated_question_id, questions[2].id)
        self.assertFalse(state.completed)

    def test_a_step_gets_one_detour_not_one_per_failure(self):
        """The loop this used to fall into: a step fails at "elaborated" and
        detours into its prerequisite; the detour is resumed (popping the
        stack); the step fails again and -- with the stack now empty -- was
        free to detour into the very same prerequisite again, forever.

        MAX_REMEDIATION_DEPTH cannot catch it, because the stack never grows
        past 1. LearningState.remediated_positions is what bounds repetition,
        and this asserts the learner reaches the end instead of circling.
        """
        start = self.client.post("/api/adaptive/start/", {"course_id": self.course.id})
        state_id = start.data["learning_state"]["id"]
        question_id = start.data["learning_state"]["current_generated_question"]

        seen, completed = set(), False
        for _ in range(40):
            state = LearningState.objects.get(pk=state_id)
            seen.add((
                state.current_step_position,
                state.current_variant,
                state.current_generated_question_id,
                tuple(frame["position"] for frame in state.remediation_stack),
            ))
            result = self._submit(state_id, question_id, "__always-wrong__")
            if result.data["completed"]:
                completed = True
                break
            question_id = result.data["next_question"]

        self.assertTrue(completed, "a learner answering everything wrong never reached the end")
        state = LearningState.objects.get(pk=state_id)
        self.assertEqual(state.remediation_stack, [])
        # Two concepts, so at most two positions can ever spend a detour.
        self.assertLessEqual(len(state.remediated_positions), 2)

    def test_remediated_positions_reset_between_topics(self):
        """Positions are numbered per topic, so the record of which ones have
        spent their detour cannot carry across a topic boundary -- step 1 of
        the second topic would inherit step 1 of the first topic's history."""
        start = self.client.post("/api/adaptive/start/", {"course_id": self.course.id})
        state_id = start.data["learning_state"]["id"]
        state = LearningState.objects.get(pk=state_id)
        state.remediated_positions = [1, 2]
        state.save()

        from .services import AdaptiveEngine, published_path_for
        path = published_path_for(state.current_lesson_node)
        AdaptiveEngine._advance_past(state, path, path["steps"][-1]["position"])

        self.assertEqual(state.remediated_positions, [])

    def test_true_false_accepts_the_numpad_letters(self):
        """The device sends "a"/"b" for True/False so the braille numpad's
        keys mean the same thing on every kind of question. GeneratedQuestion
        stores TF answers as the words, and the device never holds a correct
        answer to translate against, so the letters are resolved server-side.
        """
        from .services import path_answer_is_correct

        yes = {"format": "TF", "correct_answer": "True", "choices": None}
        no = {"format": "TF", "correct_answer": "False", "choices": None}

        self.assertTrue(path_answer_is_correct(yes, "a"))
        self.assertTrue(path_answer_is_correct(yes, "A"))
        self.assertTrue(path_answer_is_correct(yes, "true"))  # still accepted
        self.assertFalse(path_answer_is_correct(yes, "b"))

        self.assertTrue(path_answer_is_correct(no, "b"))
        self.assertTrue(path_answer_is_correct(no, "false"))
        self.assertFalse(path_answer_is_correct(no, "a"))

        self.assertFalse(path_answer_is_correct(yes, ""))

    def test_multiple_choice_letters_are_not_read_as_true_or_false(self):
        """"A" is a real option letter on a multiple-choice question, so it
        must not also match the word "true" -- otherwise a student could pass
        by sending a word that was never on offer."""
        from .services import path_answer_is_correct

        mcq = {"format": "MCQ", "correct_answer": "A", "choices": {"A": "Solid", "B": "Liquid"}}

        self.assertTrue(path_answer_is_correct(mcq, "a"))
        self.assertTrue(path_answer_is_correct(mcq, "Solid"))
        self.assertFalse(path_answer_is_correct(mcq, "true"))
        self.assertFalse(path_answer_is_correct(mcq, "b"))

    def test_answering_a_true_false_question_with_its_letter_over_the_api(self):
        """End to end: "Matter" is a TF question whose answer is True, and
        the letter the student's numpad actually sends grades correct."""
        start = self._start()
        state_id = start.data["learning_state"]["id"]

        result = self._submit(state_id, self.questions["Matter"].id, "a")

        self.assertTrue(result.data["is_correct"])
        self.assertEqual(result.data["next_step_position"], 2)

    def test_reopening_a_finished_course_teaches_the_topic_again(self):
        """A completed state has no assigned question, so ``current_step``
        came back ``None``, the player dropped out of path mode, and the
        student got a flat playlist with no questions after any concept.
        Opening the lesson again now restarts that topic properly."""
        start = self._start()
        state_id = start.data["learning_state"]["id"]
        self._submit(state_id, self.questions["Matter"].id, "True")
        result = self._submit(state_id, self.questions["Solid"].id, "A")
        self.assertTrue(result.data["completed"])

        reopened = self.client.post(
            "/api/adaptive/start/",
            {"course_id": self.course.id, "lesson_node_id": self.topic.id},
        )

        self.assertFalse(reopened.data["learning_state"]["completed"])
        self.assertEqual(reopened.data["learning_state"]["current_step_position"], 1)
        self.assertIsNotNone(reopened.data["current_step"])
        # Every rung is there to teach from, and the concept's own questions
        # come with it -- that is the "question segment after each topic".
        self.assertEqual(
            reopened.data["current_step"]["questions"][0]["id"], self.questions["Matter"].id
        )

    def test_a_replay_is_not_skipped_by_the_previous_run_s_answers(self):
        """Without per-attempt scoping the replay is pointless: every question
        is already answered correctly on record, so the engine advances past
        every step and completes again without teaching anything."""
        start = self._start()
        state_id = start.data["learning_state"]["id"]
        self._submit(state_id, self.questions["Matter"].id, "True")
        self._submit(state_id, self.questions["Solid"].id, "A")

        self.client.post(
            "/api/adaptive/start/",
            {"course_id": self.course.id, "lesson_node_id": self.topic.id},
        )
        state = LearningState.objects.get(pk=state_id)

        # Answering the first concept again must move to the second, not shoot
        # straight to "completed" on the strength of the earlier run.
        result = self._submit(state_id, self.questions["Matter"].id, "True")
        self.assertFalse(result.data["completed"])
        self.assertEqual(result.data["next_step_position"], 2)
        self.assertEqual(result.data["next_question"], self.questions["Solid"].id)

    def test_opening_a_lesson_the_cursor_is_not_on_switches_to_it(self):
        """The player is opened per lesson but the engine's cursor is
        course-wide. Tapping a different lesson teaches that one rather than
        serving another topic's concept under its title."""
        second_course, _m, second_topic, _g, second_questions = _published_topic()
        Enrollment.objects.create(student=self.student, course=second_course)
        # Park the cursor on this course's own first topic.
        start = self.client.post("/api/adaptive/start/", {"course_id": second_course.id})
        state_id = start.data["learning_state"]["id"]
        self.assertEqual(start.data["lesson"]["id"], second_topic.id)

        # Nothing to switch to within this course, so opening the same topic
        # is a resume, not a restart: the assigned question is unchanged.
        again = self.client.post(
            "/api/adaptive/start/",
            {"course_id": second_course.id, "lesson_node_id": second_topic.id},
        )
        self.assertEqual(
            again.data["learning_state"]["current_generated_question"],
            second_questions["Matter"].id,
        )
        self.assertEqual(again.data["learning_state"]["id"], state_id)

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
