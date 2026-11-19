#so ang buhaton is:
# grading 
# BKT
# cold start 
#policy
#apply_answer
# teacher report

import logging
from django.db import transaction
from django.db.models import Avg, Count, Max, Q

from adaptive_config.models import AdaptiveConfig
from lessons.models import OutlineNode
from mobile_course_package.models import StudentResponse, TopicPackageProgress, TopicPackage


from .models import ConceptMastery, Decision, Enrollment, StudentBaseline

#one answer runs through both loops: the inner loop (computations) scores it, the outer loop (policy + rulings) decides
from .computations import bkt_update, get_baseline, guess_for, post_answer, starting_mastery, update_baseline
from .policy import decide
from .rulings import LEAVES_THE_SEGMENT, askable_questions, first_question_on_arrival, segment_review, step_position_at

# prints each computation to the server terminal: [BKT], [Baseline], [Start], [Decide], [Command]
logger = logging.getLogger(__name__)


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
                    progress.return_to_position, progress.regressed_positions, progress.return_to_variant)

    # Moving to another step---tell the phone which of its questions to ask first.
    if result["action"] in ("advance", "regress", "resume"):
        target = step_position_at(package.steps, result["next_step_position"])
        result["next_question_id"] = first_question_on_arrival(target, response.student, response.topic, package,
                                                               resuming=result["action"] == "resume",
                                                               detour=result["action"] == "regress")

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
        # what the policy chose from -- the chance it picked this action, and every option it had
        action_probability=result["action_probability"],
        action_candidates=result["action_candidates"],
    )
    # Leaving a finished segment: the student hears what they missed in it, and why.
    if result["action"] in LEAVES_THE_SEGMENT:
        result["review"] = segment_review(step, response.student, response.topic, package,
                                          detour_only=result["action"] == "resume")

    return result


#TEACHER FLAGGING 

def concepts_needing_help(course):
    from lessons.models import LearningObjectGroup

    flagged = {}
    detours = (Decision.objects.filter(action="regress", response__topic__course=course)
               .select_related("response", "response__topic").order_by("response_id"))
    for d in detours:
        sent = d.response
        later = list(StudentResponse.objects.filter(student_id=sent.student_id, topic_id=sent.topic_id, id__gt=sent.id)
                     .select_related("decision").order_by("id"))
        #the detour's own answers: the ones right after the student was sent, until they are back.
        #a failed detour flags the concept that sent them -- whether or not its question comes up
        #again after (a missed True/False never does)
        on_detour = []
        for r in later:
            if not (getattr(r, "decision", None) and r.decision.on_detour):
                break
            on_detour.append(r)
        if not on_detour or any(r.is_correct for r in on_detour):
            continue  # detour not answered yet, or it worked
        flagged.setdefault(sent.student_id, {})[sent.concept_id] = {
            "concept_id": sent.concept_id, "topic": sent.topic.title, "question_id": sent.question_id,
        }
    labels = dict(LearningObjectGroup.objects.filter(
        id__in={c for per in flagged.values() for c in per}).values_list("id", "label"))
    return {sid: [dict(item, concept=labels.get(cid, f"Concept {cid}")) for cid, item in per.items()]
            for sid, per in flagged.items()}


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
    needs_help = concepts_needing_help(course)

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
            "needs_help": needs_help.get(sid, []),  # concepts where the rules ran out: the teacher steps in
        })
    return {"course_id": course.id, "total_modules": total_modules, "content_ready": bool(topics), "rows": rows}
