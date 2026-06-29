from rest_framework import serializers

from .models import AudioModule, CourseGroup, CourseOutline, Lesson, LessonPage, OutlineNode, PageImage


class PageImageSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = PageImage
        fields = ["id", "order", "caption", "interpretation", "image_url"]

    def get_image_url(self, obj):
        request = self.context.get("request")
        if obj.image and request:
            return request.build_absolute_uri(obj.image.url)
        if obj.image:
            return obj.image.url
        return None


class LessonPageSerializer(serializers.ModelSerializer):
    images = PageImageSerializer(many=True, read_only=True)

    class Meta:
        model = LessonPage
        fields = ["id", "page_number", "raw_text", "image_count", "images"]


class AudioModuleSerializer(serializers.ModelSerializer):
    audio_url = serializers.SerializerMethodField()

    class Meta:
        model = AudioModule
        fields = [
            "id",
            "order",
            "title",
            "narrative_text",
            "audio_url",
            "duration_seconds",
        ]

    def get_audio_url(self, obj):
        request = self.context.get("request")
        if obj.audio_file and request:
            return request.build_absolute_uri(obj.audio_file.url)
        if obj.audio_file:
            return obj.audio_file.url
        return None


class AudioModuleUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = AudioModule
        fields = ["id", "title", "narrative_text"]


class OutlineNodeSerializer(serializers.ModelSerializer):
    children = serializers.SerializerMethodField()
    lesson_id = serializers.SerializerMethodField()
    lesson_status = serializers.SerializerMethodField()

    class Meta:
        model = OutlineNode
        fields = [
            "id",
            "title",
            "order",
            "depth",
            "status",
            "parent",
            "lesson_id",
            "lesson_status",
            "children",
        ]

    def get_children(self, obj):
        children = obj.children.all()
        return OutlineNodeSerializer(children, many=True, context=self.context).data

    def get_lesson_id(self, obj):
        if hasattr(obj, "lesson") and obj.lesson:
            return obj.lesson.id
        return None

    def get_lesson_status(self, obj):
        if hasattr(obj, "lesson") and obj.lesson:
            return obj.lesson.status
        return None


class CourseListSerializer(serializers.ModelSerializer):
    node_count = serializers.SerializerMethodField()
    published_count = serializers.SerializerMethodField()
    has_outline = serializers.SerializerMethodField()

    class Meta:
        model = CourseGroup
        fields = [
            "id",
            "title",
            "description",
            "node_count",
            "published_count",
            "has_outline",
            "created_at",
        ]

    def get_node_count(self, obj):
        return obj.nodes.count()

    def get_published_count(self, obj):
        return obj.nodes.filter(status=OutlineNode.NodeStatus.PUBLISHED).count()

    def get_has_outline(self, obj):
        return hasattr(obj, "outline")


class CourseDetailSerializer(serializers.ModelSerializer):
    outline = serializers.SerializerMethodField()
    dag = serializers.SerializerMethodField()

    class Meta:
        model = CourseGroup
        fields = [
            "id",
            "title",
            "description",
            "outline",
            "dag",
            "created_at",
            "updated_at",
        ]

    def get_outline(self, obj):
        if hasattr(obj, "outline"):
            return {
                "id": obj.outline.id,
                "filename": obj.outline.outline_file.name.split("/")[-1],
                "uploaded_at": obj.outline.uploaded_at,
            }
        return None

    def get_dag(self, obj):
        roots = obj.nodes.filter(parent__isnull=True)
        return OutlineNodeSerializer(roots, many=True, context=self.context).data


class CourseCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = CourseGroup
        fields = ["id", "title", "description"]


class LessonListSerializer(serializers.ModelSerializer):
    module_count = serializers.SerializerMethodField()
    outline_node_id = serializers.IntegerField(source="outline_node.id", read_only=True, allow_null=True)
    course_id = serializers.IntegerField(source="course.id", read_only=True, allow_null=True)

    class Meta:
        model = Lesson
        fields = [
            "id",
            "title",
            "status",
            "progress",
            "module_count",
            "error_message",
            "outline_node_id",
            "course_id",
            "published_at",
            "created_at",
        ]

    def get_module_count(self, obj):
        return obj.audio_modules.count()


class LessonDetailSerializer(serializers.ModelSerializer):
    pages = LessonPageSerializer(many=True, read_only=True)
    audio_modules = AudioModuleSerializer(many=True, read_only=True)
    outline_node_id = serializers.IntegerField(source="outline_node.id", read_only=True, allow_null=True)
    course_id = serializers.IntegerField(source="course.id", read_only=True, allow_null=True)

    class Meta:
        model = Lesson
        fields = [
            "id",
            "title",
            "status",
            "progress",
            "error_message",
            "story_intro",
            "outline_node_id",
            "course_id",
            "script_approved_at",
            "published_at",
            "created_at",
            "updated_at",
            "pages",
            "audio_modules",
        ]


class LessonUploadSerializer(serializers.ModelSerializer):
    title = serializers.CharField(max_length=255, required=False, allow_blank=True)

    class Meta:
        model = Lesson
        fields = ["id", "title", "pdf_file", "status", "progress"]
        read_only_fields = ["id", "status", "progress"]

    def validate(self, attrs):
        node = self.context.get("outline_node")
        if node and hasattr(node, "lesson"):
            raise serializers.ValidationError("This outline node already has a lesson.")
        return attrs

    def create(self, validated_data):
        node = self.context.get("outline_node")
        course = self.context.get("course")
        lesson = Lesson.objects.create(
            **validated_data,
            course=course,
            outline_node=node,
            title=validated_data.get("title") or (node.title if node else "Untitled Lesson"),
        )
        if node:
            node.status = OutlineNode.NodeStatus.IN_PROGRESS
            node.save(update_fields=["status"])
        return lesson


class ScriptUpdateSerializer(serializers.Serializer):
    modules = AudioModuleUpdateSerializer(many=True)

    def update_lesson(self, lesson):
        modules_data = self.validated_data["modules"]
        module_map = {item["id"]: item for item in modules_data}
        modules = AudioModule.objects.filter(lesson=lesson, id__in=module_map.keys())
        for module in modules:
            data = module_map[module.id]
            module.title = data.get("title", module.title)
            module.narrative_text = data.get("narrative_text", module.narrative_text)
            module.save()
        return lesson
