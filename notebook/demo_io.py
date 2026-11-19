# %% [markdown]
# # MAVIA demo: input -> output at every stage
# Each cell prints the INPUT a stage receives, runs your real code, and prints the OUTPUT it produces.
# Runs on a COPY of backend/db.sqlite3 (the real database is never written).
#   VS Code:   click "Run Cell" above each "# %%"  (kernel: k:\STUDIO\Milestone1-Jean\mavia\Scripts\python.exe)
#   Terminal:  k:\STUDIO\Milestone1-Jean\mavia\Scripts\python.exe k:\STUDIO\mavia\notebook\demo_io.py

# %% 0. Setup: copy the database, start Django, make a fresh student and teacher
import json
import os
import random
import sqlite3
import sys
from pathlib import Path

HERE = Path(r"k:\STUDIO\mavia\notebook")
BACKEND = HERE.parent / "backend"
COPY_DB = HERE / "demo_copy.sqlite3"
with sqlite3.connect(BACKEND / "db.sqlite3") as source, sqlite3.connect(COPY_DB) as target:
    source.backup(target)  # a fresh copy every run

os.environ["DATABASE_URL"] = "sqlite:///" + COPY_DB.as_posix()
os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
os.environ["LLM_PROVIDER"] = "ollama"
os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"  # notebooks run inside an event loop
sys.path.insert(0, str(BACKEND))

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402
from django.core.management import call_command  # noqa: E402
from django.db import connection  # noqa: E402
from django.forms.models import model_to_dict  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

assert Path(connection.settings_dict["NAME"]).resolve() == COPY_DB.resolve(), "not on the copy!"
call_command("migrate", verbosity=0)
settings.ALLOWED_HOSTS.append("testserver")  # the test client's host name
random.seed(7)  # the 85/15 draws come out the same every run


def show(label, data, limit=1400):
    """Print a dict / list / model row as indented JSON, cut short if it's long."""
    if hasattr(data, "_meta"):
        data = model_to_dict(data)
    text = json.dumps(data, indent=2, default=str, ensure_ascii=False)
    if len(text) > limit:
        text = text[:limit] + f"\n... ({len(text) - limit} more characters)"
    print(f"\n=== {label} ===\n{text}")


from adaptive import policy  # noqa: E402
from adaptive.models import ConceptMastery, Decision, Enrollment, StudentBaseline  # noqa: E402
from lessons.models import OutlineNode  # noqa: E402
from mobile_course_package.models import StudentResponse, TopicPackageProgress  # noqa: E402
from user.models import User  # noqa: E402

topic = OutlineNode.objects.get(id=122)  # "Solid, Liquid and Gas"
course = topic.course
student = User.objects.create_user(username="demo_student", password="demo", email="student@example.com",
                                   role=User.Role.STUDENT, is_verified=True)
teacher = User.objects.create_user(username="demo_teacher", password="demo", email="teacher@example.com",
                                   role=User.Role.TEACHER, is_verified=True)
Enrollment.objects.create(student=student, course=course)
phone = APIClient()
phone.force_authenticate(student)  # every request below is made as this student, like the app does
print("working on", COPY_DB.name, "| topic", topic.id, topic.title, "| student", student.username)

from unittest.mock import patch  # noqa: E402


def right_answer(question_id):
    """The correct answer from the server-side answer key (the demo plays a student who knows it)."""
    return package.answer_key[str(question_id)]["correct_answer"]


def wrong_answer(question_id):
    key = package.answer_key[str(question_id)]
    if key["format"] == "TF":
        return "false" if str(key["correct_answer"]).lower() in ("true", "a") else "true"
    return next(letter for letter in "ABCD" if letter != str(key["correct_answer"]).upper())


def answer(question_id, selected, pick=None, client=None):
    """POST an answer exactly as the app does. The policy keeps its real 85/15 odds (they show in the
    output); only the draw is fixed for the demo: the rules' own pick, or the action named by `pick`."""
    def draw(options, weights):
        wanted = [o for o in options if o["action"] == pick]
        return wanted[:1] or options[:1]

    body = {"topic_id": topic.id, "question_id": question_id, "selected_answer": selected}
    with patch.object(policy.random, "choices", side_effect=draw):
        return body, (client or phone).post("/api/mobile/answers/", body, format="json")


def progress_row(who=None):
    p = TopicPackageProgress.objects.get(student=who or student, topic=topic)
    return {f: getattr(p, f) for f in ("current_step_position", "current_variant", "return_to_position",
                                       "return_to_variant", "regressed_positions", "pending_question_id", "completed")}


