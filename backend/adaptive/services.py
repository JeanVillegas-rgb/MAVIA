#so ang buhaton is:
# grading 
# BKT
# cold start 
#policy
#apply_answer
# teacher report



import logging
import math
from django.db import transaction
from django.db.models import Avg, Count, Max, Q
from django.utils import timezone

from adaptive_config.models import AdaptiveConfig
from lessons.models import OutlineNode
from mobile_course_package.models import StudentResponse, TopicPackageProgress, TopicPackage


from .models import ConceptMastery, Decision, Enrollment, StudentBaseline

# prints each computation to the server terminal: [BKT], [Baseline], [Start], [Decide], [Command]
logger = logging.getLogger(__name__)

ESCALATION = {"standard": "simplified", "simplified": "elaborated", "elaborated":None}
PLAYS_AUDIO_FOR_ACTION = {"escalate_variant", "regress", "resume", "advance"}
#prior weight, calibration margin and the true/false guess come from AdaptiveConfig (weights), so an admin can tune them

#GRADing the student responses in the question segment (for each question respo sent)

LETTERS = "abcd"

def normalize(question, value):
    #turn all inputs "A" or "a" or "True" or choice text into one comparable formz
    value = str(value or "").strip().lower()
    if question["format"] == "TF":
        return {'a':'true', 'b':'false'}.get(value, value)

    choices = question["choices"] or []
    if isinstance(choices, dict):
        texts = [str(choices.get(label, "")) for label in "ABCD"]
    else:
        texts=[str(choice) for choice in choices]

    for index, text in enumerate(texts[:4]):
        if value == text.strip().lower():
            return LETTERS[index]
    return value


def grade(question, selected_answer):
    return normalize(question, selected_answer) == normalize(question, question["correct_answer"])

def guess_for(question_format, weights, choices=None, attempt=1):
    base = weights.p_guess_true_false if question_format == "TF" else weights.p_guess
    if question_format == "TF" or attempt <= 1:
        return base
    # a re-ask after a miss: the wrong picks so far can be ruled out, so a blind guess
    # gets easier -- 1 in 3 on the 2nd try, 1 in 2 from the 3rd (never assumed easier than that)
    left = max(len(choices or "ABCD") - (attempt - 1), 2)
    return max(base, 1 / left)

#THE POSSIBILITIES BEING COMPUTED FORR:
#1: knew the answer and didnt slip: prior * (1-slip)
#2: naka chamba (1-prior) * guess
#3 knew the answer but slipped: prior * slip
#4 didnt know and didnt guess right: (1-prior) * (1-guess)


def predict_may_be_correct(prior, guess, slip): #probability of correct answer before seeing question
    return prior * (1-slip) + (1-prior) * guess

def post_answer(prior, is_correct, guess, slip): #probability that they know the concept given the answer (bali % na trot na kaibaw siya sa iya gitubag)
    p_correct = predict_may_be_correct(prior, guess, slip)
    if is_correct:
        return prior * (1-slip) / p_correct
    return prior * slip / (1 - p_correct)

def learning_transition(known, learn, ceiling): #the probability they learned from the attempt
    return min(known+ (1-known) * learn, ceiling)

def bkt_update(prior, is_correct, guess, slip, learn, ceiling):
    prediction = predict_may_be_correct(prior, guess, slip)
    known = post_answer(prior, is_correct, guess, slip)
    return prediction, learning_transition(known, learn, ceiling)




# FOR THE COLD START
def population_ability(weights):
    start = weights.starting_mastery
    return start * (1 - weights.p_slip) + (1 - start) * weights.p_guess



def shrink_for_confidence(default, successes, n, weights):
    return (default * weights.prior_weight + successes) / (weights.prior_weight + n)


def margin_of_error(p,n): # declares that we're 95% sure the guess is off by no more than this amount (if it returns 0.15, this means we're 95% sure the students real score is within 15 points of our guess)
    #thisll shrink with more answers. p is the current guess of their score, n is how many answers theyve given, 1.96 makes the system 95% sure, if no answers then we know nada--could be off by 100 points
    return 1.96 * math.sqrt(p * (1- p) / n) if n else 1.0

def get_baseline(student, course, weights):
    baseline, _ = StudentBaseline.objects.get_or_create(
        student=student,
        course=course,
        defaults={"estimated_ability": population_ability(weights), "estimated_l0": weights.starting_mastery},
    )
    return baseline

