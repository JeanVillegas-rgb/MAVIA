import logging
from collections import defaultdict
import threading
from time import perf_counter

from django.db.models import Max, Prefetch
from django.utils import timezone
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from user.permissions import IsTeacherOrAdmin

from course.models import LessonVariant
from course.services import sync_course_outline
from course.variant_generator import fill_missing_slots, generate_standalone_variants
from question_generation.models import GenerationRun
from .services.topic_publish import confirmed_materials_for, run_topic_publish
from course.version_assignment import assign_group_versions, assign_source_to_slot, settle_group

from .models import (
    CourseGroup,
    LearningMaterial,
    LearningObject,
    LearningObjectGroup,
    LearningObjectMatchSuggestion,
    OutlineNode,
    Question,
    QuestionLearningObjectLink,
)
from .features.pdf_processing.serializers import (
    CourseOutlineUploadInputSerializer,
    CoursePdfUploadInputSerializer,
    LearningMaterialUploadInputSerializer,
)
from .features.pdf_processing.use_cases import (
    PdfProcessingUseCaseError,
    confirm_course_outline,
    regenerate_learning_material,
    upload_course_outline,
    upload_course_pdf,
    upload_learning_material,
)
from .serializers import (
    CourseCreateSerializer,
    CourseDetailSerializer,
    CourseListSerializer,
    LearningMaterialSerializer,
    LearningObjectMutationSerializer,
    LearningObjectMatchSuggestionSerializer,
    LearningObjectSerializer,
    OutlineNodeMutationSerializer,
    OutlineNodeSerializer,
    QuestionSerializer,
)
from .services.audio_generator import AudioGenerationError, generate_material_audio_playlist
from .services.content_generator import (
    apply_classification_override,
    build_lesson_playlist,
    build_narration_script_from_learning_objects,
    is_structural_metadata_label,
)
from .services.instructional_content_classifier import CLASSIFICATION_CATEGORIES
from .services.image_describer import populate_missing_image_descriptions
from .services.learning_resource_linker import (
    CONFIRMED_QUESTION_PAIRING_STATUSES,
    learning_objects_are_confirmed,
    learning_object_match_debug_configuration,
    question_pairing_debug_configuration,
    question_snapshots,
    record_teacher_match_decision,
    refresh_material_learning_relationships,
    refresh_question_learning_object_links,
)
from .services.question_workflow import (
    duplicate_for_topic,
    duplicate_in_topic,
    enriched_question_values,
    question_fingerprint,
    sync_question_to_adaptive,
)


logger = logging.getLogger(__name__)


def _run_topic_publish_in_background(run_id, course_id, node_id, set_confirmed):
    """Thread entry point. Owns the run's lifecycle and nothing else."""
    from question_generation.models import GenerationEvent

    run = GenerationRun.objects.get(id=run_id)
    seq = {"n": 0}

    def record(event_type, message, **data):
        seq["n"] += 1
        GenerationEvent.objects.create(
            run=run, seq=seq["n"], event_type=event_type, message=message, data=data or None
        )

    try:
        course = CourseGroup.objects.get(id=course_id)
        node = OutlineNode.objects.get(id=node_id)
        run_topic_publish(
            course, node,
            set_confirmed=lambda material: set_confirmed(material, True),
            on_event=record,
        )
        run.status = "finished"
    except Exception as exc:  # a failed publish must not leave the run "running" forever
        logger.exception("Publish run %s failed", run_id)
        record("publish_failed", str(exc))
        run.status = "failed"
    finally:
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "finished_at"])


