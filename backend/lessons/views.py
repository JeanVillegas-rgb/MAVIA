import logging
from collections import defaultdict
from time import perf_counter

from django.db.models import Max, Prefetch
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

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
from .services.lesson_package import (
    build_course_module_summaries,
    build_module_package,
)
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
from user.permissions import IsTeacherOrAdmin


logger = logging.getLogger(__name__)


class CourseGroupViewSet(viewsets.ModelViewSet):
    # Course authoring is teacher/admin work. Students consume lessons through
    # the mobile app, not this API.
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

    # -- teacher review player -------------------------------------------------

    @action(detail=True, methods=["get"], url_path=r"review/modules")
    def review_modules(self, request, pk=None):
        """Module list for the web review section: audiobook-style playback +
        question counts per module."""
        course = self.get_object()
        return Response(build_course_module_summaries(course))

    @action(
        detail=True,
        methods=["get"],
        url_path=r"review/modules/(?P<module_id>[^/.]+)/package",
    )
    def review_module_package(self, request, pk=None, module_id=None):
        course = self.get_object()
        module_node = self._get_module_node(course, module_id)
        if module_node is None:
            return Response(
                {"detail": "Module not found for this course."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(build_module_package(module_node))

    @action(
        detail=True,
        methods=["post"],
        url_path=r"review/modules/(?P<module_id>[^/.]+)/mark-reviewed",
    )
    def mark_module_reviewed(self, request, pk=None, module_id=None):
        """Teacher-side only: stamp when a prof last listened through a module.
        Does not touch any student LearningState."""
        course = self.get_object()
        module_node = self._get_module_node(course, module_id)
        if module_node is None:
            return Response(
                {"detail": "Module not found for this course."},
                status=status.HTTP_404_NOT_FOUND,
            )
        related_info = dict(module_node.related_info or {})
        reviewed = not related_info.get("reviewed_at")
        related_info["reviewed_at"] = timezone.now().isoformat() if reviewed else None
        module_node.related_info = related_info
        module_node.save(update_fields=["related_info"])
        return Response({"reviewed": reviewed, "reviewed_at": related_info["reviewed_at"]})

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
            groups.append(
                {
                    "id": group.id,
                    "label": group.label,
                    "outline_node_id": node.id,
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

        self._refresh_relationship_snapshots({learning_object.material})
        for old_member in old_group_members:
            record_teacher_match_decision(
                learning_object,
                old_member,
                accepted=False,
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
            prompt=prompt,
            question_type=question_type,
            choices=choices,
            correct_answer=correct_answer,
            order=next_order,
            source_excerpt="Manually added by the teacher.",
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
        methods=["delete"],
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
        question.delete()

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
        """Generate lesson audio for every confirmed material and mark the topic published."""
        course = self.get_object()
        try:
            node = course.nodes.get(pk=node_id)
        except OutlineNode.DoesNotExist:
            return Response(
                {"detail": "Outline node not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        confirmed_materials = (
            LearningMaterial.objects.filter(
                course=course,
                outline_node=node,
                generated_json__learning_objects_confirmed=True,
            )
            .exclude(learning_objects__isnull=True)
            .distinct()
        )
        if not confirmed_materials.exists():
            return Response(
                {"detail": "Confirm at least one lesson file's learning objects before publishing."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        audio_generated_count = 0
        audio_errors = []
        for material in confirmed_materials:
            try:
                result = generate_material_audio_playlist(material, scope="lessons")
                audio_generated_count += result["generated_count"]
            except AudioGenerationError as exc:
                audio_errors.append(f"{material.title or material.pdf_file.name}: {exc}")

        node.published = True
        node.published_at = timezone.now()
        node.save(update_fields=["published", "published_at"])

        payload = self._learning_resources_payload(node, request)
        payload["publish"] = {
            "published": True,
            "published_at": node.published_at.isoformat(),
            "audio_generated_count": audio_generated_count,
            "materials_processed": confirmed_materials.count(),
            "audio_errors": audio_errors,
        }
        refreshed_course = self.get_queryset().get(pk=course.pk)
        payload["course"] = CourseDetailSerializer(refreshed_course, context={"request": request}).data
        return Response(payload)

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
            upload_course_outline(course=course, **input_serializer.validated_data)
        except PdfProcessingUseCaseError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        response = self._serialize_course_detail(course, request)
        response.status_code = status.HTTP_201_CREATED
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
        if material is not None:
            response.data["uploaded_material_id"] = material.id
            response.data["upload_reused"] = reused
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

        self._set_learning_objects_confirmed(material, True)
        return self._serialize_course_detail(course, request)

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