def update_baseline(baseline, is_correct, first_answer_on_concept, weights):
    #updates after EVERY answer; the L0 part below only counts the first answer on each concept
    baseline.total_responses_count += 1
    baseline.total_correct_count += int(is_correct)
    baseline.estimated_ability = shrink_for_confidence(
        population_ability(weights), baseline.total_correct_count, baseline.total_responses_count, weights
    )

    if first_answer_on_concept:
        #mix possibility of knowing and guessing
        baseline.first_attempts_count +=1
        baseline.first_attempts_correct_count += int(is_correct)    
        rate = shrink_for_confidence(population_ability(weights), baseline.first_attempts_correct_count, baseline.first_attempts_count, weights)
        estimated = (rate - weights.p_guess) / (1 - weights.p_slip - weights.p_guess)
        baseline.estimated_l0 = min(max(estimated, 0.01), .95)
        logger.info(
            "[Baseline] first try on a new concept: %d/%d first tries right -> rate %.3f -> L0 (%.3f - %.2f) / %.2f = %.3f (kept %.3f)  (student %s)",
            baseline.first_attempts_correct_count, baseline.first_attempts_count, rate,
            rate, weights.p_guess, 1 - weights.p_slip - weights.p_guess, estimated, baseline.estimated_l0,
            baseline.student_id,
        )

    margin = margin_of_error(baseline.estimated_ability, baseline.total_responses_count)
    if baseline.calibrated:
        status = "calibrated"
    elif margin <= weights.calibration_margin:
        baseline.calibrated = True
        baseline.calibrated_at = timezone.now()
        status = "CALIBRATED just now"
    else:
        status = f"still calibrating (needs <= {weights.calibration_margin:.2f})"
    logger.info(
        "[Baseline] %d/%d right (raw %.3f) -> ability (%.2f x %d + %d) / (%d + %d) = %.3f, margin +-%.3f -> %s  (student %s)",
        baseline.total_correct_count, baseline.total_responses_count,
        baseline.total_correct_count / baseline.total_responses_count,
        population_ability(weights), weights.prior_weight, baseline.total_correct_count,
        weights.prior_weight, baseline.total_responses_count, baseline.estimated_ability,
        margin, status, baseline.student_id,
    )
    baseline.save()


def starting_mastery(step, student, baseline):
    #if first time pa ma encounter ni student ang concept, it starts between the students baseline na weights and ila previous mastery (from previous concept)
    known = list( ConceptMastery.objects.filter(student=student, concept_id__in=step["prerequisites"]).values_list("mastery_score", flat=True))
    if not known:
        logger.info(
            "[Start] no prerequisite mastery yet -> starts at the student's L0 %.3f  (student %s, step %s, concept %s)",
            baseline.estimated_l0, student.id, step["position"], step["concept_id"],
        )
        return baseline.estimated_l0
    prerequisites_average = sum(known) / len(known)
    start = (baseline.estimated_l0 + prerequisites_average) / 2
    logger.info(
        "[Start] (L0 %.3f + prerequisites' average %.3f of %d) / 2 -> starts at %.3f  (student %s, step %s, concept %s)",
        baseline.estimated_l0, prerequisites_average, len(known), start, student.id, step["position"], step["concept_id"],
    )
    return start







#calibrate to each student (polizies) and apply the BKT to each answer, then decide what to do next


def step_at(steps, position):
    return next((s for s in steps if s["position"] == position), None)


def next_position(steps, position):
    later = sorted(s["position"] for s in steps if s["position"] > position)
    return later[0] if later else None


def nearest_prerequisite(step, steps):
    position_of = {s["concept_id"]: s["position"] for s in steps}
    earlier = [position_of[c] for c in step["prerequisites"] if c in position_of and position_of[c] < step["position"]]
    return max(earlier) if earlier else None


def command(action, position, variant, question_id=None, return_to=None):
    result = {"action": action, "next_step_position": position, "next_variant": variant,
              "next_question_id": question_id, "return_to_position": return_to,
              "play_audio": action in PLAYS_AUDIO_FOR_ACTION,
              "review": []}  # filled in when the student leaves a finished segment (see segment_review)
    if action == "complete":
        logger.info("[Command] complete -> topic finished")
    else:
        if question_id:
            asks = f"Q{question_id}"
        elif action in ("advance", "regress", "resume"):
            asks = "that step's first open question"  # filled in after, by apply_answer / continue
        else:
            asks = "nothing (listen, then continue)"
        logger.info(
            "[Command] %s -> step %s on %s, ask %s%s%s",
            action, position, variant or "-", asks,
            f", back to step {return_to} after" if return_to is not None else "",
            ", plays audio" if result["play_audio"] else "",
        )
    return result


