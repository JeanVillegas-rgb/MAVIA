#TRAVERSAL: the student moving through a built package -- answering, listening, reopening
#(adaptive decides what comes next; this file applies it to the student's progress)

from django.db import transaction

from .models import StudentResponse, TopicPackageProgress
from adaptive.computations import grade
from adaptive.rulings import decide_after_listening, first_question_on_arrival, askable_questions, open_questions, segment_review, LEAVES_THE_SEGMENT
from adaptive.services import apply_answer


def apply_command(progress, command): #this is what decides what happens next after grading
    if command["action"] == "regress":
        progress.regressed_positions = progress.regressed_positions + [command["return_to_position"]]
        progress.return_to_variant = command["return_to_variant"]   #the reading they missed on -- resume picks up the ladder from here
    if command["action"] == "complete":
        progress.completed = True
    else:
        progress.current_step_position = command["next_step_position"]
        progress.current_variant = command["next_variant"]

    progress.return_to_position = command["return_to_position"]
    if progress.return_to_position is None:   #detour over (or never on one): no bookmark
        progress.return_to_variant = None
    progress.pending_question_id = command.get("next_question_id")
    progress.save()



#the answeringz to the questioningz na igrade og isavingz g????
@transaction.atomic
def submit_answer(student, topic, package, question_id, selected_answer):
    question = package.answer_key.get(str(question_id))
    if question is None:
        return None

    progress, _ = TopicPackageProgress.objects.get_or_create(student=student, topic=topic)
    is_correct = grade(question, selected_answer)
    attempt_number = StudentResponse.objects.filter(student=student, topic=topic, question_id=question_id).count() + 1

    response = StudentResponse.objects.create(
        student=student,
        topic=topic,
        concept_id=question["concept_id"],
        question_id=question_id,
        selected_answer=str(selected_answer)[:20],
        is_correct=is_correct,
        variant=progress.current_variant,
        attempt_number=attempt_number,
        question_format=question["format"],
    )

    command = apply_answer(response, package, progress, topic.course)   # adaptive decides...
    apply_command(progress, command)                                    # ...this app applies it

    return {"question_id": question_id, "is_correct": is_correct,
            "explanation": question["explanation"], "next": command}


#listen-only steps (no questions, e.g. the guide's introduction)
def continue_after_listening(student, topic, package):
    progress, _ = TopicPackageProgress.objects.get_or_create(student=student, topic=topic)
    step = next((s for s in package.steps if s["position"] == progress.current_step_position), None)
    if step is None or askable_questions(step, student, topic, package):
        return None
    command = decide_after_listening(step, package.steps, progress.return_to_position, progress.return_to_variant)
    if command["action"] in LEAVES_THE_SEGMENT:
        command["review"] = segment_review(step, student, topic, package, detour_only=command["action"] == "resume")
    # Tell the phone which question waits on the step it moves to (None: listen only),
    # exactly as an answer's command does.
    if command["next_step_position"] is not None:
        target = next(s for s in package.steps if s["position"] == command["next_step_position"])
        command["next_question_id"] = first_question_on_arrival(target, student, topic, package,
                                                                resuming=command["action"] == "resume")
    apply_command(progress, command)
    return command


def next_question_on_open(student, topic, package, progress):
    #reopening the topic: ask the question the engine last decided on, if it's still open
    step = next((s for s in package.steps if s["position"] == progress.current_step_position), None)
    if step is None or progress.completed:
        return None
    pending = progress.pending_question_id
    still_open = open_questions(step, student, topic, package) + open_questions(step, student, topic, package, reserve=True)
    if pending is not None and pending in still_open:
        return pending
    return first_question_on_arrival(step, student, topic, package, detour=progress.return_to_position is not None)
