"""Non-destructive teacher overrides of stored block classifications.

No PDF/model rerun, wholesale object replacement, or automatic deletion of
teacher work. Finalized/confirmed sources are not editable through this API.
"""

from copy import deepcopy

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import APIException, ValidationError

from lessons.models import LearningMaterial, LearningObject, OutlineNode
from .embedding_service import embed_learning_objects
from .learning_object_builder import build_learning_objects, HEADING_LABELS
from .source_review import source_review_is_finalized


class BlockReviewConflict(APIException):
    status_code = 409
    default_detail = "Source-linked work must be reviewed before changing this category."


def pending_block_reviews(material):
    blocks = (material.generated_json or {}).get("classified_blocks") or material.extracted_blocks or []
    return [block for block in blocks if block.get("review_required") and not block.get("teacher_reviewed")]


@transaction.atomic
def review_block(material, *, block_id, category, user):
    # Lock the topic before its material, including the finalization check.
    if material.outline_node_id:
        node = OutlineNode.objects.select_for_update().get(pk=material.outline_node_id)
        if source_review_is_finalized(node):
            raise BlockReviewConflict("Lesson sources are finalized and cannot be changed.")
    material = LearningMaterial.objects.select_for_update().get(pk=material.pk)
    saved = deepcopy(material.generated_json or {})
    if saved.get("learning_objects_confirmed"):
        raise BlockReviewConflict("Learning objects are confirmed; classification review is locked.")
    if material.status != LearningMaterial.Status.COMPLETED:
        raise BlockReviewConflict("Wait for PDF processing to finish before reviewing blocks.")
    blocks = deepcopy(saved.get("classified_blocks") or material.extracted_blocks or [])
    matches = [index for index, block in enumerate(blocks) if block.get("block_id") == block_id]
    if len(matches) != 1:
        raise ValidationError({"block_id": "A unique source block was not found in this material."})
    index = matches[0]
    block = blocks[index]
    old_category = block.get("category", "other")
    changed = old_category != category
    objects = list(material.learning_objects.order_by("order", "id"))
    linked_ids = {obj.id for obj in objects if obj.source_block_id == block_id}
    # A visual can have consumed several source blocks; never replace it.
    for draft in [*(material.draft_learning_objects or []), *saved.get("learning_objects", [])]:
        if block_id in draft.get("source_block_ids", []):
            linked_ids.update(
                obj.id for obj in objects
                if obj.draft_id == draft.get("draft_id") or obj.id == draft.get("learning_object_id")
            )
    if changed and (linked_ids or material.questions.filter(source_block_id=block_id).exists()):
        raise BlockReviewConflict(
            "This block already has source-linked learning objects or questions. "
            "Review/remove those records using their existing controls first; "
            "classification changes never delete or overwrite them automatically."
        )
    block.setdefault("predicted_category", old_category)
    block.update(
        category=category, include_in_narration=category == "lesson_content",
        teacher_category=category, teacher_reviewed=True,
        reviewed_by=user.pk, reviewed_at=timezone.now().isoformat(),
    )
    saved.setdefault("block_review_history", []).append({
        "block_id": block_id, "from_category": old_category, "to_category": category,
        "reviewed_by": user.pk, "reviewed_at": block["reviewed_at"],
    })
    if changed and category == "lesson_content" and block.get("docling_label") not in HEADING_LABELS:
        # Only create the selected object's draft. Existing edits stay intact.
        heading = next((item for item in reversed(blocks[:index])
                        if item.get("docling_label") in HEADING_LABELS
                        and item.get("category") == "lesson_content"), None)
        drafts = build_learning_objects(([heading] if heading else []) + [block])
        for draft in drafts:
            draft["draft_id"] = f"review_block_{block_id}"
        embeddings = embed_learning_objects(drafts)
        for draft in drafts:
            obj = LearningObject.objects.create(
                material=material, draft_id=draft["draft_id"],
                title=draft["title"][:255], section_title=draft["section_title"][:255],
                content=draft["content"], source_excerpt=draft["source_text"],
                source_page=block.get("page"), source_block_id=block_id,
                embedding=embeddings.get(draft["draft_id"], []), order=len(objects),
            )
            positions = {item.get("block_id"): pos for pos, item in enumerate(blocks)}
            insertion = next((pos for pos, item in enumerate(objects)
                              if positions.get(item.source_block_id, -1) > index
                              or (item.source_page or 0) > (block.get("page") or 0)), len(objects))
            objects.insert(insertion, obj)
            for order, item in enumerate(objects):
                item.order = order
            LearningObject.objects.bulk_update(objects, ["order"])
            material.draft_learning_objects = [*(material.draft_learning_objects or []), draft]
            material.object_embeddings = {**(material.object_embeddings or {}), **embeddings}
        saved["document_role"] = "lesson"
    if changed and category == "assessment":
        from .question_workflow import add_reviewed_block_questions
        add_reviewed_block_questions(material, blocks, block_id)
    saved["classified_blocks"] = blocks
    saved["assessments"] = [item for item in blocks if item.get("category") == "assessment"]
    saved["ignored_blocks"] = [item for item in blocks if item.get("category") == "other"]
    material.extracted_blocks = blocks
    material.generated_json = saved
    material.save(update_fields=["extracted_blocks", "generated_json", "draft_learning_objects", "object_embeddings"])
    return material
