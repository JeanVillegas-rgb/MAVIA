#BUILDING: turns a published learning path into the package the phone downloads
#(the student moving through it lives in traversal.py)

from learning_path.services import get_published_path
from lessons.models import OutlineNode
from .models import TopicPackage
from question_generation.models import GeneratedQuestion
from lessons.services.audio_generator import question_audio_url




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
    #spare questions for a concept for missed true or false questions (cuz ma memorize sa student ang answer sa TF)
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
