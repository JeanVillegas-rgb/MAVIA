import logging
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db.models import Max
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from .models import CourseGroup, CourseOutline, LearningMaterial, LearningObject, OutlineNode
from .serializers import (
    CourseCreateSerializer,
    CourseDetailSerializer,
    CourseListSerializer,
    LearningMaterialSerializer,
    LearningObjectMutationSerializer,
    OutlineNodeMutationSerializer,
    OutlineNodeSerializer,
)
from .services.audio_generator import AudioGenerationError, generate_material_audio_playlist
from .services.content_generator import (
    apply_classification_override,
    build_lesson_playlist,
    build_narration_script_from_learning_objects,
    generate_material_outputs,
)
from .services.instructional_content_classifier import CLASSIFICATION_CATEGORIES
from .services.llm_client import LocalLLMError
from .services.outline_parser import build_dag_from_outline


logger = logging.getLogger(__name__)


class CourseGroupViewSet(viewsets.ModelViewSet):
    queryset = CourseGroup.objects.prefetch_related(
        "nodes",
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
        if not outline_file.name.lower().endswith(".pdf"):
            return Response(
                {"detail": "Only PDF course outlines are supported."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if hasattr(course, "outline"):
            course.outline.outline_file.delete(save=False)
            course.outline.delete()

        outline = CourseOutline.objects.create(course=course, outline_file=outline_file)
        extension = Path(outline_file.name).suffix
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
        elif not hasattr(course, "outline") or not course.outline.is_approved:
            return Response(
                {
                    "detail": "Select a module or topic before uploading lesson material, or confirm the course outline first for automatic classification."
                },
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
        generated_json["lesson_audio_generated"] = False
        generated_json["question_audio_generated"] = False
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
        serializer.save(kind=LearningObject.Kind.TEXT, image_prompt="")
        self._set_learning_objects_confirmed(material, False)
        return self._serialize_course_detail(course, request)

