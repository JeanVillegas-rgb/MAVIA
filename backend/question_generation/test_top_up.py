"""Which questions fill a concept's minimum, and what a top-up asks for."""

from unittest.mock import patch

from django.test import TestCase, override_settings

from lessons.models import (
    CourseGroup,
    LearningMaterial,
    LearningObject,
    LearningObjectGroup,
    OutlineNode,
    Question,
    QuestionLearningObjectLink,
)
from lessons.services.question_workflow import concept_tier_counts

from .models import GeneratedQuestion, GenerationRun
from .services.pipeline import (
    finalize_node_questions,
    generate_questions_for_node,
    top_up_plan,
)

Status = QuestionLearningObjectLink.ReviewStatus


class _Classifier:
    def __init__(self, order):
        self.order = order

    def classify(self, text):
        bloom = "remember" if self.order == "LOT" else "analyze"
        return {"bloom_level": bloom, "thinking_order": self.order, "category": "Meaning"}


@override_settings(
    QUESTION_MIN_LOT=4, QUESTION_MIN_HOT=2, QUESTION_COUNT_LOT=5, QUESTION_COUNT_HOT=5,
    QUESTION_GENERATION_ROUNDS=3,
)
class _ConceptTestCase(TestCase):
    """A confirmed PDF with one two-object concept, and a way to pair questions to it."""

    def setUp(self):
        course = CourseGroup.objects.create(title="Biology")
        topic = OutlineNode.objects.create(course=course, title="Photosynthesis", order=0)
        self.material = LearningMaterial.objects.create(
            course=course,
            outline_node=topic,
            title="Plants",
            pdf_file="learning_materials/plants.pdf",
            status=LearningMaterial.Status.COMPLETED,
            generated_json={"learning_objects_confirmed": True},
        )
        self.group = LearningObjectGroup.objects.create(outline_node=topic, label="Chlorophyll")
        self.lead = LearningObject.objects.create(
            material=self.material, kind=LearningObject.Kind.TEXT, group=self.group,
            title="Chlorophyll", content="Chlorophyll absorbs light for the leaf.", order=0,
        )
        self.other = LearningObject.objects.create(
            material=self.material, kind=LearningObject.Kind.TEXT, group=self.group,
            title="Leaf colour", content="Leaves look green because of chlorophyll.", order=1,
        )

    def _question(self, order, source=Question.SourceType.GENERATED, status=Status.AUTO_CONFIRMED,
                  edited=False, learning_object=None):
        question = Question.objects.create(
            material=self.material,
            prompt=f"{source} {order} question {Question.objects.count()}?",
            source_type=source,
            question_type=Question.Type.TRUE_FALSE,
            choices=["True", "False"],
            correct_answer="True",
            thinking_order=order,
            bloom_level="remember" if order == "LOT" else "analyze",
            validation_status=Question.ValidationStatus.READY,
            teacher_edited=edited,
        )
        if source == Question.SourceType.GENERATED:
            # Generated rows always carry their learner-facing copy.
            question.adaptive_question = GeneratedQuestion.objects.create(
                node=learning_object or self.lead, question_text=question.prompt,
                question_format="TF", correct_answer="True", thinking_order=order,
                status="final",
            )
            question.save(update_fields=["adaptive_question"])
        QuestionLearningObjectLink.objects.create(
            question=question, learning_object=learning_object or self.lead,
            is_primary=True, review_status=status,
        )
        return question


