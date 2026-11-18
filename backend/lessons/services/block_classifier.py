"""Classify extracted PDF blocks using the persisted contextual classifier."""

import re
from functools import lru_cache
from pathlib import Path

import joblib
import numpy as np
from django.conf import settings
from sentence_transformers import SentenceTransformer


VALID_MODEL_LABELS = {"lesson_content", "assessment", "other"}
ARTIFACT_PATH = (
    Path(__file__).resolve().parents[1]
    / "training"
    / "artifacts"
    / "block_classifier.joblib"
)


def is_structural_metadata_label(text):
    """Legacy display filter for learning objects already stored in the database.

    This is not used to classify newly extracted PDF blocks. Existing records
    lack a structural label, so their API views still need the old title check
    until those records have been migrated.
    """
    label = re.sub(r"[^a-z0-9]+", " ", (text or "").casefold()).strip()
    return bool(label and re.fullmatch(
        r"(?:module|unit|chapter|lesson|week|quarter|section|part)\s+"
        r"(?:\d+|[ivxlcdm]+)(?:\s+.+)?",
        label,
    ))


class LessonClassificationPending(RuntimeError):
    """The trained block-classification artifact is not available."""


@lru_cache(maxsize=1)
def _load_classifier():
    if not ARTIFACT_PATH.is_file():
        raise LessonClassificationPending(
            f"Classifier artifact not found: {ARTIFACT_PATH}. "
            "Run lessons.training.train_classifier first."
        )

    artifact = joblib.load(ARTIFACT_PATH)
    labels = set(artifact.get("labels", ()))
    if labels != VALID_MODEL_LABELS:
        raise LessonClassificationPending(
            "Classifier artifact labels do not match the runtime categories."
        )

    encoder = SentenceTransformer(artifact["encoder_name"], device="cpu")
    return artifact, encoder


def _contexts(blocks):
    active_section = ""
    contexts = []

    for index, block in enumerate(blocks):
        current_label = block.get("docling_label", "")
        if current_label in {"title", "section_header", "heading"}:
            active_section = block.get("text", "")

        previous = blocks[index - 1] if index else {}
        following = blocks[index + 1] if index + 1 < len(blocks) else {}
        contexts.append(
            f"Active section:\n{active_section}\n\n"
            f"Current Docling label:\n{current_label}\n\n"
            f"Previous Docling label:\n{previous.get('docling_label', '')}\n"
            f"Previous block:\n{previous.get('text', '')}\n\n"
            f"Next Docling label:\n{following.get('docling_label', '')}\n"
            f"Next block:\n{following.get('text', '')}"
        )

    return contexts


def classify_blocks(blocks):
    if not blocks:
        return []
    artifact, encoder = _load_classifier()
    feature_mode = artifact.get("feature_mode", "Target + context")
    texts = [block.get("text", "") for block in blocks]
    contexts = _contexts(blocks)
    options = {
        "batch_size": 16,
        "convert_to_numpy": True,
        "normalize_embeddings": True,
        "show_progress_bar": False,
    }
    target_vectors = encoder.encode(texts, **options)
    if feature_mode == "Target + context":
        context_vectors = encoder.encode(contexts, **options)
        features = np.concatenate([target_vectors, context_vectors], axis=1)
    else:
        features = target_vectors

    model = artifact["model"]
    predictions = model.predict(features)
    scores = np.asarray(model.decision_function(features), dtype=float)
    classes = [str(label) for label in model.classes_]
    threshold = getattr(settings, "BLOCK_CLASSIFIER_REVIEW_MARGIN", None)
    if threshold is not None:
        threshold = float(threshold)
        if not np.isfinite(threshold) or threshold < 0:
            raise ValueError("BLOCK_CLASSIFIER_REVIEW_MARGIN must be finite and nonnegative.")
    if (
        set(classes) != VALID_MODEL_LABELS
        or len(classes) != len(VALID_MODEL_LABELS)
        or len(predictions) != len(blocks)
        or any(str(label) not in VALID_MODEL_LABELS for label in predictions)
        or scores.shape != (len(blocks), len(classes))
        or not np.isfinite(scores).all()
    ):
        raise LessonClassificationPending("Classifier returned invalid decision scores.")
    classified = []
    for block, prediction, row_scores in zip(blocks, predictions, scores):
        category = str(prediction)
        ranked = np.sort(row_scores)
        margin = float(ranked[-1] - ranked[-2])
        classified_block = dict(block)
        classified_block["category"] = category
        classified_block["include_in_narration"] = category == "lesson_content"
        # LinearSVC margins are NOT calibrated confidence probabilities.
        # Keep the prediction intact; teacher overrides are stored separately.
        classified_block.update(
            predicted_category=category,
            decision_scores=dict(zip(classes, map(float, row_scores))),
            decision_margin=margin,
            review_margin_threshold=threshold,
            review_required=threshold is not None and margin < threshold,
        )
        classified.append(classified_block)

    return classified


def detect_instructional_document_role(classified_blocks):
    """Keep previously classified question payloads readable during migration."""
    lesson_count = sum(
        block.get("category") == "lesson_content" for block in classified_blocks
    )
    assessment_count = sum(
        block.get("category") == "assessment" for block in classified_blocks
    )
    return (
        "assessment"
        if assessment_count >= 5 and assessment_count > lesson_count
        else "lesson"
    )
