from rest_framework import serializers

from .models import (
    ConceptPrerequisiteEdge,
    ConceptSource,
    CourseGroup,
    ExtractedConcept,
    LearningMaterial,
    LearningObject,
    LearningObjectPrerequisiteEdge,
    ModuleConceptDAGState,
    OutlineEdge,
    OutlineNode,
)


class OutlineNodeSerializer(serializers.ModelSerializer):
    class Meta:
        model = OutlineNode
        fields = ["id", "title", "depth", "order", "parent"]


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


class OutlineEdgeSerializer(serializers.ModelSerializer):
    class Meta:
        model = OutlineEdge
        fields = [
            "id",
            "source",
            "target",
            "score",
            "semantic_similarity",
            "outline_order_score",
            "validation_status",
            "is_manual",
            "explanation",
        ]


class OutlineHierarchyNodeSerializer(serializers.ModelSerializer):
    children = serializers.SerializerMethodField()

    class Meta:
        model = OutlineNode
        fields = ["id", "title", "order", "depth", "parent", "children"]

    def get_children(self, obj):
        children = obj.children.all()
        return OutlineHierarchyNodeSerializer(children, many=True, context=self.context).data


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
        fields = ["id", "title", "content", "order"]


class LearningObjectMutationSerializer(serializers.ModelSerializer):
    class Meta:
        model = LearningObject
        fields = ["title", "content", "order"]
        extra_kwargs = {
            "title": {"required": False},
            "content": {"required": False},
            "order": {"required": False},
        }

    def validate_title(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Title cannot be blank.")
        return value


class LearningObjectPrerequisiteEdgeSerializer(serializers.ModelSerializer):
    class Meta:
        model = LearningObjectPrerequisiteEdge
        fields = [
            "id",
            "course",
            "module_node",
            "source",
            "target",
            "score",
            "semantic_similarity",
            "dependency_cue_score",
            "source_order_score",
            "title_overlap_score",
            "validation_status",
            "is_manual",
            "explanation",
            "created_at",
            "updated_at",
        ]

    def validate_content(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Content cannot be blank.")
        return value


class LearningMaterialSerializer(serializers.ModelSerializer):
    learning_objects = LearningObjectSerializer(many=True, read_only=True)
    filename = serializers.SerializerMethodField()

    class Meta:
        model = LearningMaterial
        fields = [
            "id",
            "title",
            "filename",
            "outline_node",
            "module_node",
            "status",
            "error_message",
            "generated_json",
            "created_at",
            "learning_objects",
        ]

    def get_filename(self, obj):
        return obj.pdf_file.name.split("/")[-1] if obj.pdf_file else ""


class ConceptSourceSerializer(serializers.ModelSerializer):
    material_title = serializers.CharField(source="learning_material.title", read_only=True)
    material_filename = serializers.SerializerMethodField()

    class Meta:
        model = ConceptSource
        fields = [
            "id",
            "learning_material",
            "material_title",
            "material_filename",
            "page_number",
            "section_title",
            "source_excerpt",
            "first_appearance_order",
        ]

    def get_material_filename(self, obj):
        return obj.learning_material.pdf_file.name.split("/")[-1] if obj.learning_material.pdf_file else ""


class ExtractedConceptSerializer(serializers.ModelSerializer):
    sources = ConceptSourceSerializer(many=True, read_only=True)

    class Meta:
        model = ExtractedConcept
        fields = [
            "id",
            "course",
            "module_node",
            "canonical_title",
            "normalized_title",
            "description",
            "order",
            "confidence",
            "validation_status",
            "is_manual",
            "created_at",
            "updated_at",
            "sources",
        ]


class ExtractedConceptWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExtractedConcept
        fields = ["canonical_title", "description", "order"]
        extra_kwargs = {
            "canonical_title": {"required": False},
            "description": {"required": False},
            "order": {"required": False},
        }

    def validate_canonical_title(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Concept title cannot be blank.")
        return value


class ConceptPrerequisiteEdgeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConceptPrerequisiteEdge
        fields = [
            "id",
            "course",
            "module_node",
            "source",
            "target",
            "score",
            "semantic_similarity",
            "dependency_cue_score",
            "source_order_score",
            "title_overlap_score",
            "instructional_order_score",
            "validation_status",
            "is_manual",
            "explanation",
            "created_at",
            "updated_at",
        ]


class ConceptPrerequisiteEdgeWriteSerializer(serializers.Serializer):
    source = serializers.PrimaryKeyRelatedField(queryset=ExtractedConcept.objects.all(), required=False)
    target = serializers.PrimaryKeyRelatedField(queryset=ExtractedConcept.objects.all(), required=False)
    validation_status = serializers.ChoiceField(
        choices=ConceptPrerequisiteEdge.ValidationStatus.choices,
        required=False,
    )
    explanation = serializers.CharField(required=False, allow_blank=True)


class ModuleConceptDAGStateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ModuleConceptDAGState
        fields = [
            "is_confirmed",
            "confirmed_at",
            "invalidated_at",
            "invalidation_reason",
            "updated_at",
        ]


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
