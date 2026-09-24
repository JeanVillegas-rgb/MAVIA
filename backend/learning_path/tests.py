"""The two endpoints: the review preview and the published path.

The published path is the contract the adaptive rules read, so its shape is
pinned here, including that students never receive correct answers.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from course.models import LessonVariant
from lessons.models import CourseGroup, LearningMaterial, LearningObject, LearningObjectGroup, OutlineNode
from question_generation.models import GeneratedQuestion

from .models import ConceptPrerequisite, LearningPathStep
from .services import get_published_path


class TopicFixture(TestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Grade 1 Science")
        self.topic = OutlineNode.objects.create(course=self.course, title="Solid, Liquid and Gas", order=0, depth=0)
        self.material = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="Lesson one",
            generated_json={"learning_objects_confirmed": True},
        )
        self.objects, self.groups = {}, {}
        for order, title in enumerate(["Matter", "Solid", "Liquid"]):
            group = LearningObjectGroup.objects.create(outline_node=self.topic, label=title)
            self.objects[title] = LearningObject.objects.create(
                material=self.material, group=group, title=title,
                content=f"{title} is taught here.", order=order,
            )
            self.groups[title] = group

    def _client(self, role=None):
        client = APIClient()
        if role:
            user = get_user_model().objects.create(
                username=f"user-{role.lower()}", email=f"{role.lower()}@example.com", role=role,
            )
            client.force_authenticate(user=user)
        return client

    def _link(self, before, after, status):
        return ConceptPrerequisite.objects.create(
            outline_node=self.topic, prerequisite=self.groups[before], dependent=self.groups[after],
            status=status, source="teacher" if status in ("approved", "rejected") else "derived",
        )


class TopicPreviewTests(TopicFixture):
    def _path(self):
        # The preview is teacher/admin-gated in mavia (see views.py's
        # topic_learning_path) -- unlike unauthenticated access, which
        # PublishedPathTests below covers separately.
        body = self._client("TEACHER").get(f"/api/learning-path/topics/{self.topic.id}/").json()
        return body["paths"][0] if body["paths"] else None

    def test_one_path_for_the_whole_topic(self):
        path = self._path()

        self.assertEqual([step["title"] for step in path["steps"]], ["Matter", "Solid", "Liquid"])
        self.assertEqual(path["diagnostics"]["ordering"], "document_order")

    def test_each_step_names_the_heading_it_sits_under(self):
        """The concept map draws one branch per lesson heading."""
        LearningObject.objects.filter(title="Solid").update(section_title="Solids")

        steps = {step["title"]: step for step in self._path()["steps"]}

        # A single-object bundle keeps its own title even under a heading
        # (spec 3.5, task 7 fix round 2): the fixture's "Solid" object is
        # alone in its bundle, so the step stays "Solid" while `branch`
        # still reports the section it sits under, "Solids".
        self.assertEqual(steps["Solid"]["branch"], "Solids")
        self.assertEqual(steps["Matter"]["branch"], "")

    def test_a_topic_with_no_grouped_content_has_no_path(self):
        empty = OutlineNode.objects.create(course=self.course, title="Empty", order=1, depth=0)

        body = self._client("TEACHER").get(f"/api/learning-path/topics/{empty.id}/").json()

        self.assertEqual(body["paths"], [])

    def test_the_preview_follows_stored_links(self):
        self._link("Liquid", "Solid", "approved")

        path = self._path()

        self.assertEqual([step["title"] for step in path["steps"]], ["Matter", "Liquid", "Solid"])
        self.assertEqual(path["diagnostics"]["ordering"], "prerequisite_links")
        solid = next(step for step in path["steps"] if step["title"] == "Solid")
        self.assertEqual(solid["prerequisite_ids"], [self.objects["Liquid"].id])

    def test_pending_and_rejected_links_do_not_shape_the_preview(self):
        self._link("Liquid", "Matter", "pending")
        self._link("Solid", "Matter", "rejected")

        path = self._path()

        self.assertEqual(path["steps"][0]["title"], "Matter")
        self.assertEqual(path["edges"], [])


class PublishedPathTests(TopicFixture):
    def setUp(self):
        super().setUp()
        now = timezone.now()
        for position, title in enumerate(["Matter", "Solid", "Liquid"], start=1):
            LearningPathStep.objects.create(
                outline_node=self.topic, concept=self.groups[title], position=position,
                depth=0 if title == "Matter" else 1, published_at=now,
            )
        self._link("Matter", "Solid", "accepted")
        self._link("Matter", "Liquid", "approved")
        self._link("Solid", "Liquid", "pending")
        LessonVariant.objects.create(
            learning_object=self.objects["Solid"], variant="SIMPLIFIED", narration="Solids keep shape.",
        )
        GeneratedQuestion.objects.create(
            node=self.objects["Solid"], question_text="Does a solid keep its shape?",
            question_format="TF", correct_answer="True", explanation="Its particles are fixed.",
            bloom_level="remember", thinking_order="LOT", difficulty="easy", status="final",
        )
        GeneratedQuestion.objects.create(
            node=self.objects["Solid"], question_text="A draft", question_format="TF",
            correct_answer="True", status="draft",
        )

    def _get(self, role):
        return self._client(role).get(f"/api/learning-path/topics/{self.topic.id}/published/")

    def test_signing_in_is_required(self):
        self.assertIn(self._client().get(f"/api/learning-path/topics/{self.topic.id}/published/").status_code, (401, 403))

    def test_steps_arrive_in_saved_order_with_their_content(self):
        body = self._get("TEACHER").json()

        self.assertEqual([step["title"] for step in body["steps"]], ["Matter", "Solid", "Liquid"])
        solid = body["steps"][1]
        self.assertEqual(solid["position"], 2)
        self.assertEqual(solid["depth"], 1)
        self.assertEqual(solid["concept_id"], self.groups["Solid"].id)
        self.assertEqual(solid["versions"]["normal"]["text"], "Solid is taught here.")
        self.assertEqual(solid["versions"]["simplified"]["text"], "Solids keep shape.")
        self.assertIsNone(solid["versions"]["elaborated"])
        self.assertEqual(solid["sources"], [{"material_id": self.material.id, "title": "Lesson one"}])

    def test_only_final_questions_are_served(self):
        questions = self._get("TEACHER").json()["steps"][1]["questions"]

        self.assertEqual([question["text"] for question in questions], ["Does a solid keep its shape?"])

    def test_prerequisites_are_accepted_and_approved_links_only(self):
        steps = {step["title"]: step for step in self._get("TEACHER").json()["steps"]}

        self.assertEqual(steps["Solid"]["prerequisites"], [self.groups["Matter"].id])
        self.assertEqual(steps["Liquid"]["prerequisites"], [self.groups["Matter"].id])
        self.assertEqual(steps["Matter"]["leads_to"], [self.groups["Solid"].id, self.groups["Liquid"].id])

    def test_teachers_receive_answers(self):
        body = self._get("TEACHER").json()

        self.assertTrue(body["includes_answers"])
        self.assertEqual(body["steps"][1]["questions"][0]["correct_answer"], "True")

    def test_students_never_receive_answers(self):
        body = self._get("STUDENT").json()

        self.assertFalse(body["includes_answers"])
        question = body["steps"][1]["questions"][0]
        self.assertNotIn("correct_answer", question)
        self.assertNotIn("explanation", question)

    def test_an_unpublished_topic_has_no_published_path(self):
        LearningPathStep.objects.filter(outline_node=self.topic).delete()

        response = self._get("STUDENT")

        self.assertEqual(response.status_code, 404)
        self.assertIsNone(get_published_path(self.topic))

    def test_normal_variant_uses_the_already_generated_lesson_audio(self):
        """The "normal" variant's audio isn't synthesized separately (unlike
        simplified/elaborated, via LessonVariant) -- it's whatever the
        material's own lesson-playlist TTS pass already produced for this
        LearningObject's narration. Each playlist entry names the
        LearningObject it speaks for (see course.models.audio_clip_for).
        Regression test for the mobile "quick check comes before the lesson
        chunk" bug -- normal audio was always "" before this, so path mode
        skipped straight to questions for every concept's first attempt."""
        self.material.generated_json = {
            **self.material.generated_json,
            "lesson_audio_generated": True,
            "lesson_playlist": [
                {"learning_object_id": self.objects[title].id, "audio_url": f"/media/audio_lessons/{title.lower()}.mp3"}
                for title in ("Matter", "Solid", "Liquid")
            ],
        }
        self.material.save()

        body = self._get("TEACHER").json()

        by_title = {step["title"]: step for step in body["steps"]}
        self.assertEqual(by_title["Matter"]["versions"]["normal"]["audio_url"], "/media/audio_lessons/matter.mp3")
        self.assertEqual(by_title["Solid"]["versions"]["normal"]["audio_url"], "/media/audio_lessons/solid.mp3")
        self.assertEqual(by_title["Liquid"]["versions"]["normal"]["audio_url"], "/media/audio_lessons/liquid.mp3")

    def test_normal_audio_from_a_playlist_written_before_objects_were_named(self):
        """Materials processed before playlist entries named their object keep
        their recordings: those entries are matched by position instead."""
        self.material.generated_json = {
            **self.material.generated_json,
            "lesson_audio_generated": True,
            "lesson_playlist": [
                {"narration_item_order": 1, "audio_url": "/media/audio_lessons/matter.mp3"},
                {"narration_item_order": 2, "audio_url": "/media/audio_lessons/solid.mp3"},
                {"narration_item_order": 3, "audio_url": "/media/audio_lessons/liquid.mp3"},
            ],
        }
        self.material.save()

        body = self._get("TEACHER").json()

        by_title = {step["title"]: step for step in body["steps"]}
        self.assertEqual(by_title["Solid"]["versions"]["normal"]["audio_url"], "/media/audio_lessons/solid.mp3")
        self.assertEqual(by_title["Solid"]["versions"]["normal"]["parts"][0]["audio_url"], "/media/audio_lessons/solid.mp3")

    def test_normal_variant_audio_is_blank_when_the_playlist_has_no_match(self):
        body = self._get("TEACHER").json()

        for step in body["steps"]:
            self.assertEqual(step["versions"]["normal"]["audio_url"], "")

    def test_the_python_function_and_the_api_agree(self):
        body = self._get("TEACHER").json()

        self.assertEqual(
            [step["concept_id"] for step in body["steps"]],
            [step["concept_id"] for step in get_published_path(self.topic)["steps"]],
        )

    def test_a_step_never_serves_more_than_one_lot_and_one_hot(self):
        """Question generation isn't guaranteed to cap itself at one final
        question per thinking_order per node -- seen on real published data
        (3-4 final rows on one concept). A step is one assessment, not a
        quiz bank: cap to the earliest LOT and earliest HOT, LOT first."""
        GeneratedQuestion.objects.create(
            node=self.objects["Solid"], question_text="Second LOT (should be dropped)",
            question_format="TF", correct_answer="True", thinking_order="LOT",
            bloom_level="remember", difficulty="easy", status="final",
        )
        GeneratedQuestion.objects.create(
            node=self.objects["Solid"], question_text="A HOT question",
            question_format="TF", correct_answer="False", thinking_order="HOT",
            bloom_level="analyze", difficulty="hard", status="final",
        )
        GeneratedQuestion.objects.create(
            node=self.objects["Solid"], question_text="Second HOT (should be dropped)",
            question_format="TF", correct_answer="True", thinking_order="HOT",
            bloom_level="analyze", difficulty="hard", status="final",
        )

        solid = next(step for step in self._get("TEACHER").json()["steps"] if step["title"] == "Solid")

        self.assertEqual(
            [(q["text"], q["thinking_order"]) for q in solid["questions"]],
            [("Does a solid keep its shape?", "LOT"), ("A HOT question", "HOT")],
        )


