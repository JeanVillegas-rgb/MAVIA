from django.db import transaction

from lessons.models import CourseGroup, LearningMaterial, LearningObject, OutlineNode
from question_generation.models import GeneratedQuestion

from .models import CourseModule, LessonNode, ModuleQuestion
from .question_formatting import answer_label, choice_texts

BLOOM_BUCKETS = ("remember", "understand", "analyze")
VARIANT_KEYS = ("normal", "elaborated", "simplified")


def _module_descendants(module_node):
    stack = list(module_node.children.order_by("order", "id"))
    while stack:
        node = stack.pop(0)
        yield node
        stack[0:0] = list(node.children.order_by("order", "id"))


def _lesson_sources_for_module(module_node):
    children = list(_module_descendants(module_node))
    return children or [module_node]


def _bloom_bucket(level):
    """Collapse GeneratedQuestion's six Bloom levels into three UI buckets.

    remember -> remember, understand -> understand, everything else
    (apply/analyze/evaluate/create) -> analyze. This is a deliberate
    many-to-one simplification for the three-tier UI, not a bug.
    """
    level = (level or "").lower()
    if level == "remember":
        return "remember"
    if level == "understand":
        return "understand"
    return "analyze"


def _material_text_for_source(material):
    objects = LearningObject.objects.filter(
        kind=LearningObject.Kind.TEXT,
        material=material,
        material__status="completed",
    ).order_by("order", "id")
    lines = []
    for obj in objects:
        lines.append(f"{obj.title}\n{obj.content}".strip())
    return "\n\n".join(line for line in lines if line)


def sync_course_outline(course_id):
    course = CourseGroup.objects.get(id=course_id)
    roots = course.nodes.filter(parent__isnull=True).order_by("order", "id")

    with transaction.atomic():
        for root in roots:
            module, _ = CourseModule.objects.get_or_create(source=root)
            module.is_active = True
            module.save(update_fields=["is_active"])

            for outline_node in _lesson_sources_for_module(root):
                materials = LearningMaterial.objects.filter(outline_node=outline_node)
                for material in materials:
                    LessonNode.objects.get_or_create(module=module, source=material)

    return CourseModule.objects.filter(source__course=course, is_active=True)


def sync_module_questions(lesson_node):
    learning_objects = LearningObject.objects.filter(
        material=lesson_node.source,
        kind=LearningObject.Kind.TEXT,
    )
    questions = GeneratedQuestion.objects.filter(
        node__in=learning_objects,
    ).order_by("node__order", "id")

    for index, question in enumerate(questions, start=1):
        ModuleQuestion.objects.update_or_create(
            lesson_node=lesson_node,
            question=question,
            defaults={
                "bloom_level": _bloom_bucket(question.bloom_level).upper(),
                "order": index,
            },
        )


def first_lesson_node(course_id=None):
    if course_id is None:
        course = CourseGroup.objects.filter(outline__is_approved=True).order_by("id").first()
        if course is None:
            return None
        course_id = course.id

    sync_course_outline(course_id)
    return (
        LessonNode.objects.filter(module__source__course_id=course_id, module__is_active=True)
        .order_by(
            "module__source__order",
            "source__outline_node__depth",
            "source__outline_node__order",
            "source__id",
        )
        .first()
    )


class LessonPackageService:
    @staticmethod
    def build_package(node_id):
        node = LessonNode.objects.select_related(
            "module", "source", "module__source", "source__outline_node"
        ).get(id=node_id)
        sync_module_questions(node)

        normal_text = _material_text_for_source(node.source)
        if not normal_text:
            normal_text = node.source.outline_node.related_info.get("description", "") or node.title

        variants = {}
        saved_variants = {
            item.variant.lower(): item
            for item in node.variants.all()
        }
        for key in VARIANT_KEYS:
            saved = saved_variants.get(key)
            variants[key] = {
                "text": saved.narration if saved else normal_text,
                "audio_url": saved.audio_url if saved else "",
            }

        questions = {bucket: [] for bucket in BLOOM_BUCKETS}
        module_questions = node.module_questions.select_related("question").order_by("order", "id")
        for module_question in module_questions:
            question = module_question.question
            bucket = module_question.bloom_level.lower()
            questions.setdefault(bucket, []).append(
                {
                    "id": question.id,
                    "order": module_question.order,
                    "bloom_level": bucket,
                    "question": question.question_text,
                    "choices": choice_texts(question),
                    "correct_answer": answer_label(question),
                }
            )

        return {
            "module": {
                "id": node.module.id,
                "title": node.module.title,
                "sequence_order": node.module.sequence_order,
            },
            "lesson_node": {
                "id": node.id,
                "title": node.title,
                "node_order": node.source.outline_node.order,
            },
            "variants": variants,
            "questions": questions,
        }
