"""The mobile audio course package and the calibration-phase engine behind it.

get_published_path is replaced with a small fixed path, so these tests exercise
this app and adaptive (package building, grading, BKT, the cold-start baseline,
the ladder and the endpoints) without needing real course content.
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.utils import timezone
from rest_framework.test import APITestCase

from adaptive.models import ConceptMastery, Decision, Enrollment, StudentBaseline
from adaptive.services import grade, margin_of_error
from adaptive_config.models import AdaptiveConfig
from lessons.models import CourseGroup, OutlineNode
from lessons.services.audio_generator import question_audio_url, question_spoken_text
from user.models import User

from .models import StudentResponse, TopicPackage, TopicPackageProgress
from .services import audio_for_topic_questions

INTRO, SOLIDS, COMPARING = 101, 102, 103   # concept ids


def _version(*names):
    return {"text": " ".join(names), "audio_url": "",
            "parts": [{"text": n, "audio_url": f"/media/{n}.mp3"} for n in names]}


def _question(qid, fmt="MCQ", order="LOT", correct="a"):
    return {"id": qid, "text": f"Q{qid}?", "format": fmt,
            "choices": None if fmt == "TF" else ["Right", "Wrong", "Nope", "No"],
            "bloom_level": "Remember", "thinking_order": order, "category": "recall",
            "correct_answer": correct, "explanation": f"Because {qid}."}


def fake_path(node, include_answers=True):
    """Three concepts: a listen-only intro, Solids, and Comparing (needs Solids)."""
    return {
        "topic": {"id": node.id, "title": node.title},
        "steps": [
            {"position": 1, "concept_id": INTRO, "prerequisites": [], "alternates": [], "questions": [],
             "versions": {"standard": _version("intro"), "simplified": None, "elaborated": None}},
            {"position": 2, "concept_id": SOLIDS, "prerequisites": [], "alternates": [],
             "questions": [_question(1), _question(2, fmt="TF", order="HOT", correct="True")],
             "versions": {"standard": _version("solids1", "solids2"), "simplified": _version("solids_easy"),
                          "elaborated": None}},
            {"position": 3, "concept_id": COMPARING, "prerequisites": [SOLIDS], "alternates": [],
             "questions": [_question(3)],
             "versions": {"standard": _version("compare"), "simplified": _version("compare_easy"),
                          "elaborated": _version("compare_more")}},
        ],
    }


@patch("mobile_course_package.services.get_published_path", side_effect=fake_path)
class MobilePackageFlowTests(APITestCase):
    def setUp(self):
        self.student = User.objects.create_user(username="stu", password="pw", role=User.Role.STUDENT, is_verified=True)
        self.teacher = User.objects.create_user(username="teach", password="pw", role=User.Role.TEACHER, is_verified=True)
        self.course = CourseGroup.objects.create(title="Grade 1: Science")
        module = OutlineNode.objects.create(course=self.course, title="Properties of Matter", order=0, depth=0)
        self.topic = OutlineNode.objects.create(
            course=self.course, parent=module, title="Solid, Liquid and Gas", order=0, depth=1,
            published=True, published_at=timezone.now(),
        )
        Enrollment.objects.create(student=self.student, course=self.course)
        self.client.force_authenticate(self.student)

    # -- helpers ------------------------------------------------------------

    def open_topic(self):
        return self.client.get(f"/api/mobile/topics/{self.topic.id}/")

    def answer(self, question_id, selected):
        return self.client.post("/api/mobile/answers/", {
            "topic_id": self.topic.id, "question_id": question_id, "selected_answer": selected,
        }, format="json")

    def progress(self):
        return TopicPackageProgress.objects.get(student=self.student, topic=self.topic)

    # -- the package --------------------------------------------------------

    def test_package_is_audio_only_and_hides_answers(self, _path):
        response = self.open_topic()
        self.assertEqual(response.status_code, 200)
        solids = response.data["steps"][1]
        self.assertEqual(solids["versions"], {"standard": ["/media/solids1.mp3", "/media/solids2.mp3"],
                                              "simplified": ["/media/solids_easy.mp3"]})
        self.assertNotIn("correct_answer", solids["questions"][0])
        self.assertNotIn("explanation", solids["questions"][0])
        self.assertNotIn("answer_key", response.data)
        self.assertEqual(response.data["progress"]["current_step_position"], 1)
        self.assertIsNone(response.data["next_question_id"])     # the intro: listen only

    def test_package_is_built_once_and_rebuilt_after_a_republish(self, path):
        self.open_topic()
        self.open_topic()
        self.assertEqual(path.call_count, 1)

        # Strictly later: Windows' clock ticks every ~15 ms, so a second now()
        # can equal the first, which is no republish at all.
        self.topic.published_at = self.topic.published_at + timedelta(seconds=1)
        self.topic.save()
        self.open_topic()
        self.assertEqual(path.call_count, 2)
        self.assertEqual(TopicPackage.objects.count(), 1)

    def test_not_enrolled_and_teachers_are_refused(self, _path):
        Enrollment.objects.all().delete()
        self.assertEqual(self.open_topic().status_code, 403)
        self.client.force_authenticate(self.teacher)
        self.assertEqual(self.open_topic().status_code, 403)

    def test_course_list_and_outline(self, _path):
        courses = self.client.get("/api/mobile/my-courses/")
        self.assertEqual(courses.data, [{"id": self.course.id, "title": "Grade 1: Science",
                                         "topic_count": 1, "completed_topic_count": 0}])
        outline = self.client.get(f"/api/mobile/courses/{self.course.id}/")
        self.assertEqual(outline.data["modules"][0]["topics"], [{"id": self.topic.id, "title": "Solid, Liquid and Gas"}])

    # -- listen-only steps --------------------------------------------------

    def test_intro_without_questions_is_continued_past(self, _path):
        self.open_topic()
        response = self.client.post(f"/api/mobile/topics/{self.topic.id}/continue/")
        self.assertEqual(response.data["next"]["action"], "advance")
        self.assertEqual(response.data["next"]["next_question_id"], 1)   # Solids' first question waits
        self.assertEqual(self.progress().current_step_position, 2)
        # Solids has questions: continuing past it is refused
        self.assertEqual(self.client.post(f"/api/mobile/topics/{self.topic.id}/continue/").status_code, 409)

    # -- cold start ---------------------------------------------------------

    def test_a_stranger_starts_on_population_defaults(self, _path):
        config = AdaptiveConfig.load()
        self.open_topic()
        self.client.post(f"/api/mobile/topics/{self.topic.id}/continue/")
        self.answer(1, "a")

        mastery = ConceptMastery.objects.get(student=self.student, concept_id=SOLIDS)
        decision = Decision.objects.get()
        self.assertAlmostEqual(decision.mastery_before, config.starting_mastery)          # started at population L0
        self.assertAlmostEqual(decision.predicted_correct,
                               0.30 * 0.90 + 0.70 * config.p_guess)                       # BKT predict step
        self.assertGreater(mastery.mastery_score, decision.mastery_before)                 # right answer raised it

        baseline = StudentBaseline.objects.get(student=self.student, course=self.course)
        self.assertEqual(baseline.total_responses_count, 1)
        self.assertEqual(baseline.first_attempts_count, 1)
        self.assertFalse(baseline.calibrated)

    def test_true_false_uses_its_own_guess_rate(self, _path):
        self.open_topic()
        self.client.post(f"/api/mobile/topics/{self.topic.id}/continue/")
        self.answer(1, "a")
        self.answer(2, "True")
        self.assertEqual(Decision.objects.order_by("id").last().p_guess_used, AdaptiveConfig.load().p_guess_true_false)

    def test_a_re_ask_is_scored_as_easier_to_guess(self, _path):
        self.open_topic()
        self.client.post(f"/api/mobile/topics/{self.topic.id}/continue/")
        self.answer(1, "b")                                      # first try: the configured guess rate
        self.answer(1, "a")                                      # re-asked after the re-teach: 1 of 3 picks left
        guesses = list(Decision.objects.order_by("id").values_list("p_guess_used", flat=True))
        self.assertAlmostEqual(guesses[0], AdaptiveConfig.load().p_guess)
        self.assertAlmostEqual(guesses[1], 1 / 3)

    def test_baseline_calibrates_once_the_margin_is_small_enough(self, _path):
        self.assertGreater(margin_of_error(0.7, 10), 0.15)
        self.assertLessEqual(margin_of_error(0.7, 36), 0.15)

    # -- the ladder (fixed, no score gating) ----------------------------------

    def test_correct_answers_walk_the_step_then_advance(self, _path):
        self.open_topic()
        self.client.post(f"/api/mobile/topics/{self.topic.id}/continue/")

        first = self.answer(1, "A").data                       # letter, any case
        self.assertTrue(first["is_correct"])
        self.assertEqual(first["next"]["action"], "next_question")
        self.assertEqual(first["next"]["next_question_id"], 2)

        second = self.answer(2, "true").data
        self.assertEqual(second["next"]["action"], "advance")
        self.assertEqual(second["next"]["next_question_id"], 3)
        self.assertTrue(second["next"]["play_audio"])
        self.assertEqual(self.progress().current_step_position, 3)

    def test_misses_escalate_then_regress_then_resume(self, _path):
        self.open_topic()
        self.client.post(f"/api/mobile/topics/{self.topic.id}/continue/")
        self.answer(1, "a")
        self.answer(2, "True")                                  # Solids done -> Comparing (position 3)

        # Never re-asked straight after a miss (that would let the student
        # eliminate the wrong pick): every miss re-teaches first.
        actions = [self.answer(3, "b").data["next"]["action"] for _ in range(3)]
        self.assertEqual(actions, ["escalate_variant", "escalate_variant", "regress"])
        self.assertNotIn("retry", actions)
        progress = self.progress()
        self.assertEqual((progress.current_step_position, progress.return_to_position), (2, 3))
        self.assertEqual(progress.regressed_positions, [3])

        # back in Solids: its questions were already right, so one correct answer resumes Comparing
        resumed = self.answer(1, "a").data["next"]
        self.assertEqual((resumed["action"], resumed["next_step_position"]), ("resume", 3))
        # the state each decision saw is logged as it was then
        detour = Decision.objects.order_by("id").last()
        self.assertEqual((detour.step_position, detour.on_detour, detour.attempt_number), (2, True, 2))
        last_miss = Decision.objects.filter(action="regress").get()
        self.assertEqual((last_miss.step_position, last_miss.misses_on_question, last_miss.on_detour,
                          last_miss.step_already_regressed, last_miss.action_probability), (3, 3, False, False, 1.0))
        self.assertIsNotNone(last_miss.prerequisite_mastery)           # Comparing needs Solids, already met
        self.assertEqual(resumed["next_question_id"], 3)        # the question that sent them on the detour, asked again
        self.assertFalse(resumed["play_audio"])                  # no reading replayed on the way back
        self.assertEqual(resumed["next_variant"], "elaborated")  # the step stays on its last reading
        self.assertIsNone(self.progress().return_to_position)

        # Back from the detour no reading is used again: Q3 missed once more, nothing else
        # is left in Comparing, so the topic completes -- with Q3 in the review
        done = self.answer(3, "b").data["next"]
        self.assertEqual(done["action"], "complete")
        self.assertEqual([item["question_id"] for item in done["review"]], [3])
        self.assertTrue(self.progress().completed)

    def test_every_answer_is_logged_with_a_decision(self, _path):
        self.open_topic()
        self.client.post(f"/api/mobile/topics/{self.topic.id}/continue/")
        self.answer(1, "b")
        self.answer(1, "a")
        self.assertEqual(StudentResponse.objects.count(), 2)
        self.assertEqual(Decision.objects.count(), 2)
        self.assertEqual(list(StudentResponse.objects.values_list("attempt_number", flat=True)), [1, 2])

    def test_unknown_question_and_bad_input(self, _path):
        self.open_topic()
        self.assertEqual(self.answer(999, "a").status_code, 404)
        bad = self.client.post("/api/mobile/answers/", {"topic_id": self.topic.id}, format="json")
        self.assertEqual(bad.status_code, 400)

    # -- True/False: a missed TF is never asked again ------------------------

    def reach_solids(self):
        self.open_topic()
        self.client.post(f"/api/mobile/topics/{self.topic.id}/continue/")

    def test_a_missed_mcq_is_asked_again_after_reteaching(self, _path):
        self.reach_solids()
        nxt = self.answer(1, "b").data["next"]
        self.assertEqual((nxt["action"], nxt["next_variant"], nxt["next_question_id"]),
                         ("escalate_variant", "simplified", 1))

    def test_a_concept_without_prerequisites_asks_its_other_questions_before_moving_on(self, _path):
        self.reach_solids()                                      # Solids: no prerequisite, readings standard + simplified
        self.answer(1, "b")                                      # re-taught in simplified, the step's last reading
        nxt = self.answer(1, "b").data["next"]                   # missed on it too, and nowhere to detour
        self.assertEqual((nxt["action"], nxt["next_step_position"], nxt["next_question_id"]),
                         ("next_question", 2, 2))                 # the step's other question is still asked
        done = self.answer(2, "True").data["next"]               # Q1 was missed on the last reading: not asked again here
        self.assertEqual((done["action"], done["next_step_position"], done["next_question_id"]), ("advance", 3, 3))
        # leaving the finished segment: what was missed, its answer and why
        self.assertEqual(done["review"], [{"question_id": 1, "question": "Q1?", "answer": "A, Right",
                                           "explanation": "Because 1."}])
        # mid-segment commands never carry a review
        self.assertEqual(nxt["review"], [])

    def test_a_missed_true_false_moves_to_a_different_question(self, _path):
        self.reach_solids()
        nxt = self.answer(2, "False").data["next"]              # Q2 is TF; Q1 is still open
        self.assertEqual((nxt["action"], nxt["next_question_id"]), ("escalate_variant", 1))

    def test_a_missed_true_false_with_nothing_left_reteaches_then_moves_on(self, _path):
        self.reach_solids()
        self.answer(1, "a")
        nxt = self.answer(2, "False").data["next"]              # the last open question, missed
        self.assertEqual((nxt["action"], nxt["next_question_id"]), ("escalate_variant", None))
        self.assertTrue(nxt["play_audio"])
        # after the re-teach is heard the step is listen-only: continue moves on
        moved = self.client.post(f"/api/mobile/topics/{self.topic.id}/continue/").data["next"]
        self.assertEqual((moved["action"], moved["next_step_position"]), ("advance", 3))
        # and the spent TF is never served again, even if asked for directly
        self.assertNotIn(2, [q for q in [self.client.get(
            f"/api/mobile/topics/{self.topic.id}/").data["next_question_id"]]])

    def test_a_detour_asks_a_fresh_reserve_question(self, _path):
        reserve = [{"id": 9, "text": "Spare?", "format": "MCQ", "choices": ["Right", "Wrong", "Nope", "No"],
                    "thinking_order": "LOT", "correct_answer": "a", "explanation": "Spare."}]
        with patch("mobile_course_package.services.reserve_questions_for",
                   side_effect=lambda concept_id, used: reserve if concept_id == SOLIDS else []):
            self.reach_solids()
            self.answer(1, "a")
            self.answer(2, "True")                               # Solids done -> Comparing
            for _ in range(2):
                self.answer(3, "b")
            detour = self.answer(3, "b").data["next"]
            # the prerequisite's own questions were answered: a spare it never saw is asked instead
            self.assertEqual((detour["action"], detour["next_step_position"], detour["next_question_id"]), ("regress", 2, 9))
            back = self.answer(9, "a").data["next"]
            self.assertEqual((back["action"], back["next_step_position"], back["next_question_id"]), ("resume", 3, 3))
            self.assertEqual(back["review"], [])                 # nothing missed on the detour
            self.assertTrue(Decision.objects.order_by("id").last().on_detour)

    def test_a_missed_detour_question_goes_straight_back(self, _path):
        reserve = [{"id": 9, "text": "Spare?", "format": "MCQ", "choices": ["Right", "Wrong", "Nope", "No"],
                    "thinking_order": "LOT", "correct_answer": "a", "explanation": "Spare."}]
        with patch("mobile_course_package.services.reserve_questions_for",
                   side_effect=lambda concept_id, used: reserve if concept_id == SOLIDS else []):
            self.reach_solids()
            self.answer(1, "a")
            self.answer(2, "True")
            for _ in range(3):
                self.answer(3, "b")                              # -> detour to Solids, fresh Q9
            back = self.answer(9, "b").data["next"]              # missed: no re-teach on a detour
            self.assertEqual((back["action"], back["next_step_position"], back["next_question_id"]), ("resume", 3, 3))
            self.assertEqual([item["question_id"] for item in back["review"]], [9])   # its answer, on the way back
            # missed again on the way back: the rules ran out, so the teacher is told
            self.assertEqual(self.answer(3, "b").data["next"]["action"], "complete")
            self.client.force_authenticate(self.teacher)
            row = self.client.get(f"/api/adaptive/courses/{self.course.id}/progress/").data["rows"][0]
            self.assertEqual([item["concept_id"] for item in row["needs_help"]], [COMPARING])

    def test_reopening_asks_the_question_the_engine_left_pending(self, _path):
        self.reach_solids()
        self.answer(1, "b")                                      # re-taught in simplified, Solids' last reading
        nxt = self.answer(1, "b").data["next"]                   # used up, no prerequisite -> Q2 is asked next
        self.assertEqual(nxt["next_question_id"], 2)
        # the app is closed and opened again: the same question waits, not the used-up Q1
        self.assertEqual(self.open_topic().data["next_question_id"], 2)

    def test_a_missed_true_false_falls_back_to_a_reserve_question(self, _path):
        reserve = [{"id": 9, "text": "Spare?", "format": "TF", "choices": None, "thinking_order": "LOT",
                    "correct_answer": "True", "explanation": "Spare."}]
        with patch("mobile_course_package.services.reserve_questions_for",
                   side_effect=lambda concept_id, used: reserve if concept_id == SOLIDS else []):
            self.reach_solids()
            self.answer(1, "a")
            nxt = self.answer(2, "False").data["next"]
            self.assertEqual((nxt["action"], nxt["next_question_id"]), ("escalate_variant", 9))
            done = self.answer(9, "True").data["next"]
            self.assertEqual(done["action"], "advance")

    # -- teacher report -----------------------------------------------------

    def test_teacher_report_shows_the_baseline(self, _path):
        self.open_topic()
        self.client.post(f"/api/mobile/topics/{self.topic.id}/continue/")
        self.answer(1, "a")

        self.client.force_authenticate(self.teacher)
        report = self.client.get(f"/api/adaptive/courses/{self.course.id}/progress/").data
        row = report["rows"][0]
        self.assertTrue(row["started"])
        self.assertEqual(row["questions_answered"], 1)
        self.assertEqual(row["baseline"]["total_responses_count"], 1)
        self.assertFalse(row["baseline"]["calibrated"])


class GradingTests(APITestCase):
    def test_letters_text_and_true_false(self):
        mcq = {"format": "MCQ", "choices": ["Solid", "Liquid"], "correct_answer": "A"}
        self.assertTrue(grade(mcq, "a"))
        self.assertTrue(grade(mcq, " solid "))
        self.assertFalse(grade(mcq, "b"))
        tf = {"format": "TF", "choices": None, "correct_answer": "True"}
        self.assertTrue(grade(tf, "true"))
        self.assertTrue(grade(tf, "a"))
        self.assertFalse(grade(tf, "false"))


class QuestionAudioTests(APITestCase):
    def question(self, text, generated_json):
        material = SimpleNamespace(generated_json=generated_json)
        return SimpleNamespace(id=7, question_text=text, question_format="MCQ",
                               choices={"B": "Liquid", "A": "Solid"}, node=SimpleNamespace(material=material))

    def test_clip_is_served_only_for_the_text_it_reads(self):
        spoken = "Which keeps its shape? A. Solid. B. Liquid."
        clips = {"question_audio": {"7": {"text": spoken, "audio_url": "/media/q7.mp3"}}}
        self.assertEqual(question_spoken_text(self.question("Which keeps its shape?", clips)), spoken)
        self.assertEqual(question_audio_url(self.question("Which keeps its shape?", clips)), "/media/q7.mp3")
        # Edited after it was recorded: the device voice reads it instead.
        self.assertEqual(question_audio_url(self.question("Which one keeps its shape?", clips)), "")

    def test_step_lists_audio_by_question_id(self):
        questions = [{"id": 1, "audio_url": "/media/1.mp3"}, {"id": 2, "audio_url": ""}]
        self.assertEqual(audio_for_topic_questions(questions), {"1": "/media/1.mp3"})
