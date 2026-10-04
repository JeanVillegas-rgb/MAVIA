from rest_framework import serializers


class CourseOutlineUploadInputSerializer(serializers.Serializer):
    outline_file = serializers.FileField(required=False)

    def validate(self, attrs):
        outline_file = attrs.get("outline_file")
        if not outline_file:
            raise serializers.ValidationError({"detail": "outline_file is required."})
        if not outline_file.name.lower().endswith(".pdf"):
            raise serializers.ValidationError({"detail": "Only PDF course outlines are supported."})
        return attrs


class CoursePdfUploadInputSerializer(serializers.Serializer):
    pdf_file = serializers.FileField(required=False)
    title = serializers.CharField(max_length=255, required=False, allow_blank=True)

    def validate(self, attrs):
        pdf_file = attrs.get("pdf_file")
        if not pdf_file:
            raise serializers.ValidationError({"detail": "pdf_file is required."})
        if not pdf_file.name.lower().endswith(".pdf"):
            raise serializers.ValidationError({"detail": "Only PDF files are supported."})
        return attrs


class LearningMaterialUploadInputSerializer(serializers.Serializer):
    pdf_file = serializers.FileField(required=False)
    title = serializers.CharField(max_length=255, required=False, allow_blank=True)
    outline_node_id = serializers.IntegerField(required=False, min_value=1)
    module_node_id = serializers.IntegerField(required=False, min_value=1)

    def validate(self, attrs):
        pdf_file = attrs.get("pdf_file")
        if not pdf_file:
            raise serializers.ValidationError({"detail": "pdf_file is required."})
        if not pdf_file.name.lower().endswith(".pdf"):
            raise serializers.ValidationError({"detail": "Only PDF learning materials are supported."})
        return attrs


class EmptyInputSerializer(serializers.Serializer):
    """Documents an endpoint that intentionally accepts no input fields."""
