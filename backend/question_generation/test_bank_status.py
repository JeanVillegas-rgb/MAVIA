"""A concept's question bank follows the concept's text.

The bank is written from the whole concept and filed under its Standard lead.
When the concept's text changes it is out of date until the teacher keeps or
regenerates it; deleting the object it is filed under re-files it rather
than deleting it; and questions the teacher edited survive a regeneration.
"""

from django.test import TestCase

from lessons.models import (
    CourseGroup,
    LearningMaterial,
    LearningObject,
    LearningObjectGroup,
    OutlineNode,
    Question,
    QuestionLearningObjectLink,
)
from lessons.services.question_workflow import mirror_generated_questions
from lessons.tests import authenticated_api_client

from .models import GeneratedQuestion
from .services.bank_status import bank_out_of_date, keep_bank, out_of_date_groups, text_fingerprint
from .services.pipeline import concept_source_text, finalize_node_questions
from .tests import _StubClassifier, _draft


class BankStatusTests(TestCase):
    def setUp(self):
        self.client = authenticated_api_client()
        self.course = CourseGroup.objects.create(title="Science")
        self.node = OutlineNode.objects.create(course=self.course, title="States of matter")
        self.pdf = LearningMaterial.objects.create(
            course=self.course, outline_node=self.node, title="Lesson 1",
            status=LearningMaterial.Status.COMPLETED,
            generated_json={"learning_objects_confirmed": True},
        )
        self.group = LearningObjectGroup.objects.create(outline_node=self.node, label="Solid")
        self.part1 = self.add("Solid (Part 1 of 2)", "Solids keep their shape.", 0)
        self.part2 = self.add("Solid (Part 2 of 2)", "Their particles vibrate in place.", 1)
        self.bank = self.write_bank(self.part1)

    def add(self, title, content, order):
        return LearningObject.objects.create(
            material=self.pdf, group=self.group, title=title, content=content, order=order,
        )

    def write_bank(self, owner):
        _draft(owner, "Do solids keep their shape?")
        _draft(owner, "Do solid particles move freely?", correct_answer="False")
        return finalize_node_questions(owner, _StubClassifier({}))

    def topic_url(self, path):
        return f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/{path}"

    def test_a_fresh_bank_is_current(self):
        self.assertFalse(bank_out_of_date(self.group))

    def test_editing_any_object_puts_the_bank_out_of_date(self):
        self.part2.content = "Their particles are packed tightly."
        self.part2.save()

        self.assertTrue(bank_out_of_date(self.group))
        self.assertEqual(out_of_date_groups(self.node), [self.group])

    def test_not_judged_while_a_pdf_in_the_concept_is_a_draft(self):
        self.part2.content = "Their particles are packed tightly."
        self.part2.save()
        self.pdf.generated_json = {"learning_objects_confirmed": False}
        self.pdf.save()

        self.assertFalse(bank_out_of_date(self.group))

    def test_keep_as_is_makes_it_current_again(self):
        self.part2.content = "Their particles are packed tightly."
        self.part2.save()

        keep_bank(self.group)

        self.assertFalse(bank_out_of_date(self.group))

    def test_a_bank_written_before_the_text_was_recorded_is_not_reported(self):
        GeneratedQuestion.objects.filter(node=self.part1).update(source_text_fingerprint="")
        self.part2.content = "Their particles are packed tightly."
        self.part2.save()

        self.assertFalse(bank_out_of_date(self.group))

    def test_deleting_the_object_it_is_filed_under_refiles_it(self):
        ids = sorted(question.id for question in self.bank)

        self.client.delete(self.topic_url(f"learning-objects/{self.part1.id}/"))

        moved = GeneratedQuestion.objects.filter(status="final")
        self.assertEqual(sorted(moved.values_list("id", flat=True)), ids)
        self.assertEqual(set(moved.values_list("node_id", flat=True)), {self.part2.id})
        self.assertTrue(bank_out_of_date(self.group))
        # The teacher's list still shows them, paired to the concept.
        self.assertEqual(
            QuestionLearningObjectLink.objects.filter(
                learning_object=self.part2, method="generated_from_object",
            ).count(),
            len(ids),
        )

    def test_deleting_the_last_object_deletes_the_bank(self):
        self.client.delete(self.topic_url(f"learning-objects/{self.part2.id}/"))
        self.client.delete(self.topic_url(f"learning-objects/{self.part1.id}/"))

        self.assertFalse(GeneratedQuestion.objects.exists())

    def test_editing_a_generated_question_marks_it_edited(self):
        question = Question.objects.get(adaptive_question=self.bank[0])

        self.client.patch(
            self.topic_url(f"questions/{question.id}/"),
            {"prompt": "Does a solid keep its own shape?", "question_type": "true_false",
             "correct_answer": "True"},
            format="json",
        )

        question.refresh_from_db()
        self.assertTrue(question.teacher_edited)

    def test_regenerating_keeps_edited_questions_alongside_the_new_bank(self):
        edited = Question.objects.get(adaptive_question=self.bank[0])
        Question.objects.filter(pk=edited.pk).update(teacher_edited=True)
        self.part2.content = "Their particles are packed tightly."
        self.part2.save()

        _draft(self.part1, "Are solid particles packed tightly?")
        finalize_node_questions(self.part1, _StubClassifier({}))

        texts = set(self.part1.generated_questions.filter(status="final")
                    .values_list("question_text", flat=True))
        self.assertIn(self.bank[0].question_text, texts)
        self.assertIn("Are solid particles packed tightly?", texts)
        self.assertNotIn(self.bank[1].question_text, texts)
        self.assertTrue(Question.objects.filter(pk=edited.pk).exists())
        self.assertFalse(bank_out_of_date(self.group))


class GenerateMoreTests(TestCase):
    """"Generate more" adds a batch to the concept's questions; it never replaces."""

    setUp = BankStatusTests.setUp
    add = BankStatusTests.add
    write_bank = BankStatusTests.write_bank

    def test_a_new_batch_is_added_and_the_old_questions_stay(self):
        before = set(self.part1.generated_questions.filter(status="final").values_list("id", flat=True))

        _draft(self.part1, "Can a solid be poured?", correct_answer="False")
        finalize_node_questions(self.part1, _StubClassifier({}), append=True)

        after = set(self.part1.generated_questions.filter(status="final").values_list("id", flat=True))
        self.assertTrue(before < after)
        self.assertEqual(len(after - before), 1)

    def test_a_repeat_of_an_existing_question_is_dropped(self):
        _draft(self.part1, "Do solids keep their shape?")
        finalize_node_questions(self.part1, _StubClassifier({}), append=True)

        texts = list(self.part1.generated_questions.filter(status="final")
                     .values_list("question_text", flat=True))
        self.assertEqual(texts.count("Do solids keep their shape?"), 1)
