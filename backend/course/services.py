from django.db import transaction

from lessons.models import CourseGroup, LearningObject, OutlineNode, LearningMaterial
from question_generation.models import GeneratedQuestion

from .models import CourseModule, LessonNode, LessonVariant, ModuleQuestion, normal_variant_for


VARIANT_KEYS = ("normal", "elaborated", "simplified")


def _material_outline_order(material):
    outline_node = getattr(material, "outline_node", None)
    return outline_node.order if outline_node is not None else None


def _module_descendants(module_node):
    try:
        return CourseModule.objects.get(source=module_node)
    except CourseModule.DoesNotExist:
        return None


def _lesson_sources_for_module(module_node):
    module = _module_descendants(module_node)
    if not module:
        return []

    materials = list(
        LearningMaterial.objects.filter(module_node=module_node, status="completed").order_by("created_at", "id")
    )

    if materials:
        return materials

    descendants = list(module_node.children.order_by("order", "id"))
    nodes = []
    while descendants:
        node = descendants.pop(0)
        nodes.append(node)
        descendants[0:0] = list(node.children.order_by("order", "id"))

    if nodes:
        materials = list(
            LearningMaterial.objects.filter(module_node__isnull=True, outline_node__in=nodes, status="completed").order_by("created_at", "id")
        )
        if materials:
            return materials

    return [module_node]


def _material_text_for_source(source):
    """
    Fallback concatenated text for a lesson source, used only when a chunk
    has no NORMAL narration yet (her audio/narration pipeline hasn't run).
    Accepts either a LearningMaterial instance or an OutlineNode (legacy).
    """
    if isinstance(source, LearningMaterial):
        objects = LearningObject.objects.filter(
            material=source,
            kind=LearningObject.Kind.TEXT,
        ).order_by("material_id", "order", "id")
    else:
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

            for source in _lesson_sources_for_module(root):
                if isinstance(source, LearningMaterial):
                    LessonNode.objects.get_or_create(module=module, source=source)
                else:
                    material = LearningMaterial.objects.filter(outline_node=source, status="completed").order_by("created_at", "id").first()
                    if material:
                        LessonNode.objects.get_or_create(module=module, source=material)
                    else:
                        continue

    return CourseModule.objects.filter(source__course=course, is_active=True)


def sync_module_questions(lesson_node):
    if isinstance(lesson_node.source, LearningMaterial):
        learning_objects = LearningObject.objects.filter(
            material=lesson_node.source,
            kind=LearningObject.Kind.TEXT,
        )
    else:
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
            defaults={"order": index},
        )


def _resolve_course_id(course_id):
    if course_id is not None:
        return course_id
    course = CourseGroup.objects.filter(outline__is_approved=True).order_by("id").first()
    return course.id if course is not None else None


def _node_order_value(lesson_node):
    order = _material_outline_order(lesson_node.source)
    return order if order is not None else lesson_node.id


def _node_order_key(lesson_node):
    order = _material_outline_order(lesson_node.source)
    if order is None:
        return (1, 0, lesson_node.pk)
    return (0, order, lesson_node.pk)


def _sorted_lesson_nodes(lesson_nodes):
    return sorted(lesson_nodes, key=_node_order_key)


def first_lesson_node(course_id=None):
    course_id = _resolve_course_id(course_id)
    if course_id is None:
        return None

    sync_course_outline(course_id)

    module = CourseModule.objects.filter(source__course_id=course_id, is_active=True).select_related("source").order_by("source__order", "source__id").first()
    if not module:
        return None

    lesson_nodes = list(LessonNode.objects.filter(module=module).select_related("source"))
    if not lesson_nodes:
        return None

    return _sorted_lesson_nodes(lesson_nodes)[0]


def list_modules_with_lessons(course_id=None):
    course_id = _resolve_course_id(course_id)
    if course_id is None:
        return []

    sync_course_outline(course_id)

    modules = (
        CourseModule.objects.filter(source__course_id=course_id, is_active=True)
        .select_related("source")
        .order_by("source__order", "source__id")
    )

    result = []
    for module in modules:
        lesson_nodes = list(
            LessonNode.objects.filter(module=module).select_related("source", "source__outline_node")
        )
        result.append({
            "id": module.id,
            "title": module.title,
            "sequence_order": module.sequence_order,
            "lesson_nodes": [
                {"id": ln.id, "title": ln.title, "node_order": _node_order_value(ln)}
                for ln in _sorted_lesson_nodes(lesson_nodes)
            ],
        })
    return result


def _choice_objects(question):
    """MCQ -> [{"key": "a", "label": "..."}, ...] keyed a/b/c/d to match the
    mobile app's QuestionChoice type. TF -> None (mobile renders True/False
    itself)."""
    if question.question_format == "TF":
        return None

    choices = question.choices or []
    if isinstance(choices, dict):
        texts = [str(choices.get(label, "")) for label in ("A", "B", "C", "D") if choices.get(label)]
    else:
        texts = [str(c) for c in choices[:4]]

    keys = ["a", "b", "c", "d"]
    return [{"key": keys[i], "label": text} for i, text in enumerate(texts) if text]


def _correct_answer_key(question):
    answer = (question.correct_answer or "").strip()

    if question.question_format == "TF":
        if answer.lower() in {"true", "false"}:
            return answer.lower()
        if answer.upper() == "A":
            return "true"
        if answer.upper() == "B":
            return "false"
        return answer.lower()

    if answer.upper() in {"A", "B", "C", "D"}:
        return answer.lower()

    choices = question.choices or []
    if isinstance(choices, dict):
        for label in ("A", "B", "C", "D"):
            if str(choices.get(label, "")).strip().lower() == answer.lower():
                return label.lower()
        return answer

    for index, choice in enumerate(choices[:4]):
        if str(choice).strip().lower() == answer.lower():
            return "abcd"[index]
    return answer


def _build_chunk(learning_object):
    variants = {}

    normal = normal_variant_for(learning_object)
    if normal:
        variants["normal"] = {"text": normal["narration"], "audio_url": normal["audio_url"]}
    else:
        variants["normal"] = {"text": learning_object.content, "audio_url": ""}

    for row in learning_object.variants.all(): 
        variants[row.variant.lower()] = {"text": row.narration, "audio_url": row.audio_url}

    return {
        "id": learning_object.id,
        "order": learning_object.order,
        "title": learning_object.title,
        "variants": variants,
    }


def _build_question(module_question):
    question = module_question.question
    return {
        "id": question.id,
        "chunk_id": question.node_id,
        "order": module_question.order,
        "format": question.question_format,
        "bloom_level": question.bloom_level,
        "difficulty": question.difficulty,
        "question_text": question.question_text,
        "choices": _choice_objects(question),
        "correct_answer": _correct_answer_key(question),
        "explanation": question.explanation,
    }


class LessonPackageService:
    @staticmethod
    def build_package(node_id):
        node = LessonNode.objects.select_related(
            "module", "source", "module__source", "source__outline_node"
        ).prefetch_related("source__learning_objects__variants").get(id=node_id)

        sync_module_questions(node)

        learning_objects = list(node.learning_objects.order_by("order", "id"))
        chunks = [_build_chunk(lo) for lo in learning_objects]

        module_questions = node.module_questions.select_related("question").order_by("order", "id")
        questions = [_build_question(mq) for mq in module_questions]

        return {
            "module": {
                "id": node.module.id,
                "title": node.module.title,
                "sequence_order": node.module.sequence_order,
            },
            "lesson_node": {
                "id": node.id,
                "title": node.title,
                "node_order": _node_order_value(node),
            },
            "chunks": chunks,
            "questions": questions,
        }