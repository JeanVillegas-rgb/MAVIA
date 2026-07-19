import logging
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db.models import Max
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from .models import ConceptPrerequisiteEdge, CourseGroup, CourseOutline, ExtractedConcept, LearningMaterial, LearningObject, OutlineNode
from .serializers import (
    ConceptPrerequisiteEdgeSerializer,
    ConceptPrerequisiteEdgeWriteSerializer,
    CourseCreateSerializer,
    CourseDetailSerializer,
    CourseListSerializer,
    ExtractedConceptSerializer,
    ExtractedConceptWriteSerializer,
    LearningMaterialSerializer,
    LearningObjectMutationSerializer,
    OutlineEdgeSerializer,
    OutlineNodeMutationSerializer,
    OutlineNodeSerializer,
)
from .services.concept_dag_state import confirm_module_dag, get_or_create_module_dag_state, invalidate_module_dag
from .services.concept_edge_generator import (
    edge_would_create_cycle,
    generate_module_concept_dag,
    get_unlocked_concepts,
    is_module_concept_dag_acyclic,
    validate_manual_concept_edge,
)
from .services.concept_extractor import ConceptExtractionError, extract_module_concepts
from .services.audio_generator import AudioGenerationError, generate_material_audio_playlist
from .services.content_generator import (
    apply_classification_override,
    build_lesson_playlist,
    build_narration_script_from_learning_objects,
    generate_material_outputs,
)
from .services.edge_generator import generate_prerequisite_edges
from .services.instructional_content_classifier import CLASSIFICATION_CATEGORIES
from .services.llm_client import LocalLLMError
from .services.outline_parser import build_dag_from_outline


logger = logging.getLogger(__name__)


def _get_descendant_ids(module_node):
    descendant_ids = set()
    stack = list(module_node.children.all())
    while stack:
        node = stack.pop()
        descendant_ids.add(node.id)
        stack.extend(node.children.all())
    return descendant_ids


def get_concept_readiness_summary(concepts):
    total = concepts.count()
    pending = concepts.filter(validation_status=ExtractedConcept.ValidationStatus.PENDING).count()
    approved = concepts.filter(validation_status=ExtractedConcept.ValidationStatus.APPROVED).count()
    rejected = concepts.filter(validation_status=ExtractedConcept.ValidationStatus.REJECTED).count()
    manual = concepts.filter(is_manual=True).count()
    return {
        "total": total,
        "pending": pending,
        "approved": approved,
        "rejected": rejected,
        "manual": manual,
        "ready_for_dag": approved >= 2 and pending == 0,
    }


