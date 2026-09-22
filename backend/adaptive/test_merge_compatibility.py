from django.test import TestCase
from rest_framework.test import APIClient

from adaptive_config.models import AdaptiveConfig
from user.models import User
from .models import Enrollment, LearningState, StudentResponse
from .services import _bkt_update
from .tests import _course_with_content


class MergeCompatibilityTests(TestCase):
    """Regression tests from when this app was ported in alongside the
    legacy ``adaptive`` app (course.LessonNode + GeneratedQuestion, unused by
    any client and never wired to AdaptiveConfig). That app has since been
    removed -- nothing referenced /api/adaptive/* -- leaving this app as the
    only adaptive engine. The remaining tests still guard real behavior."""

    def setUp(self):
        self.student = User.objects.create_user(username="compat", role="STUDENT")
        self.course, self.module, self.topics = _course_with_content()
        Enrollment.objects.create(student=self.student, course=self.course)
        self.client = APIClient()
        self.client.force_authenticate(self.student)

    def test_drafts_are_not_available_to_students(self):
        topic = self.topics[0][0]
        topic.published = False
        topic.save()
        response = self.client.get(f"/api/adaptive/my-courses/{self.course.pk}/lessons/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])
        self.assertEqual(self.client.get(f"/api/adaptive/lessons/{topic.pk}/").status_code, 404)
        started = self.client.post("/api/adaptive/start/", {"course_id": self.course.pk})
        self.assertIsNone(started.data["lesson"])

    def test_cannot_submit_unassigned_question_or_after_unenrollment(self):
        response = self.client.post("/api/adaptive/start/", {"course_id": self.course.pk})
        state_id = response.data["learning_state"]["id"]
        payload = {"learning_state_id": state_id, "question_id": self.topics[0][2][1].pk, "selected_answer": "a"}
        self.assertEqual(self.client.post("/api/adaptive/submit-response/", payload).status_code, 400)
        Enrollment.objects.all().delete()
        payload["question_id"] = self.topics[0][2][0].pk
        self.assertEqual(self.client.post("/api/adaptive/submit-response/", payload).status_code, 403)
        self.assertEqual(StudentResponse.objects.count(), 0)
        self.assertEqual(LearningState.objects.get(pk=state_id).attempts, 0)

    def test_weights_affect_portal_and_starting_mastery(self):
        config = AdaptiveConfig.load()
        old = _bkt_update(0.3, True)
        config.p_learn = 0.8
        config.starting_mastery = 0.6
        config.save()
        self.assertGreater(_bkt_update(0.3, True), old)
        response = self.client.post("/api/adaptive/start/", {"course_id": self.course.pk})
        self.assertEqual(float(response.data["learning_state"]["mastery"]), 0.6)
