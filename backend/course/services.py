from django.db import transaction

from lessons.models import CourseGroup, LearningObject, OutlineNode, LearningMaterial
from question_generation.models import GeneratedQuestion

from .models import CourseModule, LessonNode, ModuleQuestion
from .question_formatting import answer_label, choice_texts


BLOOM_BUCKETS = ("remember", "understand", "analyze")
VARIANT_KEYS = ("normal", "elaborated", "simplified")


def _module_descendants(module_node):
    """
    Return the CourseModule instance for the given top-level OutlineNode (module_node),
    or None if it doesn't exist.
    """
    try:
        return CourseModule.objects.get(source=module_node)
    except CourseModule.DoesNotExist:
        return None


def _lesson_sources_for_module(module_node):
    module = _module_descendants(module_node)
    if not module:
        return []

    # Prefer LearningMaterial objects that reference this module via module_node
    materials = list(
        LearningMaterial.objects.filter(module_node=module_node, status="completed").order_by("created_at", "id")
    )

    if materials:
        return materials

    # Fallback: if no materials directly reference the module, try to find materials
    # attached to outline nodes under the module's outline node, or use the outline node
    # itself as the single lesson source.
    descendants = list(module_node.children.order_by("order", "id"))
    nodes = []
    while descendants:
        node = descendants.pop(0)
        nodes.append(node)
        descendants[0:0] = list(node.children.order_by("order", "id"))

    # Find materials attached to those outline nodes (completed only) and return them
    if nodes:
        materials = list(
            LearningMaterial.objects.filter(module_node__isnull=True, outline_node__in=nodes, status="completed").order_by("created_at", "id")
        )
        if materials:
            return materials

    # As a final fallback return the module's outline node so the old behaviour still works
    return [module_node]


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


def _answer_label(question):
    answer = (question.correct_answer or "").strip()
    if answer.upper() in {"A", "B", "C", "D"}:
        return answer.upper()

    choices = question.choices or []
    if question.question_format == "TF":
        if answer.lower() == "true":
            return "A"
        if answer.lower() == "false":
            return "B"

    if isinstance(choices, dict):
        for label in ("A", "B", "C", "D"):
            if str(choices.get(label, "")).strip().lower() == answer.lower():
                return label
        return answer

    for index, choice in enumerate(choices[:4]):
        if str(choice).strip().lower() == answer.lower():
            return "ABCD"[index]
    return answer


def _choice_texts(question):
    choices = question.choices or []
    if isinstance(choices, dict):
        return [str(choices.get(label, "")) for label in ("A", "B", "C", "D") if choices.get(label)]
    if question.question_format == "TF" and not choices:
        return ["True", "False"]
    return [str(choice) for choice in choices[:4]]


def _material_text_for_source(source):
    if isinstance(source, LearningMaterial):
        objects = LearningObject.objects.filter(
            material=source,
            kind=LearningObject.Kind.TEXT,
        ).order_by("material_id", "order", "id")
    else:
        # source is an OutlineNode; find TEXT learning objects attached to materials
        # that reference this outline node and are completed.
        objects = LearningObject.objects.filter(
            kind=LearningObject.Kind.TEXT,
            material__outline_node=source,
            material__status="completed",
        ).order_by("material_id", "order", "id")

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

            # Discover lesson sources for this module and ensure LessonNode exists for each
            for source in _lesson_sources_for_module(root):
                # If the source is a LearningMaterial, create/get LessonNode using that material
                if isinstance(source, LearningMaterial):
                    LessonNode.objects.get_or_create(module=module, source=source)
                else:
                    # source is likely an OutlineNode; try to find a completed LearningMaterial
                    # attached to this outline node and use that as the LessonNode.source.
                    material = LearningMaterial.objects.filter(outline_node=source, status="completed").order_by("created_at", "id").first()
                    if material:
                        LessonNode.objects.get_or_create(module=module, source=material)
                    else:
                        # No suitable LearningMaterial found — skip creating a LessonNode for this outline node
                        continue

    return CourseModule.objects.filter(source__course=course, is_active=True)


def sync_module_questions(lesson_node):
    learning_objects = LearningObject.objects.filter(
        material__outline_node=lesson_node.source,
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

    # Ensure CourseModule/LessonNode records reflect the latest outline/materials
    sync_course_outline(course_id)
    return (
        LessonNode.objects.filter(module__source__course_id=course_id, module__is_active=True)
        .order_by("module__source__order", "source__depth", "source__order", "source__id")
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
            normal_text = node.source.related_info.get("description", "") or node.title

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

        # compute node_order defensively — source may not expose an 'order' attribute
        node_order = getattr(node.source, "order", None) or getattr(node, "id", None)

        return {
            "module": {
                "id": node.module.id,
                "title": node.module.title,
                "sequence_order": node.module.sequence_order,
            },
            "lesson_node": {
                "id": node.id,
                "title": node.title,
                "node_order": node.node_order,
            },
            "variants": variants,
            "questions": questions,
        }