class TopUpTests(_ConceptTestCase):
    def test_generated_and_teacher_written_questions_count(self):
        self._question("LOT")
        self._question("HOT", source=Question.SourceType.MANUAL, status=Status.TEACHER_CONFIRMED)

        self.assertEqual(concept_tier_counts(self.lead), {"LOT": 1, "HOT": 1})

    def test_a_printed_question_counts_only_once_edited_and_confirmed(self):
        self._question("LOT", source=Question.SourceType.PDF, status=Status.TEACHER_CONFIRMED)
        self._question("LOT", source=Question.SourceType.PDF, status=Status.AUTO_CONFIRMED, edited=True)
        self._question("HOT", source=Question.SourceType.PDF, status=Status.PENDING_REVIEW, edited=True)
        self.assertEqual(concept_tier_counts(self.lead), {"LOT": 0, "HOT": 0})

        self._question("HOT", source=Question.SourceType.PDF, status=Status.TEACHER_CONFIRMED, edited=True)
        self.assertEqual(concept_tier_counts(self.lead), {"LOT": 0, "HOT": 1})

    def test_counts_cover_every_object_of_the_concept(self):
        self._question("LOT", learning_object=self.other)

        self.assertEqual(concept_tier_counts(self.lead)["LOT"], 1)

    def test_top_up_asks_only_for_the_short_tier_and_fills_it_to_the_target(self):
        for _ in range(9):
            self._question("LOT")
        self._question("HOT")

        orders, caps = top_up_plan(self.lead)

        self.assertEqual(orders, ["HOT"])
        self.assertEqual(caps, {"LOT": 0, "HOT": 4})

    def test_a_concept_at_its_minimum_gets_no_model_call(self):
        for _ in range(4):
            self._question("LOT")
        for _ in range(2):
            self._question("HOT")

        with patch("question_generation.services.pipeline._draft_questions_for_node") as draft:
            kept = generate_questions_for_node(self.lead, _Classifier("LOT"), append=True)

        draft.assert_not_called()
        self.assertEqual(kept, [])

    def test_a_top_up_drafts_only_the_short_tier(self):
        for _ in range(4):
            self._question("LOT")

        with patch(
            "question_generation.services.pipeline._draft_questions_for_node", return_value=0,
        ) as draft:
            generate_questions_for_node(self.lead, _Classifier("HOT"), append=True)

        self.assertEqual(draft.call_args.kwargs["orders"], ["HOT"])

    def test_drift_into_a_full_tier_is_trimmed(self):
        for _ in range(5):
            self._question("LOT")
        for index in range(3):
            GeneratedQuestion.objects.create(
                node=self.lead, question_text=f"Asked as HOT, reads as LOT {index}?",
                question_format="TF", correct_answer="True", thinking_order="HOT", status="draft",
            )

        kept = finalize_node_questions(
            self.lead, _Classifier("LOT"), append=True, caps=top_up_plan(self.lead)[1],
        )

        self.assertEqual(kept, [])
        self.assertEqual(concept_tier_counts(self.lead)["LOT"], 5)

    def _printed_with_learner_copy(self, edited):
        printed = self._question(
            "LOT", source=Question.SourceType.PDF, status=Status.TEACHER_CONFIRMED,
            edited=edited, learning_object=self.other,
        )
        printed.adaptive_question = GeneratedQuestion.objects.create(
            node=self.other, question_text=printed.prompt, question_format="TF",
            correct_answer="True", thinking_order="LOT", status="final",
        )
        printed.save(update_fields=["adaptive_question"])
        return printed.adaptive_question

    def test_regeneration_leaves_printed_questions_on_other_objects_alone(self):
        unedited = self._printed_with_learner_copy(edited=False)
        edited = self._printed_with_learner_copy(edited=True)
        GeneratedQuestion.objects.create(
            node=self.lead, question_text="What does chlorophyll absorb?",
            question_format="TF", correct_answer="True", thinking_order="LOT", status="draft",
        )

        finalize_node_questions(self.lead, _Classifier("LOT"))

        self.assertEqual(
            set(GeneratedQuestion.objects.filter(pk__in=[unedited.pk, edited.pk]).values_list("node_id", flat=True)),
            {self.other.id},
        )
        self.assertEqual(GeneratedQuestion.objects.filter(pk__in=[unedited.pk, edited.pk]).count(), 2)


