import hashlib
import tempfile
from contextlib import contextmanager
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from lessons.models import CourseGroup, CourseOutline, LearningMaterial, OutlineNode
from lessons.services.content_generator import generate_material_outputs
from lessons.services.outline_parser import (
    build_dag_from_outline,
    is_course_outline_pdf,
    validate_course_outline_pdf,
)


class PdfProcessingUseCaseError(Exception):
    """A presentation-independent business error raised by a PDF use case."""


@contextmanager
def _temporary_pdf_copy(uploaded_file):
    original_position = uploaded_file.tell() if hasattr(uploaded_file, "tell") else None
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temporary_file:
            temporary_path = Path(temporary_file.name)
            if hasattr(uploaded_file, "seek"):
                uploaded_file.seek(0)
            chunks = (
                uploaded_file.chunks()
                if hasattr(uploaded_file, "chunks")
                else iter(lambda: uploaded_file.read(1024 * 1024), b"")
            )
            for chunk in chunks:
                temporary_file.write(chunk)
        yield temporary_path
    finally:
        if hasattr(uploaded_file, "seek"):
            uploaded_file.seek(original_position or 0)
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


@transaction.atomic
def upload_course_outline(*, course: CourseGroup, outline_file) -> CourseGroup:
    """Store an outline source and merge its hierarchy into the course."""
    try:
        with _temporary_pdf_copy(outline_file) as temporary_path:
            validate_course_outline_pdf(str(temporary_path))
    except Exception as exc:
        raise PdfProcessingUseCaseError(str(exc)) from exc

    outline = CourseOutline.objects.create(course=course, outline_file=outline_file)
    try:
        build_dag_from_outline(
            course,
            outline.outline_file.path,
            Path(outline_file.name).suffix,
            replace=False,
        )
    except Exception as exc:
        outline.outline_file.delete(save=False)
        outline.delete()
        raise PdfProcessingUseCaseError(str(exc)) from exc
    return course


def confirm_course_outline(*, course: CourseGroup) -> CourseGroup:
    """Approve an extracted hierarchy after enforcing its business invariants."""
    outlines = course.outlines.all()
    if not outlines.exists():
        raise PdfProcessingUseCaseError("Upload an outline before confirming the hierarchy.")

    if not course.nodes.exists():
        raise PdfProcessingUseCaseError("The outline has no extracted topics to confirm.")

    outlines.filter(is_approved=False).update(is_approved=True, approved_at=timezone.now())
    return course


def upload_course_pdf(
    *,
    course: CourseGroup,
    pdf_file,
    title: str = "",
) -> tuple[str, LearningMaterial | None, bool]:
    """Classify one PDF and route it to the outline or lesson-material workflow."""
    try:
        with _temporary_pdf_copy(pdf_file) as temporary_path:
            is_outline = is_course_outline_pdf(str(temporary_path))
    except Exception as exc:
        raise PdfProcessingUseCaseError(f"The uploaded PDF could not be read: {exc}") from exc

    if is_outline:
        upload_course_outline(course=course, outline_file=pdf_file)
        return "outline", None, False

    material, reused = upload_learning_material(
        course=course,
        pdf_file=pdf_file,
        title=title,
    )
    return "lesson_material", material, reused


def course_outline_is_approved(course: CourseGroup) -> bool:
    """Return true only when at least one outline exists and all are approved."""
    outlines = course.outlines.all()
    return outlines.exists() and not outlines.filter(is_approved=False).exists()


def _top_level_module(node: OutlineNode) -> OutlineNode:
    while node.parent_id is not None:
        node = node.parent
    return node


def _file_sha256(file_obj) -> str:
    digest = hashlib.sha256()
    original_position = file_obj.tell() if hasattr(file_obj, "tell") else None
    if hasattr(file_obj, "seek"):
        file_obj.seek(0)
    chunks = file_obj.chunks() if hasattr(file_obj, "chunks") else iter(lambda: file_obj.read(1024 * 1024), b"")
    for chunk in chunks:
        digest.update(chunk)
    if hasattr(file_obj, "seek"):
        file_obj.seek(original_position or 0)
    return digest.hexdigest()


def _find_existing_material(course: CourseGroup, pdf_file, fingerprint: str) -> LearningMaterial | None:
    existing = course.materials.filter(file_sha256=fingerprint).first()
    if existing:
        return existing

    # Populate fingerprints lazily for files uploaded before this field existed.
    for material in course.materials.filter(file_sha256="").exclude(pdf_file=""):
        try:
            material.pdf_file.open("rb")
            existing_fingerprint = _file_sha256(material.pdf_file)
        except (OSError, ValueError):
            continue
        finally:
            try:
                material.pdf_file.close()
            except Exception:
                pass
        fingerprint_owner = course.materials.filter(
            file_sha256=existing_fingerprint
        ).first()
        if fingerprint_owner is None:
            material.file_sha256 = existing_fingerprint
            material.save(update_fields=["file_sha256"])
        else:
            material = fingerprint_owner
        if existing_fingerprint == fingerprint:
            return material
    return None


def upload_learning_material(
    *,
    course: CourseGroup,
    pdf_file,
    title: str = "",
    outline_node_id: int | None = None,
    module_node_id: int | None = None,
) -> tuple[LearningMaterial, bool]:
    """Create a lesson material, resolve its placement, and generate its outputs."""
    outline_node = None
    if outline_node_id:
        try:
            outline_node = course.nodes.get(pk=outline_node_id)
        except OutlineNode.DoesNotExist as exc:
            raise PdfProcessingUseCaseError(
                "Selected outline node was not found for this course."
            ) from exc

    module_node = None
    if module_node_id:
        try:
            module_node = course.nodes.get(pk=module_node_id, parent__isnull=True)
        except OutlineNode.DoesNotExist as exc:
            raise PdfProcessingUseCaseError(
                "Selected module was not found for this course."
            ) from exc
    elif outline_node is not None:
        module_node = _top_level_module(outline_node)
    elif not course_outline_is_approved(course):
        raise PdfProcessingUseCaseError(
            "Select a module or topic before uploading lesson material, or confirm "
            "the course outline first for automatic classification."
        )

    if outline_node is not None and module_node is not None:
        expected_module = _top_level_module(outline_node)
        if expected_module.id != module_node.id:
            raise PdfProcessingUseCaseError(
                "Selected topic does not belong to the selected module."
            )

    fingerprint = _file_sha256(pdf_file)
    existing_material = _find_existing_material(course, pdf_file, fingerprint)
    if existing_material is not None:
        return existing_material, True

    material = LearningMaterial.objects.create(
        course=course,
        outline_node=outline_node,
        module_node=module_node,
        title=title.strip() or Path(pdf_file.name).stem,
        pdf_file=pdf_file,
        file_sha256=fingerprint,
    )
    generate_material_outputs(material)
    return material, False


def regenerate_learning_material(*, material: LearningMaterial) -> LearningMaterial:
    """Reset processing state and rebuild all derived PDF outputs."""
    if not material.pdf_file:
        raise PdfProcessingUseCaseError("This material has no PDF file to regenerate.")

    material.status = LearningMaterial.Status.PROCESSING
    material.error_message = ""
    material.save(update_fields=["status", "error_message"])
    return generate_material_outputs(material)
