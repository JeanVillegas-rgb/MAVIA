from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from lessons.models import CourseGroup


class CourseNameUniquenessTests(TestCase):
    def _teacher_client(self, username):
        user = get_user_model().objects.create_user(
            username=username,
            password="test-password",
            email=f"{username}@example.com",
            role=get_user_model().Role.TEACHER,
            is_verified=True,
        )
        client = APIClient()
        client.force_authenticate(user=user)
        return user, client

    def test_same_teacher_cannot_create_same_normalized_course_name(self):
        teacher, client = self._teacher_client("teacher_one")

        first = client.post(
            "/api/courses/",
            {"title": "Grade 9 - Matter", "description": "First"},
            format="json",
        )
        duplicate = client.post(
            "/api/courses/",
            {"title": "  grade 9   -   matter  ", "description": "Second"},
            format="json",
        )

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(duplicate.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("already have a course", str(duplicate.data["title"][0]))
        self.assertEqual(CourseGroup.objects.filter(created_by=teacher).count(), 1)

    def test_different_teachers_can_use_the_same_course_name(self):
        _first_teacher, first_client = self._teacher_client("teacher_one")
        _second_teacher, second_client = self._teacher_client("teacher_two")

        first = first_client.post(
            "/api/courses/",
            {"title": "Grade 9 - Matter", "description": "First teacher"},
            format="json",
        )
        second = second_client.post(
            "/api/courses/",
            {"title": "GRADE 9 - MATTER", "description": "Second teacher"},
            format="json",
        )

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_201_CREATED)
        self.assertEqual(CourseGroup.objects.count(), 2)

    def test_renaming_a_course_cannot_duplicate_another_course_name(self):
        _teacher, client = self._teacher_client("teacher_one")
        first = client.post(
            "/api/courses/",
            {"title": "Matter", "description": ""},
            format="json",
        )
        second = client.post(
            "/api/courses/",
            {"title": "Energy", "description": ""},
            format="json",
        )

        renamed = client.patch(
            f"/api/courses/{second.data['id']}/",
            {"title": " matter "},
            format="json",
        )

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_201_CREATED)
        self.assertEqual(renamed.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(CourseGroup.objects.get(pk=second.data["id"]).title, "Energy")