class TeacherLinkTests(TopicFixture):
    def setUp(self):
        super().setUp()
        self.teacher = self._client("TEACHER")
        self.base = f"/api/learning-path/topics/{self.topic.id}"

    def _add(self, before, after, client=None):
        return (client or self.teacher).post(
            f"{self.base}/links/",
            {"prerequisite_concept_id": self.groups[before].id, "dependent_concept_id": self.groups[after].id},
            format="json",
        )

    def _steps(self, response):
        return {step["title"]: step for step in response.json()["paths"][0]["steps"]}

    def test_a_teacher_can_add_a_link_and_the_preview_follows_it(self):
        response = self._add("Liquid", "Solid")

        self.assertEqual(response.status_code, 201, response.json())
        steps = self._steps(response)
        self.assertEqual([t for t in steps], ["Matter", "Liquid", "Solid"])
        self.assertEqual([p["title"] for p in steps["Solid"]["prerequisites"]], ["Liquid"])
        self.assertEqual(steps["Solid"]["prerequisites"][0]["status"], "approved")

    def test_students_cannot_change_links(self):
        response = self._add("Liquid", "Solid", client=self._client("STUDENT"))

        self.assertEqual(response.status_code, 403)
        self.assertFalse(ConceptPrerequisite.objects.exists())

    def test_a_link_that_would_loop_is_refused_and_named(self):
        self._add("Matter", "Solid")
        self._add("Solid", "Liquid")

        response = self._add("Liquid", "Matter")

        self.assertEqual(response.status_code, 400)
        self.assertIn("loop", response.json()["detail"])
        self.assertIn("Matter", response.json()["detail"])
        self.assertFalse(ConceptPrerequisite.objects.filter(prerequisite=self.groups["Liquid"]).exists())

    def test_a_concept_cannot_need_itself(self):
        response = self._add("Solid", "Solid")

        self.assertEqual(response.status_code, 400)

    def test_approving_a_suggestion_makes_it_shape_the_order(self):
        link = self._link("Liquid", "Solid", "pending")
        before = self._steps(self.teacher.get(f"{self.base}/"))
        self.assertEqual([s["title"] for s in before["Solid"]["suggestions"]], ["Liquid"])

        response = self.teacher.post(f"{self.base}/links/{link.id}/decision/", {"status": "approved"}, format="json")

        steps = self._steps(response)
        self.assertEqual(list(steps), ["Matter", "Liquid", "Solid"])
        self.assertEqual(steps["Solid"]["suggestions"], [])

    def test_removing_a_link_rejects_it_so_publishing_never_brings_it_back(self):
        from unittest.mock import patch

        from .services import publishing

        link = self._link("Matter", "Solid", "accepted")
        response = self.teacher.post(f"{self.base}/links/{link.id}/decision/", {"status": "rejected"}, format="json")
        self.assertEqual(self._steps(response)["Solid"]["prerequisites"], [])

        concepts = {c.title: c for c in publishing.concepts_for_topic(self.topic)}
        rerun = [{
            "prerequisite": concepts["Matter"], "dependent": concepts["Solid"], "verdict": "accepted",
            "votes": {}, "cross_section": False,
        }]
        with patch.object(publishing.criteria, "decide_pairs", return_value=rerun):
            publishing.publish_learning_path(self.topic)

        link.refresh_from_db()
        self.assertEqual(link.status, "rejected")

    def test_changes_after_a_publish_are_flagged_until_the_next_one(self):
        from unittest.mock import patch

        from .services import publishing

        with patch.object(publishing.criteria, "decide_pairs", return_value=[]):
            publishing.publish_learning_path(self.topic)
        self.assertFalse(self.teacher.get(f"{self.base}/").json()["paths"][0]["diagnostics"]["changed_since_publish"])

        response = self._add("Liquid", "Solid")
        self.assertTrue(response.json()["paths"][0]["diagnostics"]["changed_since_publish"])
        saved = list(LearningPathStep.objects.filter(outline_node=self.topic).values_list("concept__label", flat=True))
        self.assertEqual(saved, ["Matter", "Solid", "Liquid"])

        with patch.object(publishing.criteria, "decide_pairs", return_value=[]):
            publishing.publish_learning_path(self.topic)
        self.assertFalse(self.teacher.get(f"{self.base}/").json()["paths"][0]["diagnostics"]["changed_since_publish"])


