from rest_framework.test import APITestCase

from lessons.models import CourseGroup, LearningMaterial, OutlineNode, Question
from user.models import User


def _course_with_content(*, topics=1, questions_per_topic=2):
    course = CourseGroup.objects.create(title="Science 7")
    module = OutlineNode.objects.create(course=course, title="Module 1", order=0, depth=0)
    made = []
    for t in range(topics):
        topic = OutlineNode.objects.create(
            course=course, parent=module, title=f"Topic {t + 1}", order=t, depth=1, published=True
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
