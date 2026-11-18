"""Deleting or re-reading one source must preserve unrelated bank questions."""
from django.test import TestCase
from lessons.models import CourseGroup, LearningMaterial, LearningObject, LearningObjectGroup, OutlineNode, ExtractedQuestion
from lessons.services.question_workflow import synchronize_detected_questions
from lessons.test_utils import authenticated_api_client
from question_generation.models import Question

class GeneratedQuestionSafetyTests(TestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Science")
        self.topic = OutlineNode.objects.create(course=self.course, title="Matter")
        self.material = LearningMaterial.objects.create(course=self.course, outline_node=self.topic,
            title="Lesson", status="completed", generated_json={"learning_objects_confirmed": True})
        self.objects, self.questions = [], []
        for index, title in enumerate(("Solid", "Liquid", "Gas")):
            group = LearningObjectGroup.objects.create(outline_node=self.topic, label=title)
            obj = LearningObject.objects.create(material=self.material, group=group, title=title,
                                                 content=title + " description.", order=index)
            self.objects.append(obj)
            self.questions.append(Question.objects.create(outline_node=self.topic, node=obj,
                question_text=title + " question?", question_format="TF", correct_answer="True",
                thinking_order="LOT", paired_by="auto", status="final", source="generated"))

    def assert_other_questions_intact(self):
        for row, obj in zip(self.questions[1:], self.objects[1:]):
            row.refresh_from_db()
            self.assertEqual((row.node_id, row.status, row.paired_by), (obj.id, "final", "auto"))

    def test_final_review_deletion_only_deletes_its_own_generated_questions(self):
        obj = self.objects[0]
        response = authenticated_api_client().delete(
            f"/api/courses/{self.course.id}/outline-nodes/{self.topic.id}/learning-objects/{obj.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Question.objects.filter(pk=self.questions[0].id).exists())
        self.assert_other_questions_intact()

    def test_step_one_deletion_only_deletes_its_own_generated_questions(self):
        response = authenticated_api_client().delete(
            f"/api/courses/{self.course.id}/materials/{self.material.id}/learning-objects/{self.objects[0].id}/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Question.objects.filter(pk=self.questions[0].id).exists())
        self.assert_other_questions_intact()

    def test_re_reading_pdf_preserves_generated_and_manual_questions(self):
        manual = Question.objects.create(outline_node=self.topic, source="manual",
                                          question_text="A teacher question?", status="review")
        stale = ExtractedQuestion.objects.create(material=self.material, prompt="Old printed question?")
        synchronize_detected_questions(self.material, [])
        self.assertFalse(ExtractedQuestion.objects.filter(pk=stale.pk).exists())
        self.assertTrue(Question.objects.filter(pk=manual.pk).exists())
        self.assertEqual(Question.objects.filter(source="generated").count(), 3)
        self.assert_other_questions_intact()

    def test_question_moved_by_teacher_is_not_lost_with_old_concept(self):
        row = self.questions[0]
        row.node = self.objects[1]
        row.paired_by = "teacher"
        row.teacher_edited = True
        row.save()
        self.objects[0].delete()
        row.refresh_from_db()
        self.assertEqual(row.node_id, self.objects[1].pk)
        self.assert_other_questions_intact()