class SplitPassageTests(TestCase):
    """A passage the chunker cut into "(Part 1 of 2)" pieces is one concept.

    Publishing merges the pieces, but a ``LearningPathStep`` records only the
    first piece's group. The published path used to read just that group, so
    the later parts' narration never reached a student -- and when question
    generation put the concept's questions on a later part, the step had none
    and the engine skipped the whole concept. Seen on "Reproduction Among
    Flowering Plants": the lesson opened at its third chunk.
    """

    def setUp(self):
        from .services.publishing import save_learning_path

        course = CourseGroup.objects.create(title="Grade 5 Science")
        self.topic = OutlineNode.objects.create(course=course, title="Reproduction", order=0, depth=0)
        self.material = LearningMaterial.objects.create(
            course=course, outline_node=self.topic, title="Flowers",
            generated_json={
                "learning_objects_confirmed": True,
            },
        )
        self.parts = []
        for order, (title, content) in enumerate([
            ("Reproduction in Flowering Plants (Part 1 of 2)", "Flowering plants reproduce sexually."),
            ("Reproduction in Flowering Plants (Part 2 of 2)", "A flower holds male and female parts."),
            ("Stamen", "The stamen makes pollen."),
        ]):
            group = LearningObjectGroup.objects.create(outline_node=self.topic, label=title)
            obj = LearningObject.objects.create(
                material=self.material, group=group, title=title, content=content, order=order,
            )
            LessonVariant.objects.create(learning_object=obj, variant="SIMPLIFIED", narration=f"Simply: {content}")
            self.parts.append(obj)
        self.material.generated_json = {
            **self.material.generated_json,
            "lesson_audio_generated": True,
            "lesson_playlist": [
                {"learning_object_id": obj.id, "audio_url": url}
                for obj, url in zip(self.parts, ("/media/part-1.mp3", "/media/part-2.mp3", "/media/stamen.mp3"))
            ],
        }
        self.material.save()
        # Generation put this concept's only question on the *second* part.
        self.question = GeneratedQuestion.objects.create(
            node=self.parts[1], question_text="Can one flower hold male and female parts?",
            question_format="TF", correct_answer="True", bloom_level="remember",
            thinking_order="LOT", difficulty="easy", status="final",
        )
        GeneratedQuestion.objects.create(
            node=self.parts[2], question_text="Does the stamen make pollen?",
            question_format="TF", correct_answer="True", bloom_level="remember",
            thinking_order="LOT", difficulty="easy", status="final",
        )
        save_learning_path(self.topic)
        self.path = get_published_path(self.topic, include_answers=False)

    def test_the_split_passage_is_one_step_not_two(self):
        self.assertEqual(
            [step["title"] for step in self.path["steps"]],
            ["Reproduction in Flowering Plants", "Stamen"],
        )

    def test_every_part_is_narrated_in_reading_order_with_its_own_recording(self):
        normal = self.path["steps"][0]["versions"]["normal"]

        self.assertEqual(
            normal["parts"],
            [
                {"text": "Flowering plants reproduce sexually.", "audio_url": "/media/part-1.mp3"},
                {"text": "A flower holds male and female parts.", "audio_url": "/media/part-2.mp3"},
            ],
        )
        # No single file covers both parts, so the legacy field stays empty
        # rather than pointing at a recording of half the text.
        self.assertEqual(normal["audio_url"], "")
        self.assertIn("A flower holds male and female parts.", normal["text"])

    def test_a_rung_is_offered_only_when_every_part_has_it(self):
        simplified = self.path["steps"][0]["versions"]["simplified"]
        self.assertEqual(len(simplified["parts"]), 2)

        LessonVariant.objects.filter(learning_object=self.parts[1], variant="SIMPLIFIED").delete()
        path = get_published_path(self.topic, include_answers=False)
        self.assertIsNone(path["steps"][0]["versions"]["simplified"])

    def test_questions_on_a_later_part_belong_to_the_concept(self):
        self.assertEqual([q["id"] for q in self.path["steps"][0]["questions"]], [self.question.id])

    def test_a_later_part_is_not_mistaken_for_another_pdf_s_alternate(self):
        self.assertEqual(self.path["steps"][0]["alternates"], [])

    def test_a_single_chunk_keeps_its_own_recording(self):
        stamen = self.path["steps"][1]["versions"]["normal"]
        self.assertEqual(stamen["audio_url"], "/media/stamen.mp3")
        self.assertEqual(len(stamen["parts"]), 1)

    def test_the_engine_starts_on_the_split_concept_instead_of_skipping_it(self):
        from adaptive.services import resolve_path_start

        _path, step, question = resolve_path_start(self.topic)
        self.assertEqual(step["position"], 1)
        self.assertEqual(question["id"], self.question.id)
