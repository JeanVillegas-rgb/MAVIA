from rest_framework import serializers

from .models import (
    CourseGroup,
    LearningMaterial,
    LearningObject,
    OutlineNode,
)
from .services.audio_generator import remove_missing_audio_urls
from .services.content_generator import is_structural_metadata_label


class OutlineNodeSerializer(serializers.ModelSerializer):
    class Meta:
        model = OutlineNode
        fields = ["id", "title", "depth", "order", "parent", "related_info"]


class OutlineNodeMutationSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255, required=False)
    parent = serializers.PrimaryKeyRelatedField(
        queryset=OutlineNode.objects.all(),
        required=False,
        allow_null=True,
    )
    order = serializers.IntegerField(required=False, min_value=0)

    def validate_title(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Title cannot be blank.")
        return value

    def validate_parent(self, value):
        course = self.context["course"]
        if value is not None and value.course_id != course.id:
            raise serializers.ValidationError("Parent node must belong to this course.")
        return value


class OutlineHierarchyNodeSerializer(serializers.ModelSerializer):
    children = serializers.SerializerMethodField()
    lesson_pdf_count = serializers.SerializerMethodField()
    has_lesson_pdf = serializers.SerializerMethodField()

    class Meta:
        model = OutlineNode
        fields = [
            "id",
            "title",
            "order",
            "depth",
            "parent",
            "related_info",
            "lesson_pdf_count",
            "has_lesson_pdf",
            "children",
        ]

    def get_children(self, obj):
        children = obj.children.all()
        return OutlineHierarchyNodeSerializer(children, many=True, context=self.context).data

    def get_lesson_pdf_count(self, obj):
        return obj.materials.count()

    def get_has_lesson_pdf(self, obj):
        return self.get_lesson_pdf_count(obj) > 0


class CourseListSerializer(serializers.ModelSerializer):
    node_count = serializers.SerializerMethodField()
    has_outline = serializers.SerializerMethodField()
    outline_approved = serializers.SerializerMethodField()

    class Meta:
        model = CourseGroup
        fields = [
            "id",
            "title",
            "description",
            "node_count",
            "has_outline",
            "outline_approved",
            "created_at",
        ]

    def get_node_count(self, obj):
        if not self.get_outline_approved(obj):
            return 0
        return obj.nodes.count()

    def get_has_outline(self, obj):
        return hasattr(obj, "outline")

    def get_outline_approved(self, obj):
        return hasattr(obj, "outline") and obj.outline.is_approved


class LearningObjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = LearningObject
        fields = ["id", "kind", "section_title", "title", "content", "image_url", "order"]


class LearningObjectMutationSerializer(serializers.ModelSerializer):
    class Meta:
        model = LearningObject
        fields = ["section_title", "title", "content", "image_url", "order"]
        extra_kwargs = {
            "section_title": {"required": False},
            "title": {"required": False},
            "content": {"required": False},
            "image_url": {"required": False},
            "order": {"required": False},
        }

    def validate_title(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Title cannot be blank.")
        return value

    def validate_section_title(self, value):
        return value.strip()


class LearningMaterialSerializer(serializers.ModelSerializer):
    learning_objects = serializers.SerializerMethodField()
    filename = serializers.SerializerMethodField()
    generated_json = serializers.SerializerMethodField()
    outline_node_title = serializers.SerializerMethodField()
    outline_node_path = serializers.SerializerMethodField()
    module_node_title = serializers.SerializerMethodField()

    class Meta:
        model = LearningMaterial
        fields = [
            "id",
            "title",
            "filename",
            "outline_node",
            "outline_node_title",
            "outline_node_path",
            "module_node",
            "module_node_title",
            "status",
            "error_message",
            "generated_json",
            "created_at",
            "learning_objects",
        ]

    def get_filename(self, obj):
        return obj.pdf_file.name.split("/")[-1] if obj.pdf_file else ""

    def get_generated_json(self, obj):
        return remove_missing_audio_urls(obj)

    def get_learning_objects(self, obj):
        learning_objects = obj.learning_objects.all().order_by("order", "id")
        learning_objects = [
            item
            for item in learning_objects
            if not is_structural_metadata_label(item.title)
        ]
        return LearningObjectSerializer(learning_objects, many=True, context=self.context).data

    def get_outline_node_title(self, obj):
        return obj.outline_node.title if obj.outline_node else None

    def get_outline_node_path(self, obj):
        if not obj.outline_node:
            return []

        path = []
        node = obj.outline_node
        while node is not None:
            path.append({"id": node.id, "title": node.title, "depth": node.depth})
            node = node.parent
        return list(reversed(path))

    def get_module_node_title(self, obj):
        return obj.module_node.title if obj.module_node else None


class CourseDetailSerializer(serializers.ModelSerializer):
    outline = serializers.SerializerMethodField()
    hierarchy = serializers.SerializerMethodField()
    materials = LearningMaterialSerializer(many=True, read_only=True)

    class Meta:
        model = CourseGroup
        fields = [
            "id",
            "title",
            "description",
            "outline",
            "hierarchy",
            "materials",
            "created_at",
            "updated_at",
        ]

    def get_outline(self, obj):
        if hasattr(obj, "outline"):
            return {
                "id": obj.outline.id,
                "filename": obj.outline.outline_file.name.split("/")[-1],
                "is_approved": obj.outline.is_approved,
                "uploaded_at": obj.outline.uploaded_at,
                "approved_at": obj.outline.approved_at,
            }
        return None

    def get_hierarchy(self, obj):
        roots = obj.nodes.filter(parent__isnull=True)
        return OutlineHierarchyNodeSerializer(roots, many=True, context=self.context).data


class CourseCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = CourseGroup
        fields = ["id", "title", "description"]

