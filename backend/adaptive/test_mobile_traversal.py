"""Record what the mobile app actually receives, so its traversal can be replayed.

The engine's own rulings are covered by ``test_path_mode.py``. What was never
covered is the *other* half: whether the app walks a student correctly through
the replies those rulings produce. A miss that re-served the same question id
used to leave the player frozen on a disabled card -- an engine-side test could
never have caught it, because the engine was right.

So this drives the real student API (same endpoints, same payloads as a phone)
under several answer policies and writes each run out as a JSON transcript.
``mobile-app/scripts/traversal-check.mjs`` replays those transcripts through
the app's own traversal module and asserts the student is never stranded.

Run both halves with ``mobile-app/scripts/check-traversal.mjs``.
"""

import json
import os

from django.utils import timezone
from rest_framework.test import APITestCase

from course.models import LessonVariant
from lessons.models import (
    CourseGroup,
    LearningMaterial,
    LearningObject,
    LearningObjectGroup,
    OutlineNode,
    Question,
)
from learning_path.models import ConceptPrerequisite, LearningPathStep
from question_generation.models import GeneratedQuestion
from user.models import User

from .models import Enrollment

# Where the transcripts land for the JS replay to pick up. Overridable so the
# runner can point both halves at one temp directory.
TRANSCRIPT_DIR = os.environ.get(
    "MAVIA_TRANSCRIPT_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "mobile-app", "scripts", ".transcripts"),
)

# A submission cap: a policy that never terminates is itself a finding, and an
# unbounded loop here would just hang the suite instead of reporting it.
MAX_SUBMISSIONS = 80


def _variants(obj, *, audio=True):
    """Give a learning object the simplified/elaborated ladder the real
    published courses carry, so the re-teach path has genuine content to serve
    rather than falling back to the normal version."""
    for variant, text in (
        ("SIMPLIFIED", f"{obj.title}, put simply."),
        ("ELABORATED", f"{obj.title}, explained at greater length, with an example and a second pass over the idea."),
    ):
        LessonVariant.objects.create(
            learning_object=obj,
            variant=variant,
            narration=text,
            audio_url=f"/media/audio_versions/{obj.id}-{variant.lower()}.mp3" if audio else "",
        )


def _topic(course, title, order=0):
    module = OutlineNode.objects.create(course=course, title=f"Module {order + 1}", order=order, depth=0)
    topic = OutlineNode.objects.create(
        course=course, parent=module, title=title, order=0, depth=1, published=True
    )
    return module, topic


def _concept(topic, material, label, order, *, correct="True", audio=True, with_hot=False):
    group = LearningObjectGroup.objects.create(outline_node=topic, label=label)
    obj = LearningObject.objects.create(
        material=material, group=group, title=label, content=f"{label} is taught here.", order=order,
    )
    _variants(obj, audio=audio)
    GeneratedQuestion.objects.create(
        node=obj, question_text=f"{label}: does this hold?", question_format="TF",
        correct_answer=correct, explanation="", bloom_level="remember",
        thinking_order="LOT", difficulty="easy", status="final",
    )
    if with_hot:
        GeneratedQuestion.objects.create(
            node=obj, question_text=f"{label}: why does it hold?", question_format="MCQ",
            choices={"A": "Because of structure", "B": "No reason"}, correct_answer="A",
            explanation="", bloom_level="analyze", thinking_order="HOT",
            difficulty="hard", status="final",
        )
    return group, obj


