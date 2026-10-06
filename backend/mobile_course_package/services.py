from learning_path.services import get_published_path
from lessons.models import OutlineNode
from .models import StudentResponse, TopicPackage, TopicPackageProgress
from adaptive.services import grade, decide_after_listening, apply_answer, first_open_question, first_question_on_arrival, askable_questions, segment_review, LEAVES_THE_SEGMENT
from question_generation.models import GeneratedQuestion
from lessons.services.audio_generator import question_audio_url
from django.db import transaction




#audio course 

def course_outline(course):
    modules = OutlineNode.objects.filter(course=course, parent__isnull=True).order_by("order", "id")
    return [
        {
            "id": module.id,
            "title": module.title,
            "topics": [
                {"id": topic.id, "title": topic.title}
                for topic in module.children.filter(published=True).order_by("order", "id")
            ],
        }
        for module in modules 
    ]



#BUILD THE AUDIO COURSE PACKAGEEEEEEEEEEEEEEEEEEE

def audio_for_topic_explanation_types(versions):  # each explanation type has its own audio to pull
    result = {}
    for role, explanation_type in versions.items():
        if not explanation_type:
            continue
        urls = [clip["audio_url"] for clip in explanation_type["parts"] if clip.get("audio_url")]
        if urls:
            result[role] = urls
    return result


def audio_for_topic_questions(questions):  # each question of the step has its own audio to pull, by question id
    return {str(question["id"]): question["audio_url"] for question in questions if question.get("audio_url")}



def question_pool_for_student_view(question):
    accessible = ("id", "text", "format", "choices", "thinking_order")
    result = {}
    for attributes in accessible:
        result[attributes] = question[attributes]
    return result


def reserve_questions_for(concept_id, already_used):
    #spare questions for a concept: final questions in its bank that the learning
    #path did not serve (it caps each concept at 2 LOT + 1 HOT). Used only after
    #a True/False is missed, so the re-teach is followed by a DIFFERENT question.
    spare = GeneratedQuestion.objects.filter(
        node__group_id=concept_id, status="final"
    ).exclude(id__in=already_used).select_related("node__material").order_by("id")
    return [
        {
            "id": q.id,
            "text": q.question_text,
            "format": q.question_format,
            "choices": q.choices,
            "thinking_order": q.thinking_order,
            "correct_answer": q.correct_answer,
            "explanation": q.explanation,
            "audio_url": question_audio_url(q),
        }
        for q in spare
    ]


def build_course_package(topic):
    path = get_published_path(topic)
    if path is None or topic.published_at is None:
        return None

    steps, answer_key = [], {}
    for step in path["steps"]:
        every_question = step["questions"] + [q for alt in step["alternates"] for q in alt["questions"]]
        for question in every_question:
            answer_key[str(question["id"])] = {
                "concept_id": step["concept_id"],
                "format": question["format"],
                "choices": question["choices"],
                "correct_answer": question["correct_answer"],
                "explanation": question["explanation"],
            }

        reserve = reserve_questions_for(step["concept_id"], [q["id"] for q in every_question])
        for question in reserve:
            answer_key[str(question["id"])] = {
                "concept_id": step["concept_id"],
                "format": question["format"],
                "choices": question["choices"],
                "correct_answer": question["correct_answer"],
                "explanation": question["explanation"],
            }

        steps.append({
            "position": step["position"],
            "concept_id": step["concept_id"],
            "prerequisites": step["prerequisites"],
            "versions": audio_for_topic_explanation_types(step["versions"]),
            "questions": [question_pool_for_student_view(q) for q in step["questions"]],
            "reserve_questions": [question_pool_for_student_view(q) for q in reserve],
            "question_audio": audio_for_topic_questions(step["questions"] + reserve),
        })

    package, _ = TopicPackage.objects.update_or_create(
        topic=topic,
        defaults={"published_at": topic.published_at, "steps":steps, "answer_key": answer_key},

    )
    return package



def get_package(topic):
    #the saved package, rebuilt only when the teacher republishes. None if the topic isnt published
    if not topic.published or topic.published_at is None:
        return None

    package = TopicPackage.objects.filter(topic=topic).first()
    if package is None or package.published_at != topic.published_at:
        package = build_course_package(topic)
    return package


def apply_command(progress, command):
    if command["action"] == "regress":
        progress.regressed_positions = progress.regressed_positions + [command["return_to_position"]]
    if command["action"] == "complete":
        progress.completed = True
    else:
        progress.current_step_position = command["next_step_position"]
        progress.current_variant = command["next_variant"]
        
    progress.return_to_position = command["return_to_position"]
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
    #move past the current step once nothing is left to ask on it: a step with no
    #questions, or one whose questions are all answered or spent (a missed
    #True/False, or one missed at every reading). None if a question is still
    #worth asking -- it must be answered first.
    progress, _ = TopicPackageProgress.objects.get_or_create(student=student, topic=topic)
    step = next((s for s in package.steps if s["position"] == progress.current_step_position), None)
    if step is None or askable_questions(step, student, topic, package):
        return None
    command = decide_after_listening(step, package.steps, progress.return_to_position)
    if command["action"] in LEAVES_THE_SEGMENT:
        command["review"] = segment_review(step, student, topic, package)
    # Tell the phone which question waits on the step it moves to (None: listen only),
    # exactly as an answer's command does.
    if command["next_step_position"] is not None:
        target = next(s for s in package.steps if s["position"] == command["next_step_position"])
        command["next_question_id"] = first_question_on_arrival(target, student, topic, package,
                                                                resuming=command["action"] == "resume")
    apply_command(progress, command)
    return command


def next_question_on_open(student, topic, package, progress):
    #when a topic is opened: which question of the current step to ask (None: listen only)
    step = next((s for s in package.steps if s["position"] == progress.current_step_position), None)
    if step is None or progress.completed:
        return None
    return first_open_question(step, student, topic, package)