def move_on(step, steps, return_to_position):
    if return_to_position is not None: #If ordered to return to previous concept position
        return command("resume", return_to_position, "standard")
    position = next_position(steps, step["position"]) 
    if position is None: #if the position is empty then
        return command("complete", None, "")
    return command("advance", position, "standard")


def next_reading(step, variant):
    #the fuller reading to re-teach in after a miss on `variant`, or None when there is none left
    fuller = ESCALATION.get(variant)
    return fuller if fuller and fuller in step["versions"] else None


#da big thinking brain
def decide(step, steps, variant, is_correct, question_id, question_format, open_ids, reserve_ids,
           return_to_position, regressed_positions):
    # open_ids: the step's own questions the student can still be asked, in
    # order -- not answered correctly, not a True/False they already missed, and
    # not one already missed on the last reading (see askable_questions).
    # reserve_ids: the same, for the step's spare questions; they are only ever
    # a replacement for a missed True/False, never extra work. Both computed
    # AFTER this answer was recorded.
    answered = f"Q{question_id} ({question_format}) on {variant}"
    if is_correct:
        if open_ids:
            logger.info("[Decide] %s right -> %d question(s) still open in step %s, ask the next one",
                        answered, len(open_ids), step["position"])
            return command("next_question", step["position"], variant, open_ids[0], return_to_position)
        logger.info("[Decide] %s right -> nothing left to ask in step %s, move on", answered, step["position"])
        return move_on(step, steps, return_to_position)

    # NO immediate retry: a question is never re-asked until the concept has been
    # re-taught first. Asking again straight after a miss lets the student
    # eliminate the wrong pick -- an MCQ drops to 1 in 3, then 1 in 2.
    # A True/False is worse: after one miss the other answer is certain even
    # after re-teaching, so a missed TF is never asked again -- the re-teach is
    # followed by a DIFFERENT question of the concept, or by none.
    if question_format == "TF":
        replacements = open_ids or reserve_ids
        ask_next = replacements[0] if replacements else None
        logger.info("[Decide] %s wrong -> a missed True/False is spent; %d open, %d reserve -> %s",
                    answered, len(open_ids), len(reserve_ids),
                    f"after the re-teach ask Q{ask_next}" if ask_next else "nothing fair left to ask")
    else:
        ask_next = question_id

    next_variant = next_reading(step, variant)
    if next_variant:
        # ask_next None: nothing fair is left to ask -- re-teach, then the phone
        # continues past the step once the explanation has been heard.
        logger.info("[Decide] %s wrong -> re-teach: escalate %s to %s", answered, variant, next_variant)
        return command("escalate_variant", step["position"], next_variant, ask_next, return_to_position)

    prerequisite = nearest_prerequisite(step, steps)
    if prerequisite is not None and return_to_position is None and step["position"] not in regressed_positions:
        logger.info("[Decide] %s wrong, every explanation tried -> regress to prerequisite step %s, then back to step %s",
                    answered, prerequisite, step["position"])
        return command("regress", prerequisite, "standard", None, step["position"])

    if prerequisite is None:
        why = "no earlier prerequisite to review"
    elif return_to_position is not None:
        why = "already on a prerequisite detour"
    else:
        why = "this step already used its regress"

    # This question is done, but the step's OTHER questions still get asked: one
    # question missed at every reading doesn't show the whole concept is missed,
    # and the mastery score is only fair once the student had every question.
    remaining = [q for q in open_ids if q != question_id] or (reserve_ids if question_format == "TF" else [])
    if remaining:
        logger.info("[Decide] %s wrong, every explanation tried, %s -> ask the step's next question Q%s",
                    answered, why, remaining[0])
        return command("next_question", step["position"], variant, remaining[0], return_to_position)

    logger.info("[Decide] %s wrong, every explanation tried, %s, nothing else to ask -> move on", answered, why)
    return move_on(step, steps, return_to_position) # every option exhausted by the system: don't leave the student stranded--move them forward


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
    #the open questions still worth asking during this visit to the step: one missed on the
    #last reading is left out, so the step goes on to its other questions instead of stopping.
    #It comes back only when the student resumes this step after its detour, once the
    #prerequisite was reviewed -- see first_question_on_arrival.
    used_up = missed_on_last_reading(step, student, topic)
    return [q for q in open_questions(step, student, topic, package, reserve) if q not in used_up]