class TopicRoundTests(_ConceptTestCase):
    """One Generate click: rounds on the server, only for concepts still short."""

    def setUp(self):
        super().setUp()
        self.second_group = LearningObjectGroup.objects.create(
            outline_node=self.group.outline_node, label="Stomata",
        )
        self.second = LearningObject.objects.create(
            material=self.material, kind=LearningObject.Kind.TEXT, group=self.second_group,
            title="Stomata", content="Stomata open to let gases in.", order=2,
        )

    def _run(self, fill):
        from .services.pipeline import generate_questions_for_topic

        calls = []

        def fake_node(node, *args, **kwargs):
            calls.append(node.id)
            return fill(node)

        with patch(
            "question_generation.services.pipeline._prepare_generation",
            return_value=(None, _Classifier("LOT")),
        ), patch(
            "question_generation.services.pipeline.generate_questions_for_node",
            side_effect=fake_node,
        ):
            result = generate_questions_for_topic(
                self.group.outline_node, [self.lead, self.second],
            )
        return calls, result

    def test_rounds_stop_for_a_concept_once_it_meets_the_minimum(self):
        def fill(node):
            if node.id == self.lead.id:
                for order, count in (("LOT", 4), ("HOT", 2)):
                    for _ in range(count):
                        self._question(order)
            return []

        calls, result = self._run(fill)

        # The first concept is full after round 1; the second stays short
        # and is tried in every round.
        self.assertEqual(calls, [self.lead.id, self.second.id, self.second.id, self.second.id])
        self.assertEqual(result["still_short"], ["Stomata"])

    def test_one_failing_concept_does_not_stop_the_others(self):
        def fill(node):
            if node.id == self.lead.id:
                raise RuntimeError("model unavailable")
            return []

        calls, result = self._run(fill)

        self.assertEqual(calls.count(self.lead.id), 1)
        self.assertEqual(calls.count(self.second.id), 3)
        self.assertEqual(len(result["failed"]), 1)

    def test_a_topic_already_at_its_minimum_asks_nothing(self):
        for node, group_object in ((self.lead, self.lead), (self.second, self.second)):
            for order, count in (("LOT", 4), ("HOT", 2)):
                for _ in range(count):
                    self._question(order, learning_object=group_object)

        calls, result = self._run(lambda node: [])

        self.assertEqual(calls, [])
        self.assertEqual(result["still_short"], [])


class StartTopicGenerationTests(_ConceptTestCase):
    def setUp(self):
        super().setUp()
        from lessons.tests import authenticated_api_client

        self.client = authenticated_api_client()
        self.unclassified = LearningObjectGroup.objects.create(
            outline_node=self.group.outline_node, label="Glucose",
        )
        LearningObject.objects.create(
            material=self.material, kind=LearningObject.Kind.TEXT, group=self.unclassified,
            title="Glucose", content="Plants store energy as glucose.", order=3,
        )

    @patch("question_generation.views.assign_group_versions")
    @patch("question_generation.views.threading.Thread")
    def test_one_run_covers_every_concept_with_a_standard_version(self, thread, assignment):
        def versions(group):
            if group.id == self.group.id:
                return {"classification_complete": True, "original_selected": True,
                        "representative_id": self.lead.id}
            return {"classification_complete": False}
        assignment.side_effect = versions

        response = self.client.post(
            f"/api/generation/topics/{self.group.outline_node_id}/start/", {}, format="json",
        )

        self.assertEqual(response.status_code, 201)
        run = GenerationRun.objects.get(id=response.data["run_id"])
        self.assertEqual(run.outline_node_id, self.group.outline_node_id)
        self.assertEqual(thread.call_args.kwargs["args"], (run.id, [self.lead.id]))
        self.assertEqual(len(response.data["skipped"]), 1)

    @patch("question_generation.views.threading.Thread")
    def test_a_second_run_is_refused_while_one_is_live(self, thread):
        GenerationRun.objects.create(kind=GenerationRun.Kind.QUESTIONS, status="running")

        with patch("question_generation.views.assign_group_versions", return_value={
            "classification_complete": True, "original_selected": True,
            "representative_id": self.lead.id,
        }):
            response = self.client.post(
                f"/api/generation/topics/{self.group.outline_node_id}/start/", {}, format="json",
            )

        self.assertEqual(response.status_code, 409)
        thread.assert_not_called()