# %% 1. INPUT: the published learning path  ->  OUTPUT: the audio course package
from learning_path.services import get_published_path  # noqa: E402
from mobile_course_package.services import get_package  # noqa: E402

path = get_published_path(topic)  # learning_path/services/published.py
step = path["steps"][0]
show("INPUT  published path: number of steps", len(path["steps"]))
show("INPUT  published path, step 1 (server side: answers included)", {
    "position": step["position"],
    "concept_id": step["concept_id"],
    "prerequisites": step["prerequisites"],
    "versions (audio parts per reading)": {k: len(v["parts"]) if v else 0 for k, v in step["versions"].items()},
    "questions": [{k: q[k] for k in ("id", "format", "thinking_order", "text", "correct_answer")} for q in step["questions"]],
})

package = get_package(topic)  # mobile_course_package/services.py: get_package -> build_course_package
show("OUTPUT package, step 1 (what the phone gets)", package.steps[0])
show("OUTPUT package, answer_key for its first question (server only)",
     package.answer_key[str(package.steps[0]["questions"][0]["id"])])
show("OUTPUT does any answer leak into the phone's steps?", "correct_answer" in json.dumps(package.steps))

# %% 2. INPUT: the student opens the topic  ->  OUTPUT: what the phone receives
request = f"GET /api/mobile/topics/{topic.id}/   (as {student.username})"
show("INPUT  the request", request)
opened = phone.get(f"/api/mobile/topics/{topic.id}/")  # views.py TopicPackageView.get
data = opened.json()
show("OUTPUT status code", opened.status_code)
show("OUTPUT the response (steps shortened to a count)", {
    "topic": data["topic"],
    "steps": f"{len(data['steps'])} steps, no answers",
    "progress": data["progress"],
    "next_question_id": data["next_question_id"],
})
first_question = data["next_question_id"]

# %% 3. INPUT: a RIGHT answer  ->  OUTPUT: the response and every row it saved
body, resp = answer(first_question, right_answer(first_question))
show("INPUT  POST /api/mobile/answers/", body)
show("OUTPUT the response", resp.json())
show("OUTPUT StudentResponse saved",
     StudentResponse.objects.filter(student=student).order_by("id").values(
         "concept_id", "question_id", "selected_answer", "is_correct", "variant", "attempt_number", "question_format").last())
show("OUTPUT ConceptMastery (BKT)", list(ConceptMastery.objects.filter(student=student).values("concept_id", "mastery_score")))
show("OUTPUT StudentBaseline (cold start)",
     StudentBaseline.objects.filter(student=student).values(
         "estimated_ability", "estimated_l0", "total_responses_count", "calibrated").first())
show("OUTPUT Decision (what the engine saw and chose)",
     Decision.objects.filter(response__student=student).order_by("id").values(
         "mastery_before", "predicted_correct", "mastery_after", "p_guess_used", "action", "next_step_position",
         "next_variant", "action_probability", "action_candidates").last())
show("OUTPUT TopicPackageProgress", progress_row())
next_question = resp.json()["next"]["next_question_id"]

# %% 4. INPUT: a WRONG answer  ->  OUTPUT: re-teach on a fuller reading
body, resp = answer(next_question, wrong_answer(next_question))
show("INPUT  POST /api/mobile/answers/", body)
show("OUTPUT the command (next)", resp.json()["next"])
show("OUTPUT TopicPackageProgress", progress_row())

# %% 5. INPUT: a miss on concept 2 with the early detour  ->  OUTPUT: regress + bookmark, then resume
# finish concept 1 with right answers, so the student reaches concept 2 ("Matter", which needs concept 1)
command = resp.json()["next"]
while command["action"] in ("next_question", "escalate_variant") and command["next_step_position"] == 1:
    qid = command["next_question_id"]
    command = answer(qid, right_answer(qid))[1].json()["next"]
show("reached", {"action": command["action"], "step": command["next_step_position"], "question": command["next_question_id"]})

qid = command["next_question_id"]
body, resp = answer(qid, wrong_answer(qid), pick="regress")  # the 15% draw: the early detour
show("INPUT  POST /api/mobile/answers/  (a miss on concept 2, standard reading)", body)
show("OUTPUT the command: regress, with both options and their chances", resp.json()["next"])
show("OUTPUT TopicPackageProgress: the bookmark is saved", progress_row())

