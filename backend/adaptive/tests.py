from django.urls import reverse
from rest_framework.test import APITestCase

from lessons.models import CourseGroup, LearningMaterial, OutlineNode, Question
from user.models import User

from .models import Enrollment, LearningState
from .services import AdaptiveEngine, answer_is_correct, ordered_course_steps, resolve_start


def _course_with_content(*, topics=1, questions_per_topic=2):
    course = CourseGroup.objects.create(title="Science 7")
    module = OutlineNode.objects.create(course=course, title="Module 1", order=0, depth=0)
    made = []
    for t in range(topics):
        topic = OutlineNode.objects.create(
            course=course, parent=module, title=f"Topic {t + 1}", order=t, depth=1
        )
        material = LearningMaterial.objects.create(
            course=course,
            outline_node=topic,
            title=f"Material {t + 1}",
            pdf_file="learning_materials/x.pdf",
            status=LearningMaterial.Status.COMPLETED,
        )
        topic_questions = [
            Question.objects.create(
                material=material,
                prompt=f"T{t + 1} Q{q + 1}?",
                question_type=Question.Type.MULTIPLE_CHOICE,
                choices=["Correct", "Wrong", "Nope"],
                correct_answer="a",
                order=q,
            )
            for q in range(questions_per_topic)
        ]
        made.append((topic, material, topic_questions))
    return course, module, made


class EngineTests(APITestCase):
    def setUp(self):
        self.student = User.objects.create_user(
            username="stu", password="pw", role=User.Role.STUDENT, is_verified=True
        )
        self.course, self.module, self.topics = _course_with_content(
            topics=2, questions_per_topic=2
        )

    def _fresh_state(self):
        module, node, question = resolve_start(self.course)
        return LearningState.objects.create(
            student=self.student,
            course=self.course,
            current_module=module,
            current_lesson_node=node,
            current_question=question,
        )

    def test_ordered_steps_span_topics(self):
        steps = ordered_course_steps(self.course)
        self.assertEqual(len(steps), 2)
        self.assertEqual([len(q) for _m, _n, q in steps], [2, 2])

    def test_resolve_start_is_first_question(self):
        _module, node, question = resolve_start(self.course)
        self.assertEqual(node, self.topics[0][0])
        self.assertEqual(question, self.topics[0][2][0])

    def test_correct_answer_advances_within_topic(self):
        state = self._fresh_state()
        q1 = self.topics[0][2][0]
        result = AdaptiveEngine.evaluate(state, q1, "a")
        self.assertTrue(result["is_correct"])
        self.assertFalse(result["completed"])
        self.assertEqual(result["next_question"], self.topics[0][2][1].id)

    def test_correct_answer_crosses_topic_boundary(self):
        state = self._fresh_state()
        AdaptiveEngine.evaluate(state, self.topics[0][2][0], "a")
        result = AdaptiveEngine.evaluate(state, self.topics[0][2][1], "a")
        self.assertEqual(result["next_lesson_node"], self.topics[1][0].id)
        self.assertEqual(result["next_question"], self.topics[1][2][0].id)

    def test_finishing_last_question_completes_course(self):
        state = self._fresh_state()
        for topic, _material, qs in self.topics:
            for q in qs:
                state.current_lesson_node = topic
                state.current_question = q
                state.save()
                result = AdaptiveEngine.evaluate(state, q, "a")
        self.assertTrue(result["completed"])
        self.assertIsNone(result["next_question"])

    def test_three_wrong_answers_move_learner_on(self):
        state = self._fresh_state()
        q1 = self.topics[0][2][0]
        for _ in range(2):
            result = AdaptiveEngine.evaluate(state, q1, "b")
            self.assertEqual(result["next_question"], q1.id)
        result = AdaptiveEngine.evaluate(state, q1, "b")
        self.assertEqual(result["next_question"], self.topics[0][2][1].id)

    def test_mastery_moves_in_the_right_direction(self):
        state = self._fresh_state()
        before = state.mastery
        AdaptiveEngine.evaluate(state, self.topics[0][2][0], "a")
        self.assertGreater(state.mastery, before)

    def test_answer_matching_forms(self):
        mcq = self.topics[0][2][0]
        self.assertTrue(answer_is_correct(mcq, "a"))
        self.assertTrue(answer_is_correct(mcq, "A"))
        self.assertTrue(answer_is_correct(mcq, "Correct"))
        self.assertFalse(answer_is_correct(mcq, "b"))
        tf = Question.objects.create(
            material=self.topics[0][1],
            prompt="Sky is blue?",
            question_type=Question.Type.TRUE_FALSE,
            choices=["True", "False"],
            correct_answer="true",
            order=9,
        )
        self.assertTrue(answer_is_correct(tf, "true"))
        self.assertTrue(answer_is_correct(tf, "a"))
        self.assertFalse(answer_is_correct(tf, "false"))


