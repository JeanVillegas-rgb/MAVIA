from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from .models import CourseGroup, LearningMaterial, OutlineNode, Question


class ReviewUiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.teacher = get_user_model().objects.create_user(username="reviewteacher", role="TEACHER")
        self.course = CourseGroup.objects.create(title="Science")
        self.module = OutlineNode.objects.create(course=self.course, title="Matter")
        self.topic = OutlineNode.objects.create(course=self.course, parent=self.module, depth=1, title="Solid")
        self.material = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="Solid PDF", status="completed",
            generated_json={
                "narration_script": [{"order": 0, "content": "A solid keeps its shape."}],
                "lesson_playlist": [{"title": "Solid", "narration_item_order": 0, "audio_url": ""}],
            },
        )
        Question.objects.create(material=self.material, prompt="Does a solid keep its shape?", correct_answer="true")
        self.url = f"/api/courses/{self.course.pk}/review/modules/"
        self.package_url = f"{self.url}{self.module.pk}/package/"

    def test_review_requires_teacher_or_admin(self):
        for url in (self.url, self.package_url):
            self.assertIn(self.client.get(url).status_code, (401, 403))
        student = get_user_model().objects.create_user(username="reviewstudent", role="STUDENT")
        self.client.force_authenticate(student)
        for url in (self.url, self.package_url):
            self.assertEqual(self.client.get(url).status_code, 403)
        admin = get_user_model().objects.create_user(username="reviewadmin", role="ADMIN")
        self.client.force_authenticate(admin)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_teacher_preview_reads_existing_materials(self):
        self.client.force_authenticate(self.teacher)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]["track_count"], 1)
        self.assertEqual(response.data[0]["question_count"], 1)
        response = self.client.get(self.package_url)
        self.assertEqual(response.status_code, 200)
        lesson = response.data["lessons"][0]
        self.assertEqual(lesson["tracks"][0]["text"], "A solid keeps its shape.")
        self.assertFalse(lesson["tracks"][0]["audio_ready"])
        self.assertEqual(lesson["questions"][0]["correct_answer"], "true")
        self.assertEqual(LearningMaterial.objects.get(pk=self.material.pk).metadata_id, self.material.metadata_id)

    def test_module_must_belong_to_requested_course(self):
        other = CourseGroup.objects.create(title="Other")
        module = OutlineNode.objects.create(course=other, title="Other module")
        self.client.force_authenticate(self.teacher)
        self.assertEqual(self.client.get(f"{self.url}{module.pk}/package/").status_code, 404)

    def test_empty_course_and_topic(self):
        self.client.force_authenticate(self.teacher)
        self.material.delete()
        response = self.client.get(self.package_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["lessons"], [])
