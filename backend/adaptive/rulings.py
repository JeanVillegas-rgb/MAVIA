#ACTIONS: everything the tutor can do next -- re-teach, another question, detour, resume, advance, review
#(which one the student gets is decided in policy.py)
#(never reads mastery or the baseline: in the calibration phase nothing is score-gated)

import logging
from mobile_course_package.models import StudentResponse
from .computations import answer_text
logger = logging.getLogger(__name__)

ESCALATION = {"standard": "simplified", "simplified": "elaborated", "elaborated": None}
AUDIO_FOR_COMMAND = {"escalate_variant", "regress", "advance"}  #resume sets its own audio in move_on
LEAVES_THE_SEGMENT = {"advance", "resume", "complete"}  #segment as in katong quick assessment section


#HELPERS

def step_position_at(steps, position):  #inside package.steps, step_position
    for step in steps:
        if step["position"] == position:
            return step
    return None


def next_step_position(steps, position):  #the closest step after the one the student is on
    upcoming = sorted(step["position"] for step in steps if step["position"] > position)
    next_pos = upcoming[0] if upcoming else None
    return next_pos


def nearest_prerequisite(step, steps):
    concept_position = {s["concept_id"]: s["position"] for s in steps}  #basically ipares ang concept to its position sa steps
    concept_prerequisite = []
    for prereq in step["prerequisites"]:
        if prereq in concept_position and concept_position[prereq] < step["position"]:
            concept_prerequisite.append(concept_position[prereq])
    return max(concept_prerequisite) if concept_prerequisite else None


def command(action, position, variant, question_id=None, return_to=None, return_to_variant=None):
    result = {
        "action": action,
        "action_probability": 1.0,  #the rules' own pick, unless choose() in policy.py overrides it
        "action_candidates": [{"action": action, "next_step_position": position, "next_variant": variant, "probability": 1.0}],
        "next_step_position": position,
        "next_variant": variant,
        "next_question_id": question_id,
        "return_to_position": return_to,
        "return_to_variant": return_to_variant,
        "play_audio": action in AUDIO_FOR_COMMAND,
        "review": [],
    }

    #log what happening:
    if action == "complete":
        logger.info("Completion status: True. Topic is FINISHED.")
    else:
        if question_id:
            asks = f"Q{question_id}"
        elif action in ("advance", "regress", "resume"):
            asks = "The first available question in that topic segment."
        else:
            asks = "Nothing. Listen for user input before continuing."
        logger.info(
            "[COMMAND] %s -> step %s on %s, ask %s%s%s",
            action, position, variant or "-", asks,
            f", back to step {return_to} after" if return_to is not None else "",
            ", plays audio" if result["play_audio"] else "",
        )
    return result


#READING LADDER

def next_reading(step, variant):
    available_variant = ESCALATION.get(variant)
    #in the case that the pulled variant is UNAVAILABLE: the while loop is a failsafe that skips to the next variant
    while available_variant and available_variant not in step["versions"]:
        available_variant = ESCALATION.get(available_variant)
    return available_variant


def last_reading(step):
    #the top reading this step has -- failsafe for move_on when no reading was saved
    reading = "standard"
    while next_reading(step, reading):
        reading = next_reading(step, reading)
    return reading


#LEAVING A STEP

def move_on(step, steps, return_to_position, return_to_variant=None):
  
    if return_to_position is not None:  #on a detour: go back to where the student came from
        go_back = step_position_at(steps, return_to_position)
        if go_back is None:  #failsafe: that step isn't in the package anymore
            return command("resume", return_to_position, "standard")

        left_on = return_to_variant or last_reading(go_back)  #the reading they missed on before the detour
        skipped_reading = next_reading(go_back, left_on)  #the fuller reading they never got to hear, or None
        if skipped_reading:
            #regressed before the ladder was used up: the resume IS the escalation they skipped
            result = command("resume", return_to_position, skipped_reading)
            result["play_audio"] = True
        else:
            #regressed from the top reading: already heard everything, just ask
            result = command("resume", return_to_position, left_on)
            result["play_audio"] = False
        return result

    next_pos = next_step_position(steps, step["position"])
    if next_pos is None:  #no step after this one: topic finished
        return command("complete", None, "")
    return command("advance", next_pos, "standard")


def decide_after_listening(step, steps, return_to_position, return_to_variant=None):
    #a step with no questions: once the audio is heard, move on
    return move_on(step, steps, return_to_position, return_to_variant)


#QUESTION HELPERS FOR THE RULES

def question_after_miss(context):
    #a missed True/False is spent (50/50 guess) -- ask a different one; anything else is asked again
    if context.question_format != "TF":
        return context.question_id
    replacements = context.open_ids or context.reserve_ids
    return replacements[0] if replacements else None


