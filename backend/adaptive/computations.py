#INNER LOOP: what one answer tells us -- grading, BKT and the cold-start baseline
#(called by apply_answer in services.py; nothing here decides what the student gets next)

import logging
import math
from django.utils import timezone

from .models import ConceptMastery, StudentBaseline

logger = logging.getLogger(__name__)

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

def predict_may_be_correct(prior, guess, slip): #probability of correct answer before seeing question; prior is da current mastery
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