class CourseGroupViewSet(viewsets.ModelViewSet):
    permission_classes = [IsTeacherOrAdmin]
    queryset = CourseGroup.objects.prefetch_related(
        "nodes",
        "outlines",
        "materials__learning_objects",
        "materials__questions__learning_object_links__learning_object",
    ).all()
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_serializer_class(self):
        if self.action in {"list"}:
            return CourseListSerializer
        if self.action == "create":
            return CourseCreateSerializer
        return CourseDetailSerializer

    @action(detail=True, methods=["get"], url_path="review/modules", permission_classes=[permissions.IsAuthenticated])
    def review_modules(self, request, pk=None):
        if request.user.role not in {"TEACHER", "ADMIN"}:
            return Response({"detail": "Teacher or admin access required."}, status=403)
        from .services.lesson_package import build_course_module_summaries
        return Response(build_course_module_summaries(self.get_object()))

    @action(detail=True, methods=["get"], url_path=r"review/modules/(?P<module_id>[^/.]+)/package", permission_classes=[permissions.IsAuthenticated])
    def review_module_package(self, request, pk=None, module_id=None):
        if request.user.role not in {"TEACHER", "ADMIN"}:
            return Response({"detail": "Teacher or admin access required."}, status=403)
        from .services.lesson_package import build_module_package
        module = self._get_module_node(self.get_object(), module_id)
        if module is None:
            return Response({"detail": "Module not found in this course."}, status=404)
        return Response(build_module_package(module))

    def _get_module_node(self, course, module_id):
        try:
            module = course.nodes.get(pk=module_id, parent__isnull=True)
        except OutlineNode.DoesNotExist:
            return None
        return module

    @action(detail=True, methods=["get"], url_path="modules")
    def modules(self, request, pk=None):
        course = self.get_object()
        modules = course.nodes.filter(parent__isnull=True).order_by("order", "id")
        return Response(OutlineNodeSerializer(modules, many=True, context={"request": request}).data)

    def _learning_resources_payload(self, node, request):
        started_at = perf_counter()
        learning_object_queryset = (
            LearningObject.objects.filter(
                material__generated_json__learning_objects_confirmed=True,
            )
            .select_related("material")
            .order_by("material_id", "order", "id")
        )
        group_queryset = node.learning_object_groups.prefetch_related(
            Prefetch(
                "learning_objects",
                queryset=learning_object_queryset,
                to_attr="resource_learning_objects",
            )
        ).order_by("id")
        has_confirmed_learning_objects = learning_object_queryset.filter(
            material__outline_node=node,
        ).exists()
        confirmed_link_queryset = (
            QuestionLearningObjectLink.objects.filter(
                learning_object__material__generated_json__learning_objects_confirmed=True,
            )
            .select_related("learning_object__group")
            .order_by("-is_primary", "-relevance_score", "id")
        )
        all_questions = list(
            Question.objects.filter(material__outline_node=node)
            .select_related("material")
            .prefetch_related(
                Prefetch("learning_object_links", queryset=confirmed_link_queryset)
            )
            .order_by("material_id", "order", "id")
        ) if has_confirmed_learning_objects else []
        confirmed_questions_by_group = defaultdict(list)
        for question in all_questions:
            group_ids = {
                link.learning_object.group_id
                for link in question.learning_object_links.all()
                if link.review_status in CONFIRMED_QUESTION_PAIRING_STATUSES
                and link.learning_object.group_id is not None
            }
            for group_id in group_ids:
                confirmed_questions_by_group[group_id].append(question)

        groups = []
        for group in group_queryset:
            learning_objects = [
                item
                for item in group.resource_learning_objects
                if not is_structural_metadata_label(item.title)
            ]
            if not learning_objects:
                continue
            version_state = assign_group_versions(group)
            slot_rows = {}
            extra_rows = []
            if version_state["representative_id"] is not None:
                for row in LessonVariant.objects.filter(
                    learning_object_id=version_state["representative_id"],
                ):
                    entry = {
                        "id": row.id,
                        "text": row.narration,
                        "origin": row.origin,
                        "assigned_by": row.assigned_by,
                        "source_learning_object_id": row.source_learning_object_id,
                    }
                    # Extras are kept for the learning-path component to rule on;
                    # they are not one of the three slots a student is offered, so
                    # they travel as their own list rather than as a slot.
                    if row.variant == "EXTRA":
                        extra_rows.append(entry)
                    else:
                        slot_rows[row.variant.lower()] = entry
            groups.append(
                {
                    "id": group.id,
                    "label": group.label,
                    "outline_node_id": node.id,
                    "versions": {
                        "representative_id": version_state["representative_id"],
                        "slots": slot_rows,
                        "extras": extra_rows,
                        "needs_confirmation": version_state["needs_confirmation"],
                        "complete": {"simplified", "elaborated"}.issubset(slot_rows.keys()),
                    },
                    "learning_objects": LearningObjectSerializer(
                        learning_objects,
                        many=True,
                        context={"request": request},
                    ).data,
                    "questions": QuestionSerializer(
                        confirmed_questions_by_group[group.id],
                        many=True,
                        context={"request": request},
                    ).data,
                }
            )
        payload = {
            "outline_node": OutlineNodeSerializer(node, context={"request": request}).data,
            "learning_object_groups": groups,
            "matching_debug": learning_object_match_debug_configuration(),
            "question_pairing_debug": question_pairing_debug_configuration(),
            "question_pairings": QuestionSerializer(
                all_questions,
                many=True,
                context={"request": request},
            ).data,
            "match_suggestions": LearningObjectMatchSuggestionSerializer(
                node.learning_object_match_suggestions.filter(
                    status=LearningObjectMatchSuggestion.Status.PENDING,
                    source_learning_object__material__generated_json__learning_objects_confirmed=True,
                    candidate_learning_object__material__generated_json__learning_objects_confirmed=True,
                ).select_related(
                    "source_learning_object__material",
                    "candidate_learning_object__material",
                ),
                many=True,
                context={"request": request},
            ).data,
        }

        elapsed_ms = round((perf_counter() - started_at) * 1000, 1)
        payload["debug"] = {
            "payload_build_ms": elapsed_ms,
            "group_count": len(groups),
            "question_count": len(all_questions),
            "suggestion_count": len(payload["match_suggestions"]),
        }
        logger.info(
            "Learning-resource payload: node=%s duration_ms=%.1f groups=%s questions=%s suggestions=%s",
            node.id,
            elapsed_ms,
            len(groups),
            len(all_questions),
            len(payload["match_suggestions"]),
        )
        return payload

    def _refresh_relationship_snapshots(self, materials, *, recompute=True):
        """Keep the API handoff snapshot aligned with teacher grouping changes."""
        for material in materials:
            if recompute:
                refresh_material_learning_relationships(material)
            generated_json = material.generated_json or {}
            generated_json["questions"] = question_snapshots(material)
            generated_json["learning_objects"] = self._learning_objects_snapshot(material)
            material.generated_json = generated_json
            material.save(update_fields=["generated_json"])

    @action(
        detail=True,
        methods=["get"],
        url_path=r"outline-nodes/(?P<node_id>[^/.]+)/learning-resources",
    )
    def node_learning_resources(self, request, pk=None, node_id=None):
        course = self.get_object()
        try:
            node = course.nodes.get(pk=node_id)
        except OutlineNode.DoesNotExist:
            return Response(
                {"detail": "Outline node not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(self._learning_resources_payload(node, request))

    @action(
        detail=True,
        methods=["post"],
        url_path=r"outline-nodes/(?P<node_id>[^/.]+)/connect-learning-objects",
    )
    def connect_learning_objects(self, request, pk=None, node_id=None):
        """Connect equivalent objects without assigning a difficulty label."""
        course = self.get_object()
        try:
            node = course.nodes.get(pk=node_id)
        except OutlineNode.DoesNotExist:
            return Response(
                {"detail": "Outline node not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        raw_ids = request.data.get("learning_object_ids")
        if not isinstance(raw_ids, list) or len(set(raw_ids)) < 2:
            return Response(
                {"detail": "Provide at least two learning_object_ids."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            object_ids = {int(value) for value in raw_ids}
        except (TypeError, ValueError):
            return Response(
                {"detail": "Every learning_object_id must be an integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        learning_objects = list(
            LearningObject.objects.filter(
                id__in=object_ids,
                material__course=course,
            ).select_related("material", "group")
        )
        if len(learning_objects) != len(object_ids):
            return Response(
                {"detail": "One or more learning objects were not found in this course."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if any(item.material.outline_node_id != node.id for item in learning_objects):
            return Response(
                {"detail": "Connected learning objects must belong to this same outline node."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if any(not learning_objects_are_confirmed(item.material) for item in learning_objects):
            return Response(
                {"detail": "Confirm every selected learning object before reviewing connections."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        target_group = next(
            (
                item.group
                for item in learning_objects
                if item.group_id and item.group.outline_node_id == node.id
            ),
            None,
        )
        if target_group is None:
            target_group = LearningObjectGroup.objects.create(
                outline_node=node,
                label=(request.data.get("label") or learning_objects[0].title)[:255],
            )
        elif request.data.get("label"):
            target_group.label = str(request.data["label"]).strip()[:255]
            target_group.save(update_fields=["label"])

        old_group_ids = {
            item.group_id
            for item in learning_objects
            if item.group_id and item.group_id != target_group.id
        }
        LearningObject.objects.filter(id__in=object_ids).update(group=target_group)
        if old_group_ids:
            LearningObjectGroup.objects.filter(
                id__in=old_group_ids,
                learning_objects__isnull=True,
            ).delete()
        self._refresh_relationship_snapshots(
            {item.material for item in learning_objects}
        )
        for index, first in enumerate(learning_objects):
            for second in learning_objects[index + 1:]:
                record_teacher_match_decision(first, second, accepted=True)

        return Response(self._learning_resources_payload(node, request))

    @action(
        detail=True,
        methods=["post"],
        url_path=r"outline-nodes/(?P<node_id>[^/.]+)/separate-learning-object",
    )
    def separate_learning_object(self, request, pk=None, node_id=None):
        """Move one object into its own neutral group without classifying it."""
        course = self.get_object()
        try:
            node = course.nodes.get(pk=node_id)
        except OutlineNode.DoesNotExist:
            return Response(
                {"detail": "Outline node not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        raw_id = request.data.get("learning_object_id")
        try:
            object_id = int(raw_id)
        except (TypeError, ValueError):
            return Response(
                {"detail": "learning_object_id must be an integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            learning_object = LearningObject.objects.select_related(
                "material", "group"
            ).get(
                pk=object_id,
                material__course=course,
                material__outline_node=node,
            )
        except LearningObject.DoesNotExist:
            return Response(
                {"detail": "Learning object was not found in this outline node."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not learning_objects_are_confirmed(learning_object.material):
            return Response(
                {"detail": "Confirm this learning object before reviewing connections."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        old_group = learning_object.group
        old_group_members = list(
            old_group.learning_objects.exclude(pk=learning_object.id).select_related("material")
        ) if old_group else []
        if old_group and old_group.learning_objects.count() == 1:
            return Response(self._learning_resources_payload(node, request))

        new_group = LearningObjectGroup.objects.create(
            outline_node=node,
            label=learning_object.title[:255],
        )
        learning_object.group = new_group
        learning_object.save(update_fields=["group"])
        if old_group and not old_group.learning_objects.exists():
            old_group.delete()

        for old_member in old_group_members:
            record_teacher_match_decision(
                learning_object,
                old_member,
                accepted=False,
            )
        self._refresh_relationship_snapshots(
            {learning_object.material, *(member.material for member in old_group_members)},
            recompute=False,
        )
        return Response(self._learning_resources_payload(node, request))

    def _get_node_match_suggestion(self, course, node, suggestion_id):
        try:
            return LearningObjectMatchSuggestion.objects.select_related(
                "source_learning_object__material",
                "source_learning_object__group",
                "candidate_learning_object__material",
                "candidate_learning_object__group",
            ).get(
                pk=suggestion_id,
                outline_node=node,
                source_learning_object__material__course=course,
                candidate_learning_object__material__course=course,
            )
        except LearningObjectMatchSuggestion.DoesNotExist:
            return None

    @action(
        detail=True,
        methods=["post"],
        url_path=r"outline-nodes/(?P<node_id>[^/.]+)/match-suggestions/(?P<suggestion_id>[^/.]+)/accept",
    )
    def accept_match_suggestion(self, request, pk=None, node_id=None, suggestion_id=None):
        course = self.get_object()
        try:
            node = course.nodes.get(pk=node_id)
        except OutlineNode.DoesNotExist:
            return Response(
                {"detail": "Outline node not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        suggestion = self._get_node_match_suggestion(course, node, suggestion_id)
        if suggestion is None:
            return Response(
                {"detail": "Match suggestion not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        source = suggestion.source_learning_object
        candidate = suggestion.candidate_learning_object
        if not all(
            learning_objects_are_confirmed(item.material)
            for item in (source, candidate)
        ):
            return Response(
                {"detail": "Confirm both learning objects before reviewing this connection."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        suggestion.status = LearningObjectMatchSuggestion.Status.ACCEPTED
        suggestion.save(update_fields=["status", "updated_at"])
        target_group = source.group or LearningObjectGroup.objects.create(
            outline_node=node,
            label=source.title[:255],
        )
        old_group = candidate.group
        candidate.group = target_group
        candidate.save(update_fields=["group"])
        if old_group and old_group.id != target_group.id and not old_group.learning_objects.exists():
            old_group.delete()

        # The explicit teacher decision is already authoritative. Only update
        # handoff snapshots; rescoring every object and question made each click slow.
        self._refresh_relationship_snapshots(
            {source.material, candidate.material},
            recompute=False,
        )
        record_teacher_match_decision(source, candidate, accepted=True)
        return Response(self._learning_resources_payload(node, request))

    @action(
        detail=True,
        methods=["post"],
        url_path=r"outline-nodes/(?P<node_id>[^/.]+)/match-suggestions/(?P<suggestion_id>[^/.]+)/reject",
    )
    def reject_match_suggestion(self, request, pk=None, node_id=None, suggestion_id=None):
        course = self.get_object()
        try:
            node = course.nodes.get(pk=node_id)
        except OutlineNode.DoesNotExist:
            return Response(
                {"detail": "Outline node not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        suggestion = self._get_node_match_suggestion(course, node, suggestion_id)
        if suggestion is None:
            return Response(
                {"detail": "Match suggestion not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not all(
            learning_objects_are_confirmed(item.material)
            for item in (
                suggestion.source_learning_object,
                suggestion.candidate_learning_object,
            )
        ):
            return Response(
                {"detail": "Confirm both learning objects before reviewing this connection."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        record_teacher_match_decision(
            suggestion.source_learning_object,
            suggestion.candidate_learning_object,
            accepted=False,
        )
        return Response(self._learning_resources_payload(node, request))

    @action(
        detail=True,
        methods=["post"],
        url_path=r"outline-nodes/(?P<node_id>[^/.]+)/questions",
    )
    def create_topic_question(self, request, pk=None, node_id=None):
        """Create a teacher-authored question and immediately suggest a concept."""
        course = self.get_object()
        try:
            node = course.nodes.get(pk=node_id)
        except OutlineNode.DoesNotExist:
            return Response(
                {"detail": "Outline node not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        prompt = str(request.data.get("prompt") or "").strip()
        question_type = str(request.data.get("question_type") or "").strip()
        if not prompt:
            return Response(
                {"detail": "Enter the question text."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if question_type not in {
            Question.Type.TRUE_FALSE,
            Question.Type.MULTIPLE_CHOICE,
        }:
            return Response(
                {"detail": "Question type must be true_false or multiple_choice."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        raw_choices = request.data.get("choices") or []
        correct_answer = str(request.data.get("correct_answer") or "").strip()
        if question_type == Question.Type.TRUE_FALSE:
            choices = ["True", "False"]
            if correct_answer.casefold() not in {"true", "false"}:
                return Response(
                    {"detail": "Choose True or False as the correct answer."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            correct_answer = correct_answer.title()
        else:
            if not isinstance(raw_choices, list):
                return Response(
                    {"detail": "Multiple-choice options must be a list."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            choices = [str(choice).strip() for choice in raw_choices if str(choice).strip()]
            if len(choices) < 2 or len(choices) > 4:
                return Response(
                    {"detail": "Provide between two and four answer choices."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if correct_answer not in choices:
                return Response(
                    {"detail": "The correct answer must match one of the choices."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        values = enriched_question_values(
            prompt,
            question_type,
            choices,
            correct_answer,
        )
        duplicate = duplicate_for_topic(
            course.id,
            node.id,
            values["content_fingerprint"],
        )
        if duplicate is not None:
            return Response(
                {
                    "detail": "This question already exists in this topic.",
                    "duplicate_question_id": duplicate.id,
                },
                status=status.HTTP_409_CONFLICT,
            )

        raw_group_id = request.data.get("learning_object_group_id")
        target_group = None
        if raw_group_id not in (None, ""):
            try:
                target_group = LearningObjectGroup.objects.get(pk=int(raw_group_id), outline_node=node)
            except (TypeError, ValueError, LearningObjectGroup.DoesNotExist):
                return Response(
                    {"detail": "Selected concept was not found in this outline node."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        manual_material = LearningMaterial.objects.filter(
            course=course,
            outline_node=node,
            generated_json__document_source="manual_questions",
        ).first()
        if manual_material is None:
            module_node = node
            while module_node.parent_id:
                module_node = module_node.parent
            manual_material = LearningMaterial.objects.create(
                course=course,
                outline_node=node,
                module_node=module_node,
                title="Manually added questions",
                pdf_file="",
                status=LearningMaterial.Status.COMPLETED,
                generated_json={
                    "document_role": "assessment",
                    "document_source": "manual_questions",
                    "learning_objects": [],
                },
            )

        next_order = (manual_material.questions.aggregate(Max("order"))["order__max"] or -1) + 1
        question = Question.objects.create(
            material=manual_material,
            source_type=Question.SourceType.MANUAL,
            order=next_order,
            source_excerpt="Manually added by the teacher.",
            **values,
        )
        if target_group is not None:
            representative = (
                target_group.learning_objects.filter(
                    material__generated_json__learning_objects_confirmed=True,
                )
                .order_by("material_id", "order", "id")
                .first()
            )
            if representative is None:
                return Response(
                    {"detail": "The selected concept has no confirmed learning objects."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            QuestionLearningObjectLink.objects.create(
                question=question,
                learning_object=representative,
                relevance_score=0.0,
                method="teacher_selected",
                is_primary=True,
                review_status=QuestionLearningObjectLink.ReviewStatus.TEACHER_CONFIRMED,
                reviewed_at=timezone.now(),
            )
        refresh_question_learning_object_links(manual_material)
        question.refresh_from_db()
        sync_question_to_adaptive(question)
        generated_json = manual_material.generated_json or {}
        generated_json["questions"] = question_snapshots(manual_material)
        manual_material.generated_json = generated_json
        manual_material.save(update_fields=["generated_json"])

        return Response(
            {
                "question": QuestionSerializer(question, context={"request": request}).data,
                "resources": self._learning_resources_payload(node, request),
            },
            status=status.HTTP_201_CREATED,
        )

    @action(
        detail=True,
        methods=["patch", "delete"],
        url_path=r"outline-nodes/(?P<node_id>[^/.]+)/questions/(?P<question_id>[^/.]+)",
    )
    def delete_topic_question(self, request, pk=None, node_id=None, question_id=None):
        """Permanently drop a question and every concept pairing it had."""
        course = self.get_object()
        try:
            node = course.nodes.get(pk=node_id)
        except OutlineNode.DoesNotExist:
            return Response(
                {"detail": "Outline node not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            question = Question.objects.select_related("material").get(
                pk=question_id,
                material__course=course,
                material__outline_node=node,
            )
        except (Question.DoesNotExist, ValueError):
            return Response(
                {"detail": "Question was not found in this outline node."},
                status=status.HTTP_404_NOT_FOUND,
            )

        material = question.material
        if request.method == "PATCH":
            prompt = str(request.data.get("prompt", question.prompt) or "").strip()
            question_type = str(request.data.get("question_type", question.question_type) or "").strip()
            choices = request.data.get("choices", question.choices)
            correct_answer = str(request.data.get("correct_answer", question.correct_answer) or "").strip()
            if not prompt:
                return Response({"detail": "Enter the question text."}, status=status.HTTP_400_BAD_REQUEST)
            if question_type not in {Question.Type.TRUE_FALSE, Question.Type.MULTIPLE_CHOICE}:
                return Response(
                    {"detail": "Question type must be true_false or multiple_choice."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if question_type == Question.Type.TRUE_FALSE:
                choices = ["True", "False"]
                correct_answer = correct_answer.title()
            elif not isinstance(choices, list):
                return Response({"detail": "Multiple-choice options must be a list."}, status=status.HTTP_400_BAD_REQUEST)
            else:
                choices = [str(choice).strip() for choice in choices if str(choice).strip()]

            values = enriched_question_values(prompt, question_type, choices, correct_answer)
            if values["validation_issues"]:
                return Response(
                    {"detail": " ".join(values["validation_issues"]), "validation_issues": values["validation_issues"]},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            duplicate = duplicate_in_topic(material, values["content_fingerprint"], exclude_id=question.id)
            if duplicate is not None:
                return Response(
                    {"detail": "This question already exists in this topic.", "duplicate_question_id": duplicate.id},
                    status=status.HTTP_409_CONFLICT,
                )
            for field, value in values.items():
                setattr(question, field, value)
            question.save(update_fields=list(values))
            sync_question_to_adaptive(question)
            generated_json = material.generated_json or {}
            generated_json["questions"] = question_snapshots(material)
            material.generated_json = generated_json
            material.save(update_fields=["generated_json"])
            return Response({
                "question": QuestionSerializer(question, context={"request": request}).data,
                "resources": self._learning_resources_payload(node, request),
            })

        adaptive_id = question.adaptive_question_id
        question.delete()
        if adaptive_id:
            from question_generation.models import GeneratedQuestion
            GeneratedQuestion.objects.filter(pk=adaptive_id).delete()

        generated_json = material.generated_json or {}
        generated_json["questions"] = question_snapshots(material)
        material.generated_json = generated_json
        material.save(update_fields=["generated_json"])
        self._refresh_relationship_snapshots({material}, recompute=False)

        return Response(self._learning_resources_payload(node, request))

    @action(
        detail=True,
        methods=["delete"],
        url_path=r"outline-nodes/(?P<node_id>[^/.]+)/learning-objects/(?P<object_id>[^/.]+)",
    )
    def delete_topic_learning_object(self, request, pk=None, node_id=None, object_id=None):
        """Drop one learning object during final review, keeping its PDF confirmed.

        The step-1 endpoint unconfirms the whole material on delete, which would
        empty the final-review payload. Final review keeps the confirmation so the
        remaining concepts stay publishable.
        """
        course = self.get_object()
        try:
            node = course.nodes.get(pk=node_id)
        except OutlineNode.DoesNotExist:
            return Response(
                {"detail": "Outline node not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            learning_object = LearningObject.objects.select_related("material", "group").get(
                pk=object_id,
                material__course=course,
                material__outline_node=node,
            )
        except (LearningObject.DoesNotExist, ValueError):
            return Response(
                {"detail": "Learning object was not found in this outline node."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not learning_objects_are_confirmed(learning_object.material):
            return Response(
                {"detail": "Confirm this learning object before reviewing connections."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        material = learning_object.material
        group = learning_object.group
        learning_object.delete()
        if group is not None and not group.learning_objects.exists():
            group.delete()

        self._refresh_relationship_snapshots({material})

        return Response(self._learning_resources_payload(node, request))

    @action(
        detail=True,
        methods=["post"],
        url_path=r"outline-nodes/(?P<node_id>[^/.]+)/publish",
    )
    def publish_topic(self, request, pk=None, node_id=None):
        """Start a publish run for one topic and return its id.

        Publishing runs image narration, version settling and audio synthesis
        across every confirmed material, each of which calls a local model.
        Doing that inside the request would hold the connection open for
        minutes with nothing to show, so the work moves to a background thread
        and the teacher follows it through the run's event stream -- the same
        mechanism question generation already uses.
        """
        course = self.get_object()
        try:
            node = course.nodes.get(pk=node_id)
        except OutlineNode.DoesNotExist:
            return Response(
                {"detail": "Outline node not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not confirmed_materials_for(course, node).exists():
            return Response(
                {"detail": "Confirm at least one lesson file's learning objects before publishing."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        active = GenerationRun.objects.filter(
            outline_node=node, status="running"
        ).order_by("-id").first()
        if active is not None:
            return Response(
                {"detail": "This topic is already publishing.", "run_id": active.id},
                status=status.HTTP_409_CONFLICT,
            )

        run = GenerationRun.objects.create(outline_node=node)
        threading.Thread(
            target=_run_topic_publish_in_background,
            # The confirmation helper lives on the viewset and does not touch
            # the request, so the bound method travels to the thread rather
            # than being extracted into a module function for one caller.
            args=(run.id, course.id, node.id, self._set_learning_objects_confirmed),
            daemon=True,
        ).start()
        return Response(
            {"run_id": run.id, "node_id": node.id},
            status=status.HTTP_202_ACCEPTED,
        )

    @action(
        detail=True,
        methods=["post"],
        url_path=r"outline-nodes/(?P<node_id>[^/.]+)/learning-objects/(?P<object_id>[^/.]+)/generate-versions",
    )
    def generate_object_versions(self, request, pk=None, node_id=None, object_id=None):
        """Fill one teaching step's missing version slots so a teacher can read them.

        Deliberately one object at a time: a single Gemma call takes minutes, so
        a bulk request would block for hours with nothing to show. Failures are
        reported in the payload rather than raised -- an unreachable model must
        not look like a broken endpoint.
        """
        course = self.get_object()
        try:
            node = course.nodes.get(pk=node_id)
        except OutlineNode.DoesNotExist:
            return Response({"detail": "Outline node not found."}, status=status.HTTP_404_NOT_FOUND)

        try:
            learning_object = LearningObject.objects.select_related("material").get(
                pk=object_id,
                material__outline_node=node,
            )
        except (LearningObject.DoesNotExist, TypeError, ValueError):
            return Response(
                {"detail": "Learning object not found in this topic."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if learning_object.represented_by_id is not None:
            return Response(
                {"detail": "This object is taught through another one; generate versions there."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = fill_missing_slots(learning_object)
        payload = self._learning_resources_payload(node, request)
        payload["version_generation"] = {
            "learning_object_id": learning_object.id,
            "generated": result["generated"],
            "skipped": result["skipped"],
            "errors": result["errors"],
        }
        return Response(payload)

    @action(
        detail=True,
        methods=["post"],
        url_path=r"outline-nodes/(?P<node_id>[^/.]+)/versions/(?P<variant_id>[^/.]+)",
    )
    def edit_version_text(self, request, pk=None, node_id=None, variant_id=None):
        """Reword one version. Where the text came from is preserved."""
        course = self.get_object()
        try:
            node = course.nodes.get(pk=node_id)
        except OutlineNode.DoesNotExist:
            return Response({"detail": "Outline node not found."}, status=status.HTTP_404_NOT_FOUND)

        try:
            variant = LessonVariant.objects.select_related("learning_object__material").get(
                pk=variant_id,
                learning_object__material__outline_node=node,
            )
        except (LessonVariant.DoesNotExist, TypeError, ValueError):
            return Response(
                {"detail": "Version not found in this topic."},
                status=status.HTTP_404_NOT_FOUND,
            )

        narration = str(request.data.get("narration") or "").strip()
        if not narration:
            return Response(
                {"detail": "Version text cannot be empty."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        variant.narration = narration
        # origin records where the wording came from and stays put; assigned_by
        # is what records that a teacher has since had a hand in it.
        variant.assigned_by = LessonVariant.AssignedBy.TEACHER
        variant.save(update_fields=["narration", "assigned_by"])

        return Response(self._learning_resources_payload(node, request))

    @action(
        detail=True,
        methods=["post"],
        url_path=r"outline-nodes/(?P<node_id>[^/.]+)/version-assignment",
    )
    def confirm_version_assignment(self, request, pk=None, node_id=None):
        """Record a teacher's ruling on which version slot a grouped text fills."""
        course = self.get_object()
        try:
            node = course.nodes.get(pk=node_id)
        except OutlineNode.DoesNotExist:
            return Response({"detail": "Outline node not found."}, status=status.HTTP_404_NOT_FOUND)

        slot = str(request.data.get("slot", "")).upper()
        if slot not in ("SIMPLIFIED", "ELABORATED", "EXTRA"):
            return Response(
                {"detail": "slot must be SIMPLIFIED, ELABORATED or EXTRA."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            learning_object = LearningObject.objects.select_related("group", "material").get(
                pk=request.data.get("learning_object_id"),
                material__outline_node=node,
            )
        except (LearningObject.DoesNotExist, TypeError, ValueError):
            return Response(
                {"detail": "Learning object not found in this topic."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if learning_object.group_id is None:
            return Response(
                {"detail": "Only grouped learning objects have version slots."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        state = assign_group_versions(learning_object.group)
        representative_id = state["representative_id"]
        if representative_id is None or representative_id == learning_object.id:
            return Response(
                {"detail": "This object is the original and cannot fill another slot."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        representative = LearningObject.objects.get(pk=representative_id)
        assign_source_to_slot(representative, learning_object, slot)

        return Response(self._learning_resources_payload(node, request))

    @action(
        detail=True,
        methods=["post"],
        url_path=r"outline-nodes/(?P<node_id>[^/.]+)/questions/(?P<question_id>[^/.]+)/pairing",
    )
    def review_question_pairing(self, request, pk=None, node_id=None, question_id=None):
        """Confirm, redirect, or deliberately leave a suggested question pair unpaired."""
        course = self.get_object()
        try:
            node = course.nodes.get(pk=node_id)
        except OutlineNode.DoesNotExist:
            return Response(
                {"detail": "Outline node not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            question = Question.objects.select_related("material").get(
                pk=question_id,
                material__course=course,
                material__outline_node=node,
            )
        except Question.DoesNotExist:
            return Response(
                {"detail": "Question not found in this outline node."},
                status=status.HTTP_404_NOT_FOUND,
            )

        decision = str(request.data.get("decision") or "").strip().casefold()
        if decision not in {"confirm", "change", "unpair"}:
            return Response(
                {"detail": "decision must be confirm, change, or unpair."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        existing = question.learning_object_links.filter(
            learning_object__material__generated_json__learning_objects_confirmed=True,
        ).select_related(
            "learning_object__group"
        ).order_by("-is_primary", "-relevance_score", "id").first()
        now = timezone.now()

        if decision == "change":
            try:
                group_id = int(request.data.get("learning_object_group_id"))
            except (TypeError, ValueError):
                return Response(
                    {"detail": "Select a valid learning-object group."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            try:
                group = LearningObjectGroup.objects.get(pk=group_id, outline_node=node)
            except LearningObjectGroup.DoesNotExist:
                return Response(
                    {"detail": "Learning-object group not found in this outline node."},
                    status=status.HTTP_404_NOT_FOUND,
                )
            confirmed_group_objects = group.learning_objects.filter(
                material__generated_json__learning_objects_confirmed=True,
            )
            representative = (
                confirmed_group_objects.filter(material=question.material).order_by("order", "id").first()
                or confirmed_group_objects.order_by("material_id", "order", "id").first()
            )
            if representative is None:
                return Response(
                    {"detail": "The selected concept group has no learning objects."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            question.learning_object_links.all().delete()
            QuestionLearningObjectLink.objects.create(
                question=question,
                learning_object=representative,
                relevance_score=0.0,
                method="teacher_selected",
                is_primary=True,
                review_status=QuestionLearningObjectLink.ReviewStatus.TEACHER_CONFIRMED,
                reviewed_at=now,
            )
        elif existing is None:
            return Response(
                {"detail": "No suggested question pairing is available to review."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        elif decision == "confirm":
            question.learning_object_links.exclude(pk=existing.pk).delete()
            existing.is_primary = True
            existing.review_status = QuestionLearningObjectLink.ReviewStatus.TEACHER_CONFIRMED
            existing.reviewed_at = now
            existing.save(update_fields=["is_primary", "review_status", "reviewed_at"])
        else:
            question.learning_object_links.exclude(pk=existing.pk).delete()
            existing.is_primary = False
            existing.review_status = QuestionLearningObjectLink.ReviewStatus.TEACHER_UNPAIRED
            existing.reviewed_at = now
            existing.save(update_fields=["is_primary", "review_status", "reviewed_at"])

        question.refresh_from_db()
        sync_question_to_adaptive(question)
        # Preserve the explicit teacher decision without rescoring every question.
        self._refresh_relationship_snapshots({question.material}, recompute=False)
        return Response(self._learning_resources_payload(node, request))

    @action(
        detail=True,
        methods=["get"],
        url_path=r"modules/(?P<module_id>[^/.]+)/materials",
    )
    def module_materials(self, request, pk=None, module_id=None):
        course = self.get_object()
        module = self._get_module_node(course, module_id)
        if module is None:
            return Response(
                {"detail": "Top-level module was not found for this course."},
                status=status.HTTP_404_NOT_FOUND,
            )

        materials = (
            course.materials.filter(module_node=module)
            .prefetch_related(
                "learning_objects",
                "questions__learning_object_links__learning_object",
            )
            .order_by("-created_at")
        )
        return Response(LearningMaterialSerializer(materials, many=True, context={"request": request}).data)

    def _serialize_course_detail(self, course, request):
        course = self.get_queryset().get(pk=course.pk)
        serializer = CourseDetailSerializer(course, context={"request": request})
        return Response(serializer.data)

    @action(detail=True, methods=["post"], url_path="outline-nodes")
    def create_outline_node(self, request, pk=None):
        course = self.get_object()
        serializer = OutlineNodeMutationSerializer(
            data=request.data,
            context={"course": course},
        )
        serializer.is_valid(raise_exception=True)

        parent = serializer.validated_data.get("parent")
        max_order = OutlineNode.objects.filter(course=course, parent=parent).aggregate(Max("order"))["order__max"]
        title = serializer.validated_data.get("title")
        if not title:
            return Response(
                {"detail": "Title is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        OutlineNode.objects.create(
            course=course,
            parent=parent,
            title=title,
            order=serializer.validated_data.get("order", (max_order or 0) + 1 if max_order is not None else 0),
            depth=parent.depth + 1 if parent else 0,
        )

        return self._serialize_course_detail(course, request)

    @action(
        detail=True,
        methods=["patch", "delete"],
        url_path=r"outline-nodes/(?P<node_id>[^/.]+)",
    )
    def outline_node_detail(self, request, pk=None, node_id=None):
        course = self.get_object()
        try:
            node = course.nodes.get(pk=node_id)
        except OutlineNode.DoesNotExist:
            return Response(
                {"detail": "Outline node not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if request.method == "DELETE":
            node.delete()
            return self._serialize_course_detail(course, request)

        serializer = OutlineNodeMutationSerializer(
            data=request.data,
            partial=True,
            context={"course": course},
        )
        serializer.is_valid(raise_exception=True)

        if "title" in serializer.validated_data:
            node.title = serializer.validated_data["title"]
        if "parent" in serializer.validated_data:
            parent = serializer.validated_data["parent"]
            if parent and parent.id == node.id:
                return Response(
                    {"detail": "A node cannot be its own parent."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            node.parent = parent
            node.depth = parent.depth + 1 if parent else 0
        if "order" in serializer.validated_data:
            node.order = serializer.validated_data["order"]

        node.save()
        return self._serialize_course_detail(course, request)

    @action(detail=True, methods=["post"], url_path="upload-outline")
    def upload_outline(self, request, pk=None):
        course = self.get_object()
        input_serializer = CourseOutlineUploadInputSerializer(data=request.data)
        if not input_serializer.is_valid():
            return Response(
                {"detail": str(input_serializer.errors["detail"][0])},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            _course, reused = upload_course_outline(
                course=course,
                **input_serializer.validated_data,
            )
        except PdfProcessingUseCaseError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        response = self._serialize_course_detail(course, request)
        response.data["upload_type"] = "outline"
        response.data["upload_reused"] = reused
        response.status_code = status.HTTP_200_OK if reused else status.HTTP_201_CREATED
        return response

    @action(detail=True, methods=["post"], url_path="upload-pdf")
    def upload_pdf(self, request, pk=None):
        """Accept one PDF and automatically route outlines and lesson materials."""
        course = self.get_object()
        input_serializer = CoursePdfUploadInputSerializer(data=request.data)
        if not input_serializer.is_valid():
            detail = input_serializer.errors.get("detail")
            if detail:
                return Response({"detail": str(detail[0])}, status=status.HTTP_400_BAD_REQUEST)
            return Response(input_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            upload_type, material, reused = upload_course_pdf(
                course=course,
                **input_serializer.validated_data,
            )
        except PdfProcessingUseCaseError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        response = self._serialize_course_detail(course, request)
        response.data["upload_type"] = upload_type
        response.data["upload_reused"] = reused
        if material is not None:
            response.data["uploaded_material_id"] = material.id
        response.status_code = status.HTTP_200_OK if reused else status.HTTP_201_CREATED
        return response

    @action(detail=True, methods=["delete"], url_path="delete-outline")
    def delete_outline(self, request, pk=None):
        course = self.get_object()
        for outline in course.outlines.all():
            if outline.outline_file:
                outline.outline_file.delete(save=False)
            outline.delete()

        course.nodes.all().delete()

        course = self.get_queryset().get(pk=course.pk)
        serializer = CourseDetailSerializer(course, context={"request": request})
        return Response(serializer.data)

    @action(detail=True, methods=["post"], url_path="confirm-outline")
    def confirm_outline(self, request, pk=None):
        course = self.get_object()
        try:
            confirm_course_outline(course=course)
        except PdfProcessingUseCaseError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return self._serialize_course_detail(course, request)

    @action(detail=True, methods=["post"], url_path="upload-material")
    def upload_material(self, request, pk=None):
        course = self.get_object()
        input_serializer = LearningMaterialUploadInputSerializer(data=request.data)
        if not input_serializer.is_valid():
            detail = input_serializer.errors.get("detail")
            if detail:
                return Response({"detail": str(detail[0])}, status=status.HTTP_400_BAD_REQUEST)
            return Response(input_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        try:
            material, reused = upload_learning_material(
                course=course, **input_serializer.validated_data
            )
        except PdfProcessingUseCaseError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        response = self._serialize_course_detail(course, request)
        response.data["uploaded_material_id"] = material.id
        response.data["upload_reused"] = reused
        response.status_code = status.HTTP_200_OK if reused else status.HTTP_201_CREATED
        return response

    def _get_course_material(self, course, material_id):
        try:
            return course.materials.get(pk=material_id)
        except LearningMaterial.DoesNotExist:
            return None

    @action(
        detail=True,
        methods=["delete"],
        url_path=r"materials/(?P<material_id>[^/.]+)",
    )
    def material_detail(self, request, pk=None, material_id=None):
        course = self.get_object()
        material = self._get_course_material(course, material_id)
        if material is None:
            return Response(
                {"detail": "Learning material not found for this course."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if material.pdf_file:
            material.pdf_file.delete(save=False)
        material.delete()

        course = self.get_queryset().get(pk=course.pk)
        serializer = CourseDetailSerializer(course, context={"request": request})
        return Response(serializer.data)

    def _learning_objects_snapshot(self, material):
        return [
            {
                "order": index,
                "type": "image_description" if learning_object.kind == LearningObject.Kind.IMAGE else "lesson_content",
                "kind": learning_object.kind,
                "learning_object_group_id": learning_object.group_id,
                "section_title": learning_object.section_title,
                "title": learning_object.title,
                "content": learning_object.content,
                "image_url": learning_object.image_url,
                "source_page": learning_object.source_page,
                "source_block_id": learning_object.source_block_id,
                "source_excerpt": learning_object.source_excerpt,
                "source": "teacher_reviewed",
                "learning_object_id": learning_object.id,
            }
            for index, learning_object in enumerate(
                item
                for item in material.learning_objects.all().order_by("order", "id")
                if not is_structural_metadata_label(item.title)
            )
        ]

    def _set_learning_objects_confirmed(self, material, confirmed):
        stale_ids = [
            item.id
            for item in material.learning_objects.all()
            if is_structural_metadata_label(item.title)
        ]
        if stale_ids:
            material.learning_objects.filter(id__in=stale_ids).delete()

        # Publish the confirmation state before relationship refresh so the
        # linker can never use draft learning objects as candidates.
        generated_json = material.generated_json or {}
        generated_json["learning_objects_confirmed"] = confirmed
        generated_json["learning_objects_confirmed_at"] = timezone.now().isoformat() if confirmed else None
        material.generated_json = generated_json
        material.save(update_fields=["generated_json"])

        refresh_material_learning_relationships(material)
        if material.outline_node_id:
            related_question_materials = (
                LearningMaterial.objects.filter(
                    outline_node_id=material.outline_node_id,
                    questions__isnull=False,
                )
                .exclude(pk=material.pk)
                .distinct()
            )
            for question_material in related_question_materials:
                refresh_material_learning_relationships(question_material)
                question_json = question_material.generated_json or {}
                question_json["questions"] = question_snapshots(question_material)
                question_material.generated_json = question_json
                question_material.save(update_fields=["generated_json"])

        generated_json = material.generated_json or {}
        learning_objects = self._learning_objects_snapshot(material)
        narration_script = build_narration_script_from_learning_objects(learning_objects)
        generated_json["learning_objects"] = learning_objects
        generated_json["narration_script"] = narration_script
        generated_json["lesson_playlist"] = build_lesson_playlist(narration_script)
        generated_json["questions"] = question_snapshots(material)
        generated_json["audio_playlist_generated"] = False
        generated_json["lesson_audio_generated"] = False
        material.generated_json = generated_json
        material.save(update_fields=["generated_json"])

    @action(
        detail=True,
        methods=["post"],
        url_path=r"materials/(?P<material_id>[^/.]+)/regenerate-outputs",
    )
    def regenerate_material_outputs(self, request, pk=None, material_id=None):
        course = self.get_object()
        material = self._get_course_material(course, material_id)
        if material is None:
            return Response(
                {"detail": "Learning material not found for this course."},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            regenerate_learning_material(material=material)
        except PdfProcessingUseCaseError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return self._serialize_course_detail(course, request)

    @action(
        detail=True,
        methods=["post"],
        url_path=r"materials/(?P<material_id>[^/.]+)/learning-objects",
    )
    def create_learning_object(self, request, pk=None, material_id=None):
        course = self.get_object()
        material = self._get_course_material(course, material_id)
        if material is None:
            return Response(
                {"detail": "Learning material not found for this course."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = LearningObjectMutationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not serializer.validated_data.get("title") or not serializer.validated_data.get("content"):
            return Response(
                {"detail": "Title and content are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        max_order = material.learning_objects.aggregate(Max("order"))["order__max"]
        LearningObject.objects.create(
            material=material,
            kind=LearningObject.Kind.TEXT,
            section_title=serializer.validated_data.get("section_title", ""),
            title=serializer.validated_data["title"],
            content=serializer.validated_data["content"],
            order=serializer.validated_data.get("order", (max_order or 0) + 1 if max_order is not None else 0),
        )
        self._set_learning_objects_confirmed(material, False)

        return self._serialize_course_detail(course, request)

    @action(
        detail=True,
        methods=["post"],
        url_path=r"materials/(?P<material_id>[^/.]+)/confirm-learning-objects",
    )
    def confirm_learning_objects(self, request, pk=None, material_id=None):
        course = self.get_object()
        material = self._get_course_material(course, material_id)
        if material is None:
            return Response(
                {"detail": "Learning material not found for this course."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not material.learning_objects.exists():
            return Response(
                {"detail": "Add at least one learning object before confirming."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        image_result = populate_missing_image_descriptions(material)
        self._set_learning_objects_confirmed(material, True)
        response = self._serialize_course_detail(course, request)
        response.data["image_description_generation"] = image_result
        return response

    @action(
        detail=True,
        methods=["post"],
        url_path=r"materials/(?P<material_id>[^/.]+)/regenerate-image-narrations",
    )
    def regenerate_image_narrations(self, request, pk=None, material_id=None):
        """Repair blank narration on saved image objects without re-uploading the PDF."""
        course = self.get_object()
        material = self._get_course_material(course, material_id)
        if material is None:
            return Response(
                {"detail": "Learning material not found for this course."},
                status=status.HTTP_404_NOT_FOUND,
            )

        image_result = populate_missing_image_descriptions(material)
        if image_result["generated_count"]:
            # Refresh narration and playlist snapshots while preserving whether
            # this material was already confirmed.
            confirmed = bool(
                (material.generated_json or {}).get("learning_objects_confirmed")
            )
            self._set_learning_objects_confirmed(material, confirmed)

        response = self._serialize_course_detail(course, request)
        response.data["image_description_generation"] = image_result
        return response

    @action(
        detail=True,
        methods=["post"],
        url_path=r"materials/(?P<material_id>[^/.]+)/generate-audio-playlist",
    )
    def generate_audio_playlist(self, request, pk=None, material_id=None):
        course = self.get_object()
        material = self._get_course_material(course, material_id)
        if material is None:
            return Response(
                {"detail": "Learning material not found for this course."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not material.learning_objects.exists():
            return Response(
                {"detail": "Add at least one learning object before generating audio."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        scope = request.data.get("scope", "all")
        try:
            result = generate_material_audio_playlist(material, scope=scope)
        except AudioGenerationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        course = self.get_queryset().get(pk=course.pk)
        serializer = CourseDetailSerializer(course, context={"request": request})
        return Response(
            {
                "message": "Audio playlist generated successfully.",
                "generated_count": result["generated_count"],
                "scope": result.get("scope", scope),
                "course": serializer.data,
            }
        )

    @action(
        detail=True,
        methods=["patch"],
        url_path=r"materials/(?P<material_id>[^/.]+)/classified-blocks/(?P<block_id>[^/.]+)",
    )
    def classified_block_detail(self, request, pk=None, material_id=None, block_id=None):
        course = self.get_object()
        material = self._get_course_material(course, material_id)
        if material is None:
            return Response(
                {"detail": "Learning material not found for this course."},
                status=status.HTTP_404_NOT_FOUND,
            )

        category = request.data.get("category")
        if category and category not in CLASSIFICATION_CATEGORIES:
            return Response({"detail": "Unsupported classification category."}, status=status.HTTP_400_BAD_REQUEST)

        include_in_narration = request.data.get("include_in_narration", None)
        if include_in_narration is not None and not isinstance(include_in_narration, bool):
            return Response({"detail": "include_in_narration must be true or false."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            apply_classification_override(
                material,
                block_id=int(block_id),
                category=category,
                include_in_narration=include_in_narration,
            )
        except (TypeError, ValueError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return self._serialize_course_detail(course, request)

    @action(
        detail=True,
        methods=["patch", "delete"],
        url_path=r"materials/(?P<material_id>[^/.]+)/learning-objects/(?P<object_id>[^/.]+)",
    )
    def learning_object_detail(self, request, pk=None, material_id=None, object_id=None):
        course = self.get_object()
        material = self._get_course_material(course, material_id)
        if material is None:
            return Response(
                {"detail": "Learning material not found for this course."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            learning_object = material.learning_objects.get(pk=object_id)
        except LearningObject.DoesNotExist:
            return Response(
                {"detail": "Learning object not found for this material."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if request.method == "DELETE":
            learning_object.delete()
            self._set_learning_objects_confirmed(material, False)
            return self._serialize_course_detail(course, request)

        serializer = LearningObjectMutationSerializer(
            learning_object,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        self._set_learning_objects_confirmed(material, False)
        return self._serialize_course_detail(course, request)