detour_question = resp.json()["next"]["next_question_id"]
body, resp = answer(detour_question, right_answer(detour_question))
show("INPUT  POST /api/mobile/answers/  (the detour's question, answered right)", body)
show("OUTPUT the command: resume on the reading that was skipped, with audio", resp.json()["next"])
show("OUTPUT TopicPackageProgress: bookmark cleared", progress_row())

# %% 6. INPUT: a second student FAILS the detour  ->  OUTPUT: back to concept 2, and the concept is flagged
# (a new student: the first one already used concept 2's one detour)
student2 = User.objects.create_user(username="demo_student2", password="demo", email="student2@example.com",
                                    first_name="Second", last_name="Student", role=User.Role.STUDENT, is_verified=True)
Enrollment.objects.create(student=student2, course=course)
phone2 = APIClient()
phone2.force_authenticate(student2)

command = {"next_question_id": phone2.get(f"/api/mobile/topics/{topic.id}/").json()["next_question_id"],
           "next_step_position": 1, "action": "next_question"}
while command["next_step_position"] == 1 and command["action"] == "next_question":  # concept 1, all right
    qid = command["next_question_id"]
    command = answer(qid, right_answer(qid), client=phone2)[1].json()["next"]

qid = command["next_question_id"]
body, resp = answer(qid, wrong_answer(qid), pick="regress", client=phone2)
show("INPUT  POST /api/mobile/answers/  (student 2 misses on concept 2; the 15% draw sends them on the detour)", body)
show("OUTPUT the command: regress", {k: resp.json()["next"][k] for k in
     ("action", "action_probability", "action_candidates", "next_step_position", "next_question_id", "return_to_variant")})

detour_question = resp.json()["next"]["next_question_id"]
body, resp = answer(detour_question, wrong_answer(detour_question), client=phone2)
show("INPUT  POST /api/mobile/answers/  (the detour's question, answered WRONG)", body)
show("OUTPUT the command: straight back to concept 2, on the reading that was skipped", resp.json()["next"])
show("OUTPUT TopicPackageProgress", progress_row(student2))

# %% 7. OUTPUT: the Decision log -- one row per answer, what the engine saw and what it chose
from adaptive.services import concepts_needing_help, course_progress_report  # noqa: E402

print("\n=== OUTPUT Decision log for", student2.username, "===")
print(f"{'answer':<20}{'step':>5}{'detour':>8}{'mastery':>16}{'pred':>7}{'guess':>7}   action (chance)  [candidates]")
for d in Decision.objects.filter(response__student=student2).select_related("response").order_by("id"):
    r = d.response
    candidates = ", ".join(f"{c['action']} {c['probability']:.2f}" for c in d.action_candidates)
    print(f"{f'Q{r.question_id} ' + ('right' if r.is_correct else 'WRONG') + f' ({r.variant[:4]})':<20}"
          f"{d.step_position:>5}{('yes' if d.on_detour else 'no'):>8}"
          f"{f'{d.mastery_before:.3f}->{d.mastery_after:.3f}':>16}{d.predicted_correct:>7.3f}{d.p_guess_used:>7.2f}"
          f"   {d.action} ({d.action_probability:.2f})  [{candidates}]")
show("OUTPUT one Decision row in full (the detour decision)",
     Decision.objects.filter(response__student=student2, action="regress").values(
         "step_position", "attempt_number", "misses_on_question", "questions_left_in_step", "on_detour",
         "step_already_regressed", "mastery_before", "predicted_correct", "mastery_after", "p_guess_used",
         "baseline_ability", "baseline_calibrated", "prerequisite_mastery", "action", "next_step_position",
         "next_variant", "action_probability", "action_candidates").first())

# %% 8. INPUT: the course  ->  OUTPUT: concepts_needing_help and the teacher's progress report
show("INPUT  the course", {"id": course.id, "title": course.title})
show("OUTPUT concepts_needing_help(course)  (student id -> flagged concepts)",
     {student_id: flags for student_id, flags in concepts_needing_help(course).items() if student_id in (student.id, student2.id)})

office = APIClient()
office.force_authenticate(teacher)
show("INPUT  the request", f"GET /api/adaptive/courses/{course.id}/progress/   (as {teacher.username})")
report = office.get(f"/api/adaptive/courses/{course.id}/progress/").json()  # adaptive/views.py -> course_progress_report
show("OUTPUT report header", {k: v for k, v in report.items() if k != "rows"})
for who in (student, student2):
    row = next(r for r in report["rows"] if r["student"]["id"] == who.id)
    show(f"OUTPUT report row for {who.username}", {k: v for k, v in row.items() if k != "student"})