class EnrollmentAndProgressApiTests(APITestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="teach", password="pw", role=User.Role.TEACHER, is_verified=True
        )
        self.student = User.objects.create_user(
            username="stu", password="pw", role=User.Role.STUDENT, is_verified=True
        )
        self.course, self.module, self.topics = _course_with_content(
            topics=1, questions_per_topic=2
        )

    def test_enroll_then_progress_shows_not_started(self):
        self.client.force_authenticate(self.teacher)
        resp = self.client.post(
            f"/api/adaptive/courses/{self.course.id}/enrollments/",
            {"student_id": self.student.id},
        )
        self.assertEqual(resp.status_code, 201)

        progress = self.client.get(f"/api/adaptive/courses/{self.course.id}/progress/")
        self.assertEqual(progress.status_code, 200)
        self.assertEqual(len(progress.data["rows"]), 1)
        row = progress.data["rows"][0]
        self.assertFalse(row["started"])
        self.assertEqual(row["total_modules"], 1)

    def test_start_requires_enrollment(self):
        self.client.force_authenticate(self.student)
        resp = self.client.post("/api/adaptive/start/", {"course_id": self.course.id})
        self.assertEqual(resp.status_code, 403)

    def test_full_student_flow_updates_progress(self):
        Enrollment.objects.create(student=self.student, course=self.course)
        self.client.force_authenticate(self.student)

        start = self.client.post("/api/adaptive/start/", {"course_id": self.course.id})
        self.assertEqual(start.status_code, 201)
        state_id = start.data["learning_state"]["id"]
        first_q = start.data["lesson"]["questions"][0]["id"]

        submit = self.client.post(
            "/api/adaptive/submit-response/",
            {"learning_state_id": state_id, "question_id": first_q, "selected_answer": "a"},
        )
        self.assertEqual(submit.status_code, 200)
        self.assertTrue(submit.data["is_correct"])

        self.client.force_authenticate(self.teacher)
        progress = self.client.get(f"/api/adaptive/courses/{self.course.id}/progress/")
        row = progress.data["rows"][0]
        self.assertTrue(row["started"])
        self.assertEqual(row["questions_answered"], 1)
        self.assertEqual(row["correct_rate"], 1.0)


class ReviewPackageApiTests(APITestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="teach", password="pw", role=User.Role.TEACHER, is_verified=True
        )
        self.course, self.module, self.topics = _course_with_content(
            topics=1, questions_per_topic=2
        )
        material = self.topics[0][1]
        material.generated_json = {
            "narration_script": [
                {"order": 0, "content": "Intro narration.", "title": "Intro"},
                {"order": 1, "content": "Second part.", "title": "Part 2"},
            ],
            "lesson_playlist": [
                {
                    "order": 0,
                    "title": "Intro",
                    "type": "lesson_content",
                    "narration_item_order": 0,
                    "audio_url": "/media/audio_lessons/material_x/playlist_item_1.mp3",
                },
                {
                    "order": 1,
                    "title": "Part 2",
                    "type": "lesson_content",
                    "narration_item_order": 1,
                },
            ],
        }
        material.save(update_fields=["generated_json"])

    def test_module_summary_and_package(self):
        self.client.force_authenticate(self.teacher)
        summaries = self.client.get(f"/api/courses/{self.course.id}/review/modules/")
        self.assertEqual(summaries.status_code, 200)
        self.assertEqual(summaries.data[0]["track_count"], 2)
        self.assertEqual(summaries.data[0]["question_count"], 2)

        package = self.client.get(
            f"/api/courses/{self.course.id}/review/modules/{self.module.id}/package/"
        )
        self.assertEqual(package.status_code, 200)
        lesson = package.data["lessons"][0]
        self.assertEqual(len(lesson["tracks"]), 2)
        self.assertTrue(lesson["tracks"][0]["audio_ready"])
        self.assertFalse(lesson["tracks"][1]["audio_ready"])
        self.assertEqual(lesson["tracks"][0]["text"], "Intro narration.")
        self.assertTrue(lesson["has_questions"])

    def test_mark_reviewed_toggles(self):
        self.client.force_authenticate(self.teacher)
        url = f"/api/courses/{self.course.id}/review/modules/{self.module.id}/mark-reviewed/"
        first = self.client.post(url)
        self.assertTrue(first.data["reviewed"])
        second = self.client.post(url)
        self.assertFalse(second.data["reviewed"])
