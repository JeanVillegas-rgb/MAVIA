from django.db import transaction

from lessons.models import CourseGroup, LearningObject, OutlineNode, LearningMaterial
from question_generation.models import GeneratedQuestion

from .models import (
    CourseModule,
    LessonNode,
    LessonVariant,
    ModuleQuestion,
    bundle_segments,
    normal_bundle_for,
)
from .version_assignment import version_bundles


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


def _generated_versions(objects):
    """``{role: {"segments": [...], "origin": ...}}`` written for this bundle.

    A generated version is written per object of the Normal bundle, so its
    segments line up with the Normal ones. A role that is short of the bundle
    -- one object's generation failed, or the bundle grew after the wording was
    written -- is left out entirely rather than served. Half a track reads as a
    complete lesson to a student who cannot see the page, so the version is
    reported missing instead, which is the state the publish gate and the
    teacher's review screen already know how to show.
    """
    versions = {}
    for item in objects:
        for row in item.variants.all():
            # Extras are retained for the interaction pipeline to rule on
            # later. They are not one of the three versions a student is
            # offered.
            if row.variant == "EXTRA":
                continue
            version = versions.setdefault(row.variant, {"segments": [], "origin": row.origin})
            version["segments"].append({"text": row.narration, "audio_url": row.audio_url})
    return {
        role: version
        for role, version in versions.items()
        if len(version["segments"]) >= len(objects)
    }


def _bundle_leads(learning_objects):
    """One object per concept, keeping the order it was given in.

    A chunk carries the concept's whole bundle, so every other member of that
    bundle would repeat it. The objects arrive in document order, so a
    bundle's first surviving member is its lead -- the same object every other
    consumer speaks for the concept with -- and it is found without asking the
    database once per object.
    """
    leads = []
    seen = set()
    for item in learning_objects:
        if item.group_id is None:
            leads.append(item)
            continue
        bundle = (item.group_id, item.material_id)
        if bundle in seen:
            continue
        seen.add(bundle)
        leads.append(item)
    return leads


def _version_from_segments(segments, *, origin):
    """A version's payload: its segments, and the same text joined.

    ``text`` is derived from the segments rather than read separately, so a
    caption can never drift from the wording the segment actually carries.
    """
    texts = [segment["text"].strip() for segment in segments]
    return {
        "text": "\n".join(text for text in texts if text),
        "audio_url": next(
            (segment["audio_url"] for segment in segments if segment["audio_url"]), ""
        ),
        "segments": segments,
        "origin": origin,
    }


def _build_chunk(learning_object):
    """One concept, served as its versions -- each an ordered bundle.

    The chunk is still keyed by the object that leads the concept, so existing
    readers keep working: ``text`` is the whole version joined, and
    ``segments`` is what it is actually made of, in document order.
    """
    variants = {}
    normal_objects = normal_bundle_for(learning_object)

    variants["normal"] = _version_from_segments(
        bundle_segments(normal_objects), origin="original"
    )

    for role, version in _generated_versions(normal_objects).items():
        variants[role.lower()] = _version_from_segments(
            version["segments"], origin=version["origin"]
        )

    # A version a PDF supplies is that PDF's own objects -- nothing is copied
    # into a row -- and it outranks anything generated for the same role.
    if learning_object.group_id is not None:
        for role, objects in version_bundles(learning_object.group).items():
            # Normal is already built above, and an extra bundle is not one
            # of the three versions a student is offered.
            if role in ("NORMAL", "EXTRA"):
                continue
            variants[role.lower()] = _version_from_segments(
                bundle_segments(objects), origin=LessonVariant.Origin.SOURCE_PDF
            )

    return {
        "id": learning_object.id,
        "metadata_id": str(learning_object.metadata_id),
        "order": learning_object.order,
        "title": learning_object.title,
        "variants": variants,
        "versions_complete": {"simplified", "elaborated"}.issubset(variants.keys()),
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

        learning_objects = _bundle_leads(
            node.learning_objects.filter(represented_by__isnull=True).order_by("order", "id")
        )
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