class MobileTraversalTranscripts(APITestCase):
    """Each test drives one scenario end to end and writes its transcript."""

    def setUp(self):
        self.student = User.objects.create_user(
            username="traversal-student", email="t@example.com", password="pw",
            role=User.Role.STUDENT, is_verified=True,
        )
        self.client.force_authenticate(self.student)
        os.makedirs(TRANSCRIPT_DIR, exist_ok=True)

    # -- driving ---------------------------------------------------------

    def _answer_for(self, question_id, *, correct, generated):
        """What to submit. Wrong answers are deliberately unmatchable rather
        than 'the other option', so a two-option question can't be passed by
        luck and skew a policy that meant to miss."""
        if not correct:
            return "__deliberately-wrong__"
        if generated:
            return GeneratedQuestion.objects.get(pk=question_id).correct_answer
        return Question.objects.get(pk=question_id).correct_answer

    def _run(self, name, course, topic, policy):
        """Drive the student API under `policy` and write the transcript.

        `policy(index, step_kind)` returns True to answer correctly. The
        transcript records every request/response pair exactly as the device
        would see it.
        """
        Enrollment.objects.create(student=self.student, course=course)

        lesson_res = self.client.get(f"/api/adaptive/lessons/{topic.id}/")
        self.assertEqual(lesson_res.status_code, 200, lesson_res.data)

        start_res = self.client.post("/api/adaptive/start/", {"course_id": course.id}, format="json")
        self.assertIn(start_res.status_code, (200, 201), start_res.data)
        start = start_res.data

        transcript = {
            "scenario": name,
            "lesson_package": json.loads(json.dumps(lesson_res.data, default=str)),
            "start": json.loads(json.dumps(start, default=str)),
            "steps": [],
        }

        state_id = start["learning_state"]["id"]
        ls = start["learning_state"]
        question_id = ls["current_generated_question"] or ls["current_question"]
        generated = ls["current_generated_question"] is not None

        index = 0
        while question_id is not None and index < MAX_SUBMISSIONS:
            correct = policy(index)
            answer = self._answer_for(question_id, correct=correct, generated=generated)
            res = self.client.post(
                "/api/adaptive/submit-response/",
                {"learning_state_id": state_id, "question_id": question_id, "selected_answer": answer},
                format="json",
            )
            self.assertEqual(res.status_code, 200, f"{name} step {index}: {res.data}")
            body = json.loads(json.dumps(res.data, default=str))
            transcript["steps"].append(
                {
                    "submitted": {"question_id": question_id, "selected_answer": answer,
                                  "intended_correct": correct},
                    "response": body,
                }
            )
            self.assertEqual(
                body["is_correct"], correct,
                f"{name} step {index}: grading disagreed with the intended answer",
            )
            if body["completed"]:
                question_id = None
                break
            question_id = body["next_question"]
            generated = body.get("current_step") is not None
            index += 1

        self.assertLess(index, MAX_SUBMISSIONS, f"{name}: never terminated in {MAX_SUBMISSIONS} answers")
        transcript["ended_completed"] = bool(transcript["steps"]) and transcript["steps"][-1]["response"]["completed"]

        path = os.path.join(TRANSCRIPT_DIR, f"{name}.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(transcript, handle, indent=2)
        return transcript

    # -- fixtures --------------------------------------------------------

    def _two_concepts_with_prerequisite(self, *, audio=True):
        course = CourseGroup.objects.create(title="Science 7")
        _module, topic = _topic(course, "States of Matter")
        material = LearningMaterial.objects.create(
            course=course, outline_node=topic, title="Lesson one",
            generated_json={"learning_objects_confirmed": True},
        )
        now = timezone.now()
        matter, _ = _concept(topic, material, "Matter", 0, audio=audio)
        solid, _ = _concept(topic, material, "Solid", 1, audio=audio, with_hot=True)
        LearningPathStep.objects.create(outline_node=topic, concept=matter, position=1, depth=0, published_at=now)
        LearningPathStep.objects.create(outline_node=topic, concept=solid, position=2, depth=1, published_at=now)
        ConceptPrerequisite.objects.create(
            outline_node=topic, prerequisite=matter, dependent=solid, status="accepted", source="derived",
        )
        return course, topic

    def _leaf_with_alternate(self):
        course = CourseGroup.objects.create(title="Alt course")
        _module, topic = _topic(course, "One concept, two books")
        material = LearningMaterial.objects.create(
            course=course, outline_node=topic, title="Book one",
            generated_json={"learning_objects_confirmed": True},
        )
        now = timezone.now()
        solid, _obj = _concept(topic, material, "Solid", 0)
        LearningPathStep.objects.create(outline_node=topic, concept=solid, position=1, depth=0, published_at=now)

        second = LearningMaterial.objects.create(
            course=course, outline_node=topic, title="Book two",
            generated_json={"learning_objects_confirmed": True},
        )
        alt = LearningObject.objects.create(
            material=second, group=solid, title="Solid (other book)",
            content="Another book's telling of the same idea.", order=0,
        )
        _variants(alt)
        GeneratedQuestion.objects.create(
            node=alt, question_text="Other book: does this hold?", question_format="TF",
            correct_answer="True", explanation="", bloom_level="remember",
            thinking_order="LOT", difficulty="easy", status="final",
        )
        return course, topic

    def _path_then_legacy(self):
        """A path-mode topic followed by a legacy (flat Question) topic, so the
        hand-off between the two progression modes gets walked."""
        course = CourseGroup.objects.create(title="Mixed course")
        _module, topic = _topic(course, "Path topic", order=0)
        material = LearningMaterial.objects.create(
            course=course, outline_node=topic, title="Lesson one",
            generated_json={"learning_objects_confirmed": True},
        )
        now = timezone.now()
        concept, _obj = _concept(topic, material, "Matter", 0)
        LearningPathStep.objects.create(outline_node=topic, concept=concept, position=1, depth=0, published_at=now)

        legacy_module = OutlineNode.objects.create(course=course, title="Module 2", order=1, depth=0)
        legacy_topic = OutlineNode.objects.create(
            course=course, parent=legacy_module, title="Legacy topic", order=0, depth=1, published=True,
        )
        legacy_material = LearningMaterial.objects.create(
            course=course, outline_node=legacy_topic, title="Legacy lesson",
            generated_json={"learning_objects_confirmed": True},
        )
        for order, prompt in enumerate(["Flat one?", "Flat two?"]):
            Question.objects.create(
                material=legacy_material, order=order, prompt=prompt,
                question_type=Question.Type.TRUE_FALSE,
                choices=["True", "False"], correct_answer="true",
            )
        return course, topic

    def _chain(self, length=4):
        course = CourseGroup.objects.create(title="Chain course")
        _module, topic = _topic(course, "Chain topic")
        material = LearningMaterial.objects.create(
            course=course, outline_node=topic, title="Lesson one",
            generated_json={"learning_objects_confirmed": True},
        )
        now = timezone.now()
        previous = None
        for index in range(length):
            group, _obj = _concept(topic, material, f"Concept{index + 1}", index)
            LearningPathStep.objects.create(
                outline_node=topic, concept=group, position=index + 1, depth=index, published_at=now
            )
            if previous is not None:
                ConceptPrerequisite.objects.create(
                    outline_node=topic, prerequisite=previous, dependent=group,
                    status="accepted", source="derived",
                )
            previous = group
        return course, topic

    def _split_passage(self):
        """One passage cut into "(Part 1 of 2)" / "(Part 2 of 2)", published
        through the real publishing path so the pieces merge, with the
        concept's only question on the second part -- the shape that used to
        make the engine skip the concept."""
        from learning_path.services.publishing import save_learning_path

        course = CourseGroup.objects.create(title="Split course")
        _module, topic = _topic(course, "Reproduction")
        material = LearningMaterial.objects.create(
            course=course, outline_node=topic, title="Flowers",
            generated_json={
                "learning_objects_confirmed": True,
                "lesson_playlist": [
                    {"narration_item_order": 1, "audio_url": "/media/p1.mp3"},
                    {"narration_item_order": 2, "audio_url": "/media/p2.mp3"},
                    {"narration_item_order": 3, "audio_url": "/media/stamen.mp3"},
                ],
            },
        )
        objects = []
        for order, title in enumerate([
            "Reproduction in Flowering Plants (Part 1 of 2)",
            "Reproduction in Flowering Plants (Part 2 of 2)",
            "Stamen",
        ]):
            group = LearningObjectGroup.objects.create(outline_node=topic, label=title)
            obj = LearningObject.objects.create(
                material=material, group=group, title=title, content=f"{title} text.", order=order,
            )
            _variants(obj)
            objects.append(obj)
        for obj in objects[1:]:
            GeneratedQuestion.objects.create(
                node=obj, question_text=f"{obj.title}?", question_format="TF",
                correct_answer="True", explanation="", bloom_level="remember",
                thinking_order="LOT", difficulty="easy", status="final",
            )
        save_learning_path(topic)
        return course, topic

    # -- scenarios -------------------------------------------------------

    def test_all_correct(self):
        course, topic = self._two_concepts_with_prerequisite()
        t = self._run("all-correct", course, topic, lambda i: True)
        self.assertTrue(t["ended_completed"])

    def test_all_wrong(self):
        """The floor: even answering everything wrong has to terminate."""
        course, topic = self._two_concepts_with_prerequisite()
        t = self._run("all-wrong", course, topic, lambda i: False)
        self.assertTrue(t["ended_completed"])

    def test_variant_ladder_then_recover(self):
        """Miss twice (normal -> simplified -> elaborated), then get it right:
        the case that used to freeze the player."""
        course, topic = self._two_concepts_with_prerequisite()
        t = self._run("ladder-then-recover", course, topic, lambda i: i >= 2)
        reserved = [
            s for s in t["steps"]
            if not s["submitted"]["intended_correct"]
            and s["response"]["next_question"] == s["submitted"]["question_id"]
        ]
        self.assertTrue(reserved, "expected at least one same-question re-serve to replay")

    def test_no_audio_anywhere(self):
        """Every version text-only: the player has to read them aloud instead
        of waiting on a tap."""
        course, topic = self._two_concepts_with_prerequisite(audio=False)
        self._run("no-audio", course, topic, lambda i: i % 3 == 2)

    def test_chunk_switch_on_a_leaf(self):
        course, topic = self._leaf_with_alternate()
        self._run("chunk-switch", course, topic, lambda i: False)

    def test_path_then_legacy_handoff(self):
        course, topic = self._path_then_legacy()
        t = self._run("path-then-legacy", course, topic, lambda i: True)
        self.assertTrue(t["ended_completed"])

    def test_deep_chain_remediation(self):
        course, topic = self._chain(4)
        self._run("deep-chain", course, topic, lambda i: i % 4 == 3)

    def test_alternating(self):
        course, topic = self._two_concepts_with_prerequisite()
        self._run("alternating", course, topic, lambda i: i % 2 == 0)

    def test_split_passage(self):
        """Miss twice so the re-teach rungs play across both parts, then clear."""
        course, topic = self._split_passage()
        t = self._run("split-passage", course, topic, lambda i: i >= 2)
        first = t["start"]["current_step"]
        self.assertEqual(first["position"], 1)
        self.assertEqual(len(first["versions"]["normal"]["parts"]), 2)
        self.assertTrue(t["ended_completed"])