class CourseGroupViewSet(viewsets.ModelViewSet):
    queryset = CourseGroup.objects.prefetch_related(
        "nodes",
        "outline_edges",
        "outline",
        "materials__learning_objects",
    ).all()
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_serializer_class(self):
        if self.action in {"list"}:
            return CourseListSerializer
        if self.action == "create":
            return CourseCreateSerializer
        return CourseDetailSerializer

    @action(detail=True, methods=["get"], url_path="dag")
    def dag(self, request, pk=None):
        course = self.get_object()
        nodes = course.nodes.all()
        edges = course.outline_edges.all()

        return Response(
            {
                "nodes": OutlineNodeSerializer(nodes, many=True, context={"request": request}).data,
                "edges": OutlineEdgeSerializer(edges, many=True, context={"request": request}).data,
            }
        )

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
            .prefetch_related("learning_objects")
            .order_by("-created_at")
        )
        return Response(LearningMaterialSerializer(materials, many=True, context={"request": request}).data)

    def _serialize_module_concepts(self, request, module):
        concepts = (
            module.extracted_concepts.select_related("course", "module_node")
            .prefetch_related("sources__learning_material")
            .order_by("order", "id")
        )
        return {
            "summary": get_concept_readiness_summary(concepts),
            "concepts": ExtractedConceptSerializer(concepts, many=True, context={"request": request}).data,
        }

    @action(
        detail=True,
        methods=["get", "post"],
        url_path=r"modules/(?P<module_id>[^/.]+)/concepts",
    )

    def module_concepts(self, request, pk=None, module_id=None):
        course = self.get_object()
        module = self._get_module_node(course, module_id)
        if module is None:
            return Response(
                {"detail": "Top-level module was not found for this course."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if request.method == "POST":
            serializer = ExtractedConceptWriteSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            if not serializer.validated_data.get("canonical_title"):
                return Response(
                    {"canonical_title": ["This field is required."]},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            max_order = module.extracted_concepts.aggregate(Max("order"))["order__max"]
            concept = ExtractedConcept(
                course=course,
                module_node=module,
                canonical_title=serializer.validated_data["canonical_title"],
                description=serializer.validated_data.get("description", ""),
                order=serializer.validated_data.get("order", (max_order or 0) + 1 if max_order is not None else 0),
                validation_status=ExtractedConcept.ValidationStatus.APPROVED,
                is_manual=True,
            )
            try:
                concept.full_clean()
                concept.save()
                invalidate_module_dag(course, module, "A concept was added.")
            except ValidationError as exc:
                return Response(exc.message_dict if hasattr(exc, "message_dict") else {"detail": exc.messages}, status=status.HTTP_400_BAD_REQUEST)
            return Response(
                ExtractedConceptSerializer(concept, context={"request": request}).data,
                status=status.HTTP_201_CREATED,
            )

        return Response(self._serialize_module_concepts(request, module))

    def _get_module_concept(self, course, module, concept_id):
        try:
            return ExtractedConcept.objects.select_related("course", "module_node").prefetch_related(
                "sources__learning_material"
            ).get(pk=concept_id, course=course, module_node=module)
        except ExtractedConcept.DoesNotExist:
            return None

    @action(
        detail=True,
        methods=["get", "patch", "delete"],
        url_path=r"modules/(?P<module_id>[^/.]+)/concepts/(?P<concept_id>[^/.]+)",
    )
    def module_concept_detail(self, request, pk=None, module_id=None, concept_id=None):
        course = self.get_object()
        module = self._get_module_node(course, module_id)
        if module is None:
            return Response(
                {"detail": "Top-level module was not found for this course."},
                status=status.HTTP_404_NOT_FOUND,
            )

        concept = self._get_module_concept(course, module, concept_id)
        if concept is None:
            return Response(
                {"detail": "Concept was not found for this module."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if request.method == "GET":
            return Response(ExtractedConceptSerializer(concept, context={"request": request}).data)

        if request.method == "DELETE":
            concept.delete()
            invalidate_module_dag(course, module, "A concept was deleted.")
            return Response({"message": "Concept deleted successfully."})

        serializer = ExtractedConceptWriteSerializer(concept, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        for field in ["canonical_title", "description", "order"]:
            if field in serializer.validated_data:
                setattr(concept, field, serializer.validated_data[field])
        try:
            concept.full_clean()
            concept.save()
            invalidate_module_dag(course, module, "A concept was edited.")
        except ValidationError as exc:
            return Response(exc.message_dict if hasattr(exc, "message_dict") else {"detail": exc.messages}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ExtractedConceptSerializer(concept, context={"request": request}).data)

    @action(
        detail=True,
        methods=["post"],
        url_path=r"modules/(?P<module_id>[^/.]+)/concepts/(?P<concept_id>[^/.]+)/approve",
    )
    def approve_module_concept(self, request, pk=None, module_id=None, concept_id=None):
        course = self.get_object()
        module = self._get_module_node(course, module_id)
        if module is None:
            return Response({"detail": "Top-level module was not found for this course."}, status=status.HTTP_404_NOT_FOUND)
        concept = self._get_module_concept(course, module, concept_id)
        if concept is None:
            return Response({"detail": "Concept was not found for this module."}, status=status.HTTP_404_NOT_FOUND)
        concept.validation_status = ExtractedConcept.ValidationStatus.APPROVED
        concept.save(update_fields=["validation_status", "updated_at"])
        invalidate_module_dag(course, module, "A concept was approved.")
        return Response(ExtractedConceptSerializer(concept, context={"request": request}).data)

    @action(
        detail=True,
        methods=["post"],
        url_path=r"modules/(?P<module_id>[^/.]+)/concepts/(?P<concept_id>[^/.]+)/reject",
    )
    def reject_module_concept(self, request, pk=None, module_id=None, concept_id=None):
        course = self.get_object()
        module = self._get_module_node(course, module_id)
        if module is None:
            return Response({"detail": "Top-level module was not found for this course."}, status=status.HTTP_404_NOT_FOUND)
        concept = self._get_module_concept(course, module, concept_id)
        if concept is None:
            return Response({"detail": "Concept was not found for this module."}, status=status.HTTP_404_NOT_FOUND)
        concept.validation_status = ExtractedConcept.ValidationStatus.REJECTED
        concept.save(update_fields=["validation_status", "updated_at"])
        invalidate_module_dag(course, module, "A concept was rejected.")
        return Response(ExtractedConceptSerializer(concept, context={"request": request}).data)

    @action(
        detail=True,
        methods=["post"],
        url_path=r"modules/(?P<module_id>[^/.]+)/approve-concepts",
    )
    def approve_module_concepts(self, request, pk=None, module_id=None):
        course = self.get_object()
        module = self._get_module_node(course, module_id)
        if module is None:
            return Response(
                {"detail": "Top-level module was not found for this course."},
                status=status.HTTP_404_NOT_FOUND,
            )

        concepts = module.extracted_concepts.all()
        approved_count = concepts.filter(validation_status=ExtractedConcept.ValidationStatus.PENDING).update(
            validation_status=ExtractedConcept.ValidationStatus.APPROVED,
            updated_at=timezone.now(),
        )
        if approved_count:
            invalidate_module_dag(course, module, "Module concepts were approved.")
        refreshed = (
            module.extracted_concepts.select_related("course", "module_node")
            .prefetch_related("sources__learning_material")
            .order_by("order", "id")
        )
        return Response(
            {
                "message": "Pending concepts approved successfully.",
                "approved_count": approved_count,
                "already_approved_count": refreshed.filter(
                    validation_status=ExtractedConcept.ValidationStatus.APPROVED
                ).count()
                - approved_count,
                "rejected_count": refreshed.filter(validation_status=ExtractedConcept.ValidationStatus.REJECTED).count(),
                "concepts": ExtractedConceptSerializer(refreshed, many=True, context={"request": request}).data,
            }
        )

    @action(
        detail=True,
        methods=["post"],
        url_path=r"modules/(?P<module_id>[^/.]+)/extract-concepts",
    )
    def extract_module_concepts(self, request, pk=None, module_id=None):
        course = self.get_object()
        module = self._get_module_node(course, module_id)
        if module is None:
            return Response(
                {"detail": "Top-level module was not found for this course."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not course.materials.filter(module_node=module).exists():
            return Response(
                {"detail": "Upload at least one PDF material under this module before extracting concepts."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = extract_module_concepts(module)
        except ConceptExtractionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            logger.exception("Unexpected concept extraction failure for course %s module %s", course.id, module.id)
            return Response(
                {"detail": "Concepts could not be extracted from this module."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        module = course.nodes.get(pk=module.pk)
        if result["concepts_created"] or result["concepts_updated"]:
            invalidate_module_dag(course, module, "Module concepts were extracted or updated.")
        return Response(
            {
                "module": OutlineNodeSerializer(module, context={"request": request}).data,
                "materials_processed": result["materials_processed"],
                "concepts_created": result["concepts_created"],
                "concepts_updated": result["concepts_updated"],
                "duplicates_merged": result["duplicates_merged"],
                "summary": get_concept_readiness_summary(result["concepts"]),
                "concepts": ExtractedConceptSerializer(
                    result["concepts"],
                    many=True,
                    context={"request": request},
                ).data,
            }
        )

    def _serialize_module_concept_dag(self, request, course, module):
        state = get_or_create_module_dag_state(course, module)
        concepts = (
            ExtractedConcept.objects.filter(
                course=course,
                module_node=module,
                validation_status=ExtractedConcept.ValidationStatus.APPROVED,
            )
            .prefetch_related("sources__learning_material")
            .order_by("order", "id")
        )
        edges = (
            ConceptPrerequisiteEdge.objects.filter(course=course, module_node=module)
            .select_related("source", "target")
            .order_by("source__order", "target__order", "id")
        )
        nodes = []
        for concept in concepts:
            sources = list(concept.sources.all())
            nodes.append(
                {
                    "id": concept.id,
                    "title": concept.canonical_title,
                    "description": concept.description,
                    "validation_status": concept.validation_status,
                    "material_ids": sorted({source.learning_material_id for source in sources}),
                    "source_count": len(sources),
                    "order": concept.order,
                    "is_manual": concept.is_manual,
                }
            )
        return {
            "module": {
                "id": module.id,
                "title": module.title,
            },
            "confirmed": state.is_confirmed,
            "state": {
                "is_confirmed": state.is_confirmed,
                "confirmed_at": state.confirmed_at,
                "invalidated_at": state.invalidated_at,
                "invalidation_reason": state.invalidation_reason,
            },
            "is_acyclic": is_module_concept_dag_acyclic(module),
            "nodes": nodes,
            "edges": ConceptPrerequisiteEdgeSerializer(edges, many=True, context={"request": request}).data,
        }

    def _get_module_concept_edge(self, course, module, edge_id):
        try:
            return ConceptPrerequisiteEdge.objects.select_related("source", "target").get(
                pk=edge_id,
                course=course,
                module_node=module,
            )
        except ConceptPrerequisiteEdge.DoesNotExist:
            return None

    @action(
        detail=True,
        methods=["get"],
        url_path=r"modules/(?P<module_id>[^/.]+)/concept-dag",
    )
    def module_concept_dag(self, request, pk=None, module_id=None):
        course = self.get_object()
        module = self._get_module_node(course, module_id)
        if module is None:
            return Response({"detail": "Top-level module was not found for this course."}, status=status.HTTP_404_NOT_FOUND)
        return Response(self._serialize_module_concept_dag(request, course, module))

    @action(
        detail=True,
        methods=["post"],
        url_path=r"modules/(?P<module_id>[^/.]+)/generate-concept-dag",
    )
    def generate_module_concept_dag(self, request, pk=None, module_id=None):
        course = self.get_object()
        module = self._get_module_node(course, module_id)
        if module is None:
            return Response({"detail": "Top-level module was not found for this course."}, status=status.HTTP_404_NOT_FOUND)

        try:
            threshold = request.data.get("threshold") if isinstance(request.data, dict) else None
            summary = generate_module_concept_dag(
                course,
                module,
                regenerate=bool(request.data.get("regenerate", False)) if isinstance(request.data, dict) else False,
                threshold=threshold,
            )
        except RuntimeError:
            logger.exception("Concept prerequisite model unavailable for course %s module %s", course.id, module.id)
            return Response(
                {"detail": "The semantic similarity model could not be loaded."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except (ValueError, ValidationError) as exc:
            detail = exc.message_dict if hasattr(exc, "message_dict") else {"detail": getattr(exc, "messages", [str(exc)])}
            return Response(detail, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            logger.exception("Unexpected concept DAG generation failure for course %s module %s", course.id, module.id)
            return Response({"detail": "Concept learner path could not be generated."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response(
            {
                "message": "Concept learner path generated successfully.",
                "summary": summary,
                **self._serialize_module_concept_dag(request, course, module),
            }
        )

    @action(
        detail=True,
        methods=["post"],
        url_path=r"modules/(?P<module_id>[^/.]+)/concept-edges",
    )
    def create_module_concept_edge(self, request, pk=None, module_id=None):
        course = self.get_object()
        module = self._get_module_node(course, module_id)
        if module is None:
            return Response({"detail": "Top-level module was not found for this course."}, status=status.HTTP_404_NOT_FOUND)

        serializer = ConceptPrerequisiteEdgeWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        source = serializer.validated_data.get("source")
        target = serializer.validated_data.get("target")
        if source is None or target is None:
            return Response({"detail": "source and target are required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            validate_manual_concept_edge(course, module, source, target)
            edge = ConceptPrerequisiteEdge(
                course=course,
                module_node=module,
                source=source,
                target=target,
                score=1.0,
                semantic_similarity=None,
                dependency_cue_score=None,
                source_order_score=None,
                title_overlap_score=None,
                instructional_order_score=None,
                explanation=serializer.validated_data.get("explanation", "Teacher-created prerequisite edge."),
                validation_status=serializer.validated_data.get(
                    "validation_status",
                    ConceptPrerequisiteEdge.ValidationStatus.APPROVED,
                ),
                is_manual=True,
            )
            edge.full_clean()
            edge.save()
            invalidate_module_dag(course, module, "A manual concept edge was added.")
        except ValidationError as exc:
            return Response(exc.message_dict if hasattr(exc, "message_dict") else {"detail": exc.messages}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ConceptPrerequisiteEdgeSerializer(edge, context={"request": request}).data, status=status.HTTP_201_CREATED)

    @action(
        detail=True,
        methods=["patch", "delete"],
        url_path=r"modules/(?P<module_id>[^/.]+)/concept-edges/(?P<edge_id>[^/.]+)",
    )
    def module_concept_edge_detail(self, request, pk=None, module_id=None, edge_id=None):
        course = self.get_object()
        module = self._get_module_node(course, module_id)
        if module is None:
            return Response({"detail": "Top-level module was not found for this course."}, status=status.HTTP_404_NOT_FOUND)

        edge = self._get_module_concept_edge(course, module, edge_id)
        if edge is None:
            return Response({"detail": "Concept prerequisite edge was not found for this module."}, status=status.HTTP_404_NOT_FOUND)

        if request.method == "DELETE":
            if not edge.is_manual:
                return Response({"detail": "Only manual concept edges can be deleted."}, status=status.HTTP_400_BAD_REQUEST)
            edge.delete()
            invalidate_module_dag(course, module, "A manual concept edge was deleted.")
            return Response({"message": "Concept prerequisite edge deleted successfully."})

        serializer = ConceptPrerequisiteEdgeWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        source = serializer.validated_data.get("source", edge.source)
        target = serializer.validated_data.get("target", edge.target)
        try:
            if source.id != edge.source_id or target.id != edge.target_id:
                validate_manual_concept_edge(course, module, source, target, edge_id=edge.id)
                edge.source = source
                edge.target = target
                edge.is_manual = True
            if "validation_status" in serializer.validated_data:
                next_status = serializer.validated_data["validation_status"]
                if next_status != ConceptPrerequisiteEdge.ValidationStatus.REJECTED:
                    validate_manual_concept_edge(course, module, edge.source, edge.target, edge_id=edge.id)
                edge.validation_status = next_status
            if "explanation" in serializer.validated_data:
                edge.explanation = serializer.validated_data["explanation"]
            edge.full_clean()
            edge.save()
            invalidate_module_dag(course, module, "A concept edge was changed.")
        except ValidationError as exc:
            return Response(exc.message_dict if hasattr(exc, "message_dict") else {"detail": exc.messages}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ConceptPrerequisiteEdgeSerializer(edge, context={"request": request}).data)

    @action(
        detail=True,
        methods=["post"],
        url_path=r"modules/(?P<module_id>[^/.]+)/confirm-concept-dag",
    )
    def confirm_module_concept_dag(self, request, pk=None, module_id=None):
        course = self.get_object()
        module = self._get_module_node(course, module_id)
        if module is None:
            return Response({"detail": "Top-level module was not found for this course."}, status=status.HTTP_404_NOT_FOUND)

        pending_count = ConceptPrerequisiteEdge.objects.filter(
            course=course,
            module_node=module,
            validation_status=ConceptPrerequisiteEdge.ValidationStatus.PENDING,
        ).count()
        if pending_count:
            return Response(
                {"detail": "Resolve all pending concept edges before confirming the learner path."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not is_module_concept_dag_acyclic(module):
            return Response({"detail": "The concept learner path contains a cycle."}, status=status.HTTP_400_BAD_REQUEST)
        state = confirm_module_dag(course, module)
        return Response(
            {
                "message": "Concept learner path confirmed.",
                "state": {
                    "is_confirmed": state.is_confirmed,
                    "confirmed_at": state.confirmed_at,
                    "invalidated_at": state.invalidated_at,
                    "invalidation_reason": state.invalidation_reason,
                },
                **self._serialize_module_concept_dag(request, course, module),
            }
        )

    @action(
        detail=True,
        methods=["post"],
        url_path=r"modules/(?P<module_id>[^/.]+)/unlocked-concepts",
    )
    def unlocked_module_concepts(self, request, pk=None, module_id=None):
        course = self.get_object()
        module = self._get_module_node(course, module_id)
        if module is None:
            return Response({"detail": "Top-level module was not found for this course."}, status=status.HTTP_404_NOT_FOUND)
        mastered_ids = request.data.get("mastered_concept_ids", [])
        if not isinstance(mastered_ids, list):
            return Response({"detail": "mastered_concept_ids must be a list."}, status=status.HTTP_400_BAD_REQUEST)
        concepts = get_unlocked_concepts(module, mastered_ids)
        return Response(
            {
                "concepts": ExtractedConceptSerializer(concepts, many=True, context={"request": request}).data,
            }
        )

    def _serialize_module_dag(self, request, course, module):
        descendant_ids = _get_descendant_ids(module)
        nodes = course.nodes.filter(id__in=descendant_ids).order_by("depth", "order", "id")
        edges = course.outline_edges.filter(
            source_id__in=descendant_ids,
            target_id__in=descendant_ids,
        )
        return {
            "module": OutlineNodeSerializer(module, context={"request": request}).data,
            "nodes": OutlineNodeSerializer(nodes, many=True, context={"request": request}).data,
            "edges": OutlineEdgeSerializer(edges, many=True, context={"request": request}).data,
        }

    @action(
        detail=True,
        methods=["get"],
        url_path=r"modules/(?P<module_id>[^/.]+)/dag",
    )
    def module_dag(self, request, pk=None, module_id=None):
        course = self.get_object()
        module = self._get_module_node(course, module_id)
        if module is None:
            return Response(
                {"detail": "Top-level module was not found for this course."},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(self._serialize_module_dag(request, course, module))

    @action(
        detail=True,
        methods=["post"],
        url_path=r"modules/(?P<module_id>[^/.]+)/generate-dag",
    )
    def generate_module_dag(self, request, pk=None, module_id=None):
        course = self.get_object()
        module = self._get_module_node(course, module_id)
        if module is None:
            return Response(
                {"detail": "Top-level module was not found for this course."},
                status=status.HTTP_404_NOT_FOUND,
            )

        descendant_ids = _get_descendant_ids(module)
        if len(descendant_ids) < 2:
            return Response(
                {"detail": "This module needs at least two lesson concepts before a DAG can be generated."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            summary = generate_prerequisite_edges(course, module_node=module)
        except RuntimeError:
            logger.exception("Prerequisite edge generation model unavailable for course %s module %s", course.id, module.id)
            return Response(
                {
                    "detail": (
                        "The semantic similarity model could not be loaded. "
                        "Please try again after the model is installed or cached."
                    )
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except Exception:
            logger.exception("Unexpected prerequisite edge generation failure for course %s module %s", course.id, module.id)
            return Response(
                {"detail": "Candidate prerequisite edges could not be generated."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        course = self.get_queryset().get(pk=course.pk)
        module = course.nodes.get(pk=module.pk)
        return Response(
            {
                "message": "Candidate prerequisite edges generated successfully.",
                "summary": summary,
                **self._serialize_module_dag(request, course, module),
            }
        )

    @action(detail=True, methods=["post"], url_path="generate-dag")
    def generate_dag(self, request, pk=None):
        course = self.get_object()
        if not course.nodes.exists():
            return Response(
                {"detail": "Generate or upload an outline before generating prerequisite edges."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            summary = generate_prerequisite_edges(course)
        except RuntimeError as exc:
            logger.exception("Prerequisite edge generation model unavailable for course %s", course.id)
            return Response(
                {
                    "detail": (
                        "The semantic similarity model could not be loaded. "
                        "Please try again after the model is installed or cached."
                    )
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except Exception:
            logger.exception("Unexpected prerequisite edge generation failure for course %s", course.id)
            return Response(
                {"detail": "Candidate prerequisite edges could not be generated."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        course = self.get_queryset().get(pk=course.pk)
        nodes = course.nodes.all()
        edges = course.outline_edges.all()

        return Response(
            {
                "message": "Candidate prerequisite edges generated successfully.",
                "summary": summary,
                "nodes": OutlineNodeSerializer(nodes, many=True, context={"request": request}).data,
                "edges": OutlineEdgeSerializer(edges, many=True, context={"request": request}).data,
            }
        )

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
        outline_file = request.FILES.get("outline_file")
        if not outline_file:
            return Response(
                {"detail": "outline_file is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if hasattr(course, "outline"):
            course.outline.outline_file.delete(save=False)
            course.outline.delete()

        outline = CourseOutline.objects.create(course=course, outline_file=outline_file)
        extension = Path(outline_file.name).suffix or ".txt"
        try:
            build_dag_from_outline(course, outline.outline_file.path, extension)
        except LocalLLMError as exc:
            outline.delete()
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        serializer = CourseDetailSerializer(course, context={"request": request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["delete"], url_path="delete-outline")
    def delete_outline(self, request, pk=None):
        course = self.get_object()
        try:
            outline = course.outline
        except CourseOutline.DoesNotExist:
            outline = None

        if outline:
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
            outline = course.outline
        except CourseOutline.DoesNotExist:
            return Response(
                {"detail": "Upload an outline before confirming the hierarchy."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not course.nodes.exists():
            return Response(
                {"detail": "The outline has no extracted topics to confirm."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        outline.is_approved = True
        outline.approved_at = timezone.now()
        outline.save(update_fields=["is_approved", "approved_at"])

        course = self.get_queryset().get(pk=course.pk)
        serializer = CourseDetailSerializer(course, context={"request": request})
        return Response(serializer.data)

    @action(detail=True, methods=["post"], url_path="upload-material")
    def upload_material(self, request, pk=None):
        course = self.get_object()
        pdf_file = request.FILES.get("pdf_file")
        if not pdf_file:
            return Response(
                {"detail": "pdf_file is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not pdf_file.name.lower().endswith(".pdf"):
            return Response(
                {"detail": "Only PDF learning materials are supported."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        outline_node = None
        outline_node_id = request.data.get("outline_node_id")
        if outline_node_id:
            try:
                outline_node = course.nodes.get(pk=outline_node_id)
            except OutlineNode.DoesNotExist:
                return Response(
                    {"detail": "Selected outline node was not found for this course."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        module_node = None
        module_node_id = request.data.get("module_node_id")
        if module_node_id:
            module_node = self._get_module_node(course, module_node_id)
            if module_node is None:
                return Response(
                    {"detail": "Selected module was not found for this course."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        elif outline_node is not None:
            module_node = outline_node
            while module_node.parent_id is not None:
                module_node = module_node.parent
        else:
            return Response(
                {"detail": "Select a module or topic before uploading lesson material."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        material = LearningMaterial.objects.create(
            course=course,
            outline_node=outline_node,
            module_node=module_node,
            title=request.data.get("title") or Path(pdf_file.name).stem,
            pdf_file=pdf_file,
        )
        generate_material_outputs(material)

        course = self.get_queryset().get(pk=course.pk)
        serializer = CourseDetailSerializer(course, context={"request": request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def _get_course_material(self, course, material_id):
        try:
            return course.materials.get(pk=material_id)
        except LearningMaterial.DoesNotExist:
            return None

    def _learning_objects_snapshot(self, material):
        return [
            {
                "order": index,
                "title": learning_object.title,
                "content": learning_object.content,
                "source": "teacher_reviewed",
                "learning_object_id": learning_object.id,
            }
            for index, learning_object in enumerate(material.learning_objects.all().order_by("order", "id"))
        ]

    def _set_learning_objects_confirmed(self, material, confirmed):
        generated_json = material.generated_json or {}
        learning_objects = self._learning_objects_snapshot(material)
        narration_script = build_narration_script_from_learning_objects(learning_objects)
        generated_json["learning_objects"] = learning_objects
        generated_json["narration_script"] = narration_script
        generated_json["lesson_playlist"] = build_lesson_playlist(narration_script)
        generated_json["audio_playlist_generated"] = False
        generated_json["learning_objects_confirmed"] = confirmed
        generated_json["learning_objects_confirmed_at"] = timezone.now().isoformat() if confirmed else None
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
        if not material.pdf_file:
            return Response(
                {"detail": "This material has no PDF file to regenerate."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        material.status = LearningMaterial.Status.PROCESSING
        material.error_message = ""
        material.save(update_fields=["status", "error_message"])
        generate_material_outputs(material)

        course = self.get_queryset().get(pk=course.pk)
        serializer = CourseDetailSerializer(course, context={"request": request})
        return Response(serializer.data)

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

        try:
            result = generate_material_audio_playlist(material)
        except AudioGenerationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        course = self.get_queryset().get(pk=course.pk)
        serializer = CourseDetailSerializer(course, context={"request": request})
        return Response(
            {
                "message": "Audio playlist generated successfully.",
                "generated_count": result["generated_count"],
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
        serializer.save(kind=LearningObject.Kind.TEXT, image_prompt="")
        self._set_learning_objects_confirmed(material, False)
        return self._serialize_course_detail(course, request)