def first_question_on_arrival(step, student, topic, package, resuming):
    #the question waiting when the student moves to a step. Resuming the step that sent them
    #on a detour, the question they ran out of readings on is asked again -- that is what the
    #detour was for. Anywhere else a used-up question is skipped: its explanation was already
    #spoken, so asking it again would test memory, not understanding.
    if resuming:
        return first_open_question(step, student, topic, package)
    askable = askable_questions(step, student, topic, package)
    return askable[0] if askable else None


def first_open_question(step, student, topic, package):
    #which question of the step to ask first when the student arrives there (None: listen only)
    open_ids = open_questions(step, student, topic, package)
    return open_ids[0] if open_ids else None


LEAVES_THE_SEGMENT = {"advance", "resume", "complete"}  # regress leaves mid-segment: the student comes back to it


def answer_text(question):
    #the right answer, said the way the options are read: "B, Liquid" -- or "True" / "False"
    right = str(question.get("correct_answer") or "").strip()
    if question.get("format") == "TF":
        return {"a": "True", "b": "False", "true": "True", "false": "False"}.get(right.lower(), right)
    choices = question.get("choices") or []
    texts = [str(choices.get(label, "")) for label in "ABCD"] if isinstance(choices, dict) else [str(c) for c in choices]
    letter = normalize(question, right)
    if letter in LETTERS and LETTERS.index(letter) < len(texts):
        return f"{letter.upper()}, {texts[LETTERS.index(letter)]}"
    return right


def segment_review(step, student, topic, package):
    #the step's questions this student missed and never got right, each with its answer and
    #why. Heard once the segment is over -- never before, where it would give away a question
    #still to come (the step's other questions share its facts).
    answers = StudentResponse.objects.filter(student=student, topic=topic, concept_id=step["concept_id"])
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


def decide_after_listening(step, steps, return_to_position):
    #if the concept doesnt follow with a question segment--then move on
    result = move_on(step, steps, return_to_position)
    return result












 

@transaction.atomic # ALL OR NOTHING. cuz if any of the steps fail--it shouldnt go through. 
def apply_answer(response, package, progress, course):
    #Trace mastery, update the baseline, decide and log. Returns the command (katong actions) 
    #mobile_course_package applies it to the student's progress as they interact with the app

    weights = AdaptiveConfig.load()
    step = next(s for s in package.steps if s["concept_id"] == response.concept_id)
    baseline = get_baseline(response.student, course, weights)

    concept = ConceptMastery.objects.filter(student=response.student, concept_id=response.concept_id).first()
    first_answer_on_concept = concept is None
    if first_answer_on_concept: # SET THE STARTING MASTERY
        concept = ConceptMastery(
            student=response.student,
            concept_id=response.concept_id,
            mastery_score=starting_mastery(step, response.student, baseline),
        )

    question = package.answer_key.get(str(response.question_id), {})
    guess = guess_for(response.question_format, weights, question.get("choices"), response.attempt_number)
    before = concept.mastery_score
    prediction, concept.mastery_score = bkt_update(before, response.is_correct, guess, weights.p_slip, weights.p_learn, weights.mastery_ceiling)
    concept.save()
    logger.info(
        "[BKT] %s %s: predicted %.3f (G %.2f, S %.2f) -> knew it %.3f -> +learn T %.2f -> mastery %.3f -> %.3f  (student %s, step %s, Q%s)",
        response.question_format, "right" if response.is_correct else "wrong", prediction, guess, weights.p_slip,
        post_answer(before, response.is_correct, guess, weights.p_slip), weights.p_learn,
        before, concept.mastery_score, response.student_id, step["position"], response.question_id,
    )

    update_baseline(baseline, response.is_correct, first_answer_on_concept, weights)

    open_ids = askable_questions(step, response.student, response.topic, package)
    reserve_ids = askable_questions(step, response.student, response.topic, package, reserve=True)
    on_detour = progress.return_to_position is not None                      # read BEFORE the command moves the student
    step_already_regressed = step["position"] in progress.regressed_positions
    result = decide(step, package.steps, progress.current_variant, response.is_correct,
                    response.question_id, response.question_format, open_ids, reserve_ids,
                    progress.return_to_position, progress.regressed_positions)

    # Moving to another step---tell the phone which of its questions to ask first.
    if result["action"] in ("advance", "regress", "resume"):
        target = step_at(package.steps, result["next_step_position"])
        result["next_question_id"] = first_question_on_arrival(target, response.student, response.topic, package,
                                                               resuming=result["action"] == "resume")

    # Leaving a finished segment: the student hears what they missed in it, and why.
    if result["action"] in LEAVES_THE_SEGMENT:
        result["review"] = segment_review(step, response.student, response.topic, package)

    prerequisite_scores = list(ConceptMastery.objects.filter(student=response.student, concept_id__in=step["prerequisites"])
                               .values_list("mastery_score", flat=True))
    Decision.objects.create(
        response=response,
        predicted_correct=prediction,
        mastery_before=before,
        mastery_after=concept.mastery_score,
        p_guess_used=guess,
        baseline_ability=baseline.estimated_ability,
        baseline_calibrated=baseline.calibrated,
        action=result["action"],
        next_step_position=result["next_step_position"],
        next_variant=result["next_variant"],
        # the state as the engine saw it -- logging only, for phase 2
        step_position=step["position"],
        attempt_number=response.attempt_number,
        misses_on_question=StudentResponse.objects.filter(student=response.student, topic=response.topic,
                                                          question_id=response.question_id, is_correct=False).count(),
        questions_left_in_step=len(open_ids),
        prerequisite_mastery=sum(prerequisite_scores) / len(prerequisite_scores) if prerequisite_scores else None,
        on_detour=on_detour,
        step_already_regressed=step_already_regressed,
        action_probability=1.0,
    )
    return result


