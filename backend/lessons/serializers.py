from rest_framework import serializers

from .models import (
    CourseGroup,
    LearningMaterial,
    LearningObject,
    LearningObjectMatchSuggestion,
    OutlineNode,
    Question,
    QuestionLearningObjectLink,
)
from .services.audio_generator import remove_missing_audio_urls
from .services.content_generator import is_structural_metadata_label


class OutlineNodeSerializer(serializers.ModelSerializer):
    class Meta:
        model = OutlineNode
        fields = ["id", "title", "depth", "order", "parent", "related_info", "published", "published_at"]


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
            "published",
            "published_at",
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
        return obj.outlines.exists()

    def get_outline_approved(self, obj):
        outlines = obj.outlines.all()
        return outlines.exists() and not outlines.filter(is_approved=False).exists()


class LearningObjectSerializer(serializers.ModelSerializer):
    outline_node_id = serializers.IntegerField(
        source="material.outline_node_id",
        read_only=True,
        allow_null=True,
    )

    class Meta:
        model = LearningObject
        fields = [
            "id",
            "metadata_id",
            "material",
            "outline_node_id",
            "group",
            "kind",
            "section_title",
            "title",
            "content",
            "image_url",
            "order",
            "source_page",
            "source_block_id",
            "source_excerpt",
        ]


class LearningObjectMatchSuggestionSerializer(serializers.ModelSerializer):
    source_learning_object = LearningObjectSerializer(read_only=True)
    candidate_learning_object = LearningObjectSerializer(read_only=True)

    class Meta:
        model = LearningObjectMatchSuggestion
        fields = [
            "id",
            "outline_node",
            "source_learning_object",
            "candidate_learning_object",
            "similarity_score",
            "confidence",
            "evidence",
            "status",
            "created_at",
            "updated_at",
        ]


class QuestionLearningObjectLinkSerializer(serializers.ModelSerializer):
    learning_object_group_id = serializers.IntegerField(
        source="learning_object.group_id",
        read_only=True,
        allow_null=True,
    )
    learning_object_title = serializers.CharField(
        source="learning_object.title",
        read_only=True,
    )

    class Meta:
        model = QuestionLearningObjectLink
        fields = [
            "learning_object",
            "learning_object_group_id",
            "learning_object_title",
            "relevance_score",
            "method",
            "is_primary",
            "review_status",
            "reviewed_at",
        ]


class QuestionSerializer(serializers.ModelSerializer):
    learning_object_links = QuestionLearningObjectLinkSerializer(many=True, read_only=True)
    outline_node_id = serializers.IntegerField(
        source="material.outline_node_id",
        read_only=True,
        allow_null=True,
    )
    source_filename = serializers.SerializerMethodField()
    pairing_status = serializers.SerializerMethodField()

    class Meta:
        model = Question
        fields = [
            "id",
            "material",
            "outline_node_id",
            "prompt",
            "source_type",
            "source_filename",
            "content_fingerprint",
            "question_type",
            "choices",
            "correct_answer",
            "bloom_level",
            "thinking_order",
            "difficulty",
            "category",
            "validation_status",
            "validation_issues",
            "pairing_status",
            "adaptive_question",
            "order",
            "source_page",
            "source_block_id",
            "source_excerpt",
            "learning_object_links",
        ]

    def get_source_filename(self, obj):
        if obj.source_type == Question.SourceType.MANUAL:
            return ""
        return obj.material.pdf_file.name.split("/")[-1] if obj.material.pdf_file else ""

    def get_pairing_status(self, obj):
        link = next(iter(obj.learning_object_links.all()), None)
        return link.review_status if link else "unmatched"


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
    questions = QuestionSerializer(many=True, read_only=True)

    class Meta:
        model = LearningMaterial
        fields = [
            "id",
            "metadata_id",
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
            "questions",
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
        outlines = list(obj.outlines.all())
        if not outlines:
            return None

        latest = outlines[-1]
        all_approved = all(outline.is_approved for outline in outlines)
        return {
            "id": latest.id,
            "metadata_id": str(latest.metadata_id),
            "filename": latest.outline_file.name.split("/")[-1],
            "is_approved": all_approved,
            "uploaded_at": latest.uploaded_at,
            "approved_at": latest.approved_at if all_approved else None,
            "source_count": len(outlines),
            "files": [
                {
                    "id": outline.id,
                    "metadata_id": str(outline.metadata_id),
                    "filename": outline.outline_file.name.split("/")[-1],
                    "is_approved": outline.is_approved,
                    "uploaded_at": outline.uploaded_at,
                }
                for outline in outlines
            ],
        }

    def get_hierarchy(self, obj):
        roots = obj.nodes.filter(parent__isnull=True)
        return OutlineHierarchyNodeSerializer(roots, many=True, context=self.context).data


class CourseCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = CourseGroup
        fields = ["id", "title", "description"]