def remaining_questions(context):
    #the step's other questions; a missed True/False can fall back to the reserves (not on a detour)
    others = [q for q in context.open_ids if q != context.question_id]
    if others:
        return others
    if context.question_format == "TF" and not context.on_detour:
        return context.reserve_ids
    return []


#THE RULES -- each one: "does this apply? if yes, here's the action" (None = doesn't apply)
#policy.py lists them in AFTER_CORRECT / AFTER_WRONG and picks between the ones that apply

def ask_next_open_question(context):
    #right answer and the step still has questions
    if context.open_ids:
        return {"action": "next_question", "position": context.step["position"], "variant": context.variant,
                "question_id": context.open_ids[0], "return_to": context.return_to_position}


def reteach(context):
    #re-teach on a fuller reading (never on a detour -- one question, then back)
    fuller = None if context.on_detour else next_reading(context.step, context.variant)
    if fuller:
        return {"action": "escalate_variant", "position": context.step["position"], "variant": fuller,
                "question_id": question_after_miss(context), "return_to": context.return_to_position}


def regress(context):
    #detour to the prerequisite (once per step, never from inside a detour)
    prerequisite = nearest_prerequisite(context.step, context.steps)
    if prerequisite is not None and not context.on_detour and context.step["position"] not in context.regressed_positions:
        return {"action": "regress", "position": prerequisite, "variant": "standard",
                "return_to": context.step["position"], "return_to_variant": context.variant}


def ask_other_question(context):
    #couldn't re-teach or detour: still ask the step's other questions
    remaining = remaining_questions(context)
    if remaining:
        return {"action": "next_question", "position": context.step["position"], "variant": context.variant,
                "question_id": remaining[0], "return_to": context.return_to_position}


#PICKING QUESTIONS (reads the student's answers from the database)

def open_questions(step, student, topic, package, reserve=False):
    concept_answers = StudentResponse.objects.filter(student=student, topic=topic, concept_id=step["concept_id"])
    correct = set(concept_answers.filter(is_correct=True).values_list("question_id", flat=True))
    missed_tf = set(concept_answers.filter(is_correct=False, question_format="TF").values_list("question_id", flat=True))
    pool = step.get("reserve_questions", []) if reserve else step["questions"]
    return [q["id"] for q in pool if q["id"] not in correct and q["id"] not in missed_tf]


def missed_on_last_reading(step, student, topic):
    #the questions of the step missed on a reading with nothing fuller after it: every explanation was tried
    last = [v for v in ESCALATION if next_reading(step, v) is None]
    wrong = StudentResponse.objects.filter(student=student, topic=topic, concept_id=step["concept_id"],
                                           is_correct=False, variant__in=last)
    return set(wrong.values_list("question_id", flat=True))


def askable_questions(step, student, topic, package, reserve=False):
    used_up = missed_on_last_reading(step, student, topic)
    return [q for q in open_questions(step, student, topic, package, reserve) if q not in used_up]


def fresh_reserve_question(step, student, topic):
    #a spare question of the step this student has never answered -- so never heard reviewed either
    answered = set(StudentResponse.objects.filter(student=student, topic=topic, concept_id=step["concept_id"])
                   .values_list("question_id", flat=True))
    return next((q["id"] for q in step.get("reserve_questions", []) if q["id"] not in answered), None)


def first_open_question(step, student, topic, package):
    #which question of the step to ask first when the student arrives there (None: listen only)
    open_ids = open_questions(step, student, topic, package)
    return open_ids[0] if open_ids else None


def first_question_on_arrival(step, student, topic, package, resuming=False, detour=False):
    if resuming:
        return first_open_question(step, student, topic, package)
    if detour:
        fresh = fresh_reserve_question(step, student, topic)
        if fresh is not None:
            return fresh
    askable = askable_questions(step, student, topic, package)
    return askable[0] if askable else None


#REVIEW

def segment_review(step, student, topic, package, detour_only=False):
    answers = StudentResponse.objects.filter(student=student, topic=topic, concept_id=step["concept_id"])
    if detour_only:
        answers = answers.filter(decision__on_detour=True)
    missed = set(answers.filter(is_correct=False).values_list("question_id", flat=True))
    right = set(answers.filter(is_correct=True).values_list("question_id", flat=True))
    review = []
    for question in step["questions"] + step.get("reserve_questions", []):
        if question["id"] not in missed or question["id"] in right:
            continue
        key = package.answer_key.get(str(question["id"]), {})
        review.append({"question_id": question["id"], "question": question["text"],
                       "answer": answer_text(key), "explanation": key.get("explanation", "")})
    if review:
        logger.info("[Review] step %s finished: %d missed question(s) explained before moving on (%s)",
                    step["position"], len(review), ", ".join(f"Q{item['question_id']}" for item in review))
    return review