# ---------------------------------------------------------------------------
# teacher report (web): same response shape the Review page already reads
# ---------------------------------------------------------------------------

def course_progress_report(course):
    topics = list(OutlineNode.objects.filter(course=course, parent__isnull=False, published=True))
    topics_by_module = {}
    for topic in topics:
        topics_by_module.setdefault(topic.parent_id, set()).add(topic.id)
    total_modules = course.nodes.filter(parent__isnull=True).count()

    course_concepts = {
        step["concept_id"]
        for package in TopicPackage.objects.filter(topic__course=course)
        for step in package.steps
    }

    completed_topics, last_progress = {}, {}
    for row in TopicPackageProgress.objects.filter(topic__course=course):
        last_progress[row.student_id] = max(row.updated_at, last_progress.get(row.student_id, row.updated_at))
        if row.completed:
            completed_topics.setdefault(row.student_id, set()).add(row.topic_id)

    answers = {
        row["student"]: row
        for row in StudentResponse.objects.filter(topic__course=course)
        .values("student")
        .annotate(n=Count("id"), correct=Count("id", filter=Q(is_correct=True)), last=Max("created_at"))
    }
    mastery = {
        row["student"]: row["avg"]
        for row in ConceptMastery.objects.filter(concept_id__in=course_concepts)
        .values("student").annotate(avg=Avg("mastery_score"))
    }
    baselines = {b.student_id: b for b in StudentBaseline.objects.filter(course=course)}

    rows = []
    for enrollment in Enrollment.objects.filter(course=course).select_related("student"):
        sid = enrollment.student_id
        done = completed_topics.get(sid, set())
        answered = answers.get(sid)
        baseline = baselines.get(sid)
        activity = [t for t in (last_progress.get(sid), answered["last"] if answered else None) if t]
        rows.append({
            "enrollment_id": enrollment.id,
            "student": enrollment.student,
            "started": sid in last_progress or answered is not None,
            "mastery": round(mastery[sid], 3) if sid in mastery else None,
            "questions_answered": answered["n"] if answered else 0,
            "correct_rate": round(answered["correct"] / answered["n"], 3) if answered else None,
            "modules_completed": sum(1 for ids in topics_by_module.values() if ids and ids <= done),
            "total_modules": total_modules,
            "completed": bool(topics) and {t.id for t in topics} <= done,
            "last_activity": max(activity) if activity else None,
            "baseline": baseline,  # the model object (or None); the view serializes it
        })
    return {"course_id": course.id, "total_modules": total_modules, "content_ready": bool(topics), "rows": rows}
