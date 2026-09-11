"""Content-only semantic grouping. Scores are similarities, not probabilities.

Models are pinned and CPU-backed. After their public weights are downloaded,
all learning-object inference is local.
The STS cross-encoder is a baseline to evaluate, not proof of interchangeability.
"""
from collections import defaultdict
from contextlib import closing
from functools import lru_cache
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import sqlite3
from threading import RLock
from time import perf_counter

from django.conf import settings

logger = logging.getLogger(__name__)
ENCODER = "sentence-transformers/all-MiniLM-L6-v2"
ENCODER_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
RERANKER = "cross-encoder/stsb-roberta-base"
RERANKER_REVISION = "d576534b67143e2c70ee9966d7fdbf5835728d13"
FINGERPRINT = f"content-sts-v1:{ENCODER}@{ENCODER_REVISION}:{RERANKER}@{RERANKER_REVISION}"

_GENERIC_INSTRUCTIONAL_LABELS = {
    "activity",
    "additional information",
    "conclusion",
    "definition",
    "diagram",
    "everyday examples",
    "example",
    "examples",
    "exercise",
    "glossary",
    "introduction",
    "key facts",
    "key facts to remember",
    "key points",
    "key points for students",
    "note",
    "notes",
    "objectives",
    "overview",
    "practice questions",
    "questions",
    "recap",
    "remember",
    "review",
    "summary",
    "vocabulary",
    "worksheet",
}
_GENERIC_NUMBERED_LABEL = re.compile(
    r"(?:figure|table|diagram|worksheet)(?:\s+\d+)?$",
    re.IGNORECASE,
)


class SemanticUnavailable(RuntimeError):
    pass


def normalized(text):
    # Preserve punctuation, negation, numbers and case for the language models.
    return " ".join((text or "").split())


def content_hash(text):
    return hashlib.sha256(normalized(text).encode("utf-8")).hexdigest()


def mode():
    value = os.getenv("SEMANTIC_GROUPING_MODE", "auto").lower()
    if value not in {"legacy", "review", "auto"}:
        raise SemanticUnavailable("SEMANTIC_GROUPING_MODE must be legacy, review, or auto.")
    return value


def policy():
    # These rollout defaults implement the teacher workflow requested for MAVIA:
    # high -> automatic, medium -> confirmation, low -> ignored. They are
    # configured operating thresholds, not calibrated probabilities.
    result = {
        "review_threshold": 0.3,
        "auto_threshold": 0.6,
        "minimum_sbert_cosine": 0.6,
        "minimum_margin": 0.05,
        "top_k": 10,
        "auto_threshold_source": "configured_default",
    }
    explicit_review = os.getenv("SEMANTIC_GROUPING_REVIEW_THRESHOLD", "").strip()
    if explicit_review:
        try:
            review = float(explicit_review)
            if not 0 <= review <= 1:
                raise ValueError("must be between 0 and 1")
            result["review_threshold"] = review
        except ValueError as exc:
            raise SemanticUnavailable(f"Invalid SEMANTIC_GROUPING_REVIEW_THRESHOLD: {exc}") from exc
    explicit_auto = os.getenv("SEMANTIC_GROUPING_AUTO_THRESHOLD", "").strip()
    if explicit_auto:
        try:
            high = float(explicit_auto)
            if not result["review_threshold"] <= high <= 1:
                raise ValueError("must be between the review threshold and 1")
            result.update(auto_threshold=high, auto_threshold_source="environment_unvalidated")
        except ValueError as exc:
            raise SemanticUnavailable(f"Invalid SEMANTIC_GROUPING_AUTO_THRESHOLD: {exc}") from exc
    explicit_sbert = os.getenv("SEMANTIC_GROUPING_MINIMUM_SBERT_COSINE", "").strip()
    if explicit_sbert:
        try:
            minimum_sbert = float(explicit_sbert)
            if not 0 <= minimum_sbert <= 1:
                raise ValueError("must be between 0 and 1")
            result["minimum_sbert_cosine"] = minimum_sbert
        except ValueError as exc:
            raise SemanticUnavailable(f"Invalid SEMANTIC_GROUPING_MINIMUM_SBERT_COSINE: {exc}") from exc
    explicit_margin = os.getenv("SEMANTIC_GROUPING_MINIMUM_MARGIN", "").strip()
    if explicit_margin:
        try:
            minimum_margin = float(explicit_margin)
            if not 0 <= minimum_margin <= 1:
                raise ValueError("must be between 0 and 1")
            result["minimum_margin"] = minimum_margin
        except ValueError as exc:
            raise SemanticUnavailable(f"Invalid SEMANTIC_GROUPING_MINIMUM_MARGIN: {exc}") from exc
    path = os.getenv("SEMANTIC_GROUPING_CALIBRATION")
    if path:
        try:
            document = json.loads(Path(path).read_text(encoding="utf-8"))
            if document.get("model_fingerprint") != FINGERPRINT:
                raise ValueError("calibration model/version mismatch")
            thresholds = document["thresholds"]
            review = float(thresholds["review_threshold"])
            high = thresholds.get("auto_threshold")
            margin = float(thresholds["minimum_margin"])
            if not (0 <= review <= 1 and 0 <= margin <= 1):
                raise ValueError("invalid thresholds")
            if high is not None and not review <= float(high) <= 1:
                raise ValueError("auto threshold must be above review threshold")
            result.update(review_threshold=review, minimum_margin=margin)
            # Pair-level validation is a prerequisite; group-level rollout still
            # requires review. An unvalidated artifact can never enable auto.
            validation = document.get("validation", {})
            if (document.get("approved_for_auto") is True
                    and validation.get("auto_predictions", 0) >= 40
                    and validation.get("false_auto", 1) == 0
                    and validation.get("negative_pairs", 0) >= 20
                    and document.get("dataset_sha256")):
                result["auto_threshold"] = float(high) if high is not None else None
                result["auto_threshold_source"] = "validated_calibration"
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise SemanticUnavailable(f"Invalid semantic calibration: {exc}") from exc
    return result


class ScoreCache:
    """Derived values only: content hashes and vectors/scores, never raw text."""
    def __init__(self, path):
        self.path = Path(path)
        self.memory = {}
        self.lock = RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=10)) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.commit()

    def get(self, key):
        return self.get_many([key]).get(key)

    def get_many(self, keys):
        keys = list(dict.fromkeys(keys))
        if not keys:
            return {}
        with self.lock:
            found = {key: self.memory[key] for key in keys if key in self.memory}
            missing = [key for key in keys if key not in found]
            if missing:
                placeholders = ",".join("?" for _ in missing)
                with closing(sqlite3.connect(self.path, timeout=10)) as connection:
                    rows = connection.execute(
                        f"SELECT key, value FROM cache WHERE key IN ({placeholders})",
                        missing,
                    ).fetchall()
                loaded = {key: json.loads(value) for key, value in rows}
                self.memory.update(loaded)
                found.update(loaded)
            return found

    def put(self, key, value):
        self.put_many({key: value})

    def put_many(self, values):
        if not values:
            return
        with self.lock:
            serialized = [
                (key, json.dumps(value, allow_nan=False))
                for key, value in values.items()
            ]
            with closing(sqlite3.connect(self.path, timeout=10)) as connection:
                connection.executemany("INSERT OR REPLACE INTO cache VALUES (?, ?)", serialized)
                connection.commit()
            self.memory.update(values)


class SemanticRuntime:
    def __init__(self, *, download=False):
        try:
            from sentence_transformers import CrossEncoder, SentenceTransformer
            import torch
            torch.set_num_threads(max(1, int(os.getenv("SEMANTIC_GROUPING_THREADS", "2"))))
            shared = {"device": "cpu", "local_files_only": not download,
                      "trust_remote_code": False, "token": False,
                      "model_kwargs": {"use_safetensors": True}}
            self.encoder = SentenceTransformer(ENCODER, revision=ENCODER_REVISION, **shared)
            self.reranker = CrossEncoder(RERANKER, revision=RERANKER_REVISION, max_length=512, **shared)
        except Exception as exc:
            raise SemanticUnavailable("Semantic models unavailable. Run prepare_semantic_grouping --download first.") from exc
        self.cache = ScoreCache(Path(settings.BASE_DIR) / "semantic_cache" / "scores.sqlite3")
        self.lock = RLock()

    def supports(self, text):
        text = normalized(text)
        return bool(text) and len(self.encoder.tokenizer.encode(text, add_special_tokens=True)) <= self.encoder.max_seq_length

    def embeddings(self, texts):
        with self.lock:
            texts = [normalized(text) for text in texts]
            if not all(self.supports(text) for text in texts):
                raise SemanticUnavailable("Empty or over-length content must be reviewed; no silent truncation.")
            keys = [f"{FINGERPRINT}:embedding:{content_hash(text)}" for text in texts]
            found = self.cache.get_many(keys)
            missing = list(dict.fromkeys(text for text, key in zip(texts, keys) if key not in found))
            if missing:
                vectors = self.encoder.encode(missing, batch_size=32, normalize_embeddings=True, show_progress_bar=False)
                updates = {}
                for text, vector in zip(missing, vectors):
                    key = f"{FINGERPRINT}:embedding:{content_hash(text)}"
                    updates[key] = vector.tolist()
                self.cache.put_many(updates)
                found.update(updates)
            return [found[key] for key in keys]

    def supports_pair(self, a, b):
        return self.supports(a) and self.supports(b) and all(
            len(self.reranker.tokenizer(left, right, truncation=False)["input_ids"]) <= 512
            for left, right in ((normalized(a), normalized(b)), (normalized(b), normalized(a)))
        )

    def pair_scores(self, pairs):
        """Conservative symmetry: lower of STS(A,B) and STS(B,A)."""
        with self.lock:
            pairs = [(normalized(a), normalized(b)) for a, b in pairs]
            result, missing = {}, {}
            for a, b in pairs:
                if not self.supports(a) or not self.supports(b):
                    raise SemanticUnavailable("Unsupported content length")
                for left, right in ((a, b), (b, a)):
                    tokens = self.reranker.tokenizer(left, right, truncation=False)["input_ids"]
                    if len(tokens) > 512:
                        raise SemanticUnavailable("Pair exceeds cross-encoder context; no silent truncation.")
                key = f"{FINGERPRINT}:pair:{':'.join(sorted((content_hash(a), content_hash(b))))}"
                result[(a, b)] = key
                if key not in missing:
                    missing[key] = [a, b, None]
            for key, score in self.cache.get_many(missing).items():
                missing[key][2] = score
            uncached = [(key, a, b) for key, (a, b, score) in missing.items() if score is None]
            if uncached:
                inputs = [pair for _, a, b in uncached for pair in ((a, b), (b, a))]
                scores = self.reranker.predict(inputs, batch_size=16, show_progress_bar=False).tolist()
                updates = {}
                for index, (key, a, b) in enumerate(uncached):
                    score = float(min(scores[2 * index:2 * index + 2]))
                    if not math.isfinite(score) or not 0 <= score <= 1:
                        raise SemanticUnavailable("Cross-encoder returned an invalid STS score")
                    missing[key][2] = score
                    updates[key] = score
                self.cache.put_many(updates)
            return [missing[result[pair]][2] for pair in pairs]


@lru_cache(maxsize=1)
def runtime():
    allow_download = os.getenv(
        "SEMANTIC_GROUPING_ALLOW_MODEL_DOWNLOAD", "True"
    ).lower() in {"1", "true", "yes"}
    try:
        # Cached installations must remain fully offline and must not perform a
        # Hugging Face metadata request merely to discover that weights exist.
        return SemanticRuntime(download=False)
    except SemanticUnavailable:
        if not allow_download:
            raise
        return SemanticRuntime(download=True)


def rank_groups(content, candidates, members_by_group, *, runtime_instance=None, thresholds=None):
    """Shortlist groups by content embeddings; score EVERY member of each.

No category/title heuristics and no best-member/transitive chaining shortcut.
"""
    if not candidates:
        return []
    engine = runtime_instance or runtime()
    config = thresholds or policy()
    if not engine.supports(content):
        raise SemanticUnavailable("This learning object is empty or exceeds the model's text limit. Review its connection manually; it has not been scored.")
    candidates = [item for item in candidates if engine.supports(item.content)]
    if not candidates:
        return []
    vectors = engine.embeddings([content] + [item.content for item in candidates])
    best = {}
    group_cosines = defaultdict(list)
    for candidate, vector in zip(candidates, vectors[1:]):
        cosine = sum(a * b for a, b in zip(vectors[0], vector))
        group_cosines[candidate.group_id].append(cosine)
        if candidate.group_id not in best or cosine > best[candidate.group_id][0]:
            best[candidate.group_id] = (cosine, candidate)
    shortlisted = sorted(best.values(), key=lambda row: (-row[0], row[1].id))[:config["top_k"]]
    pair_inputs, layouts = [], []
    for cosine, candidate in shortlisted:
        members = members_by_group[candidate.group_id]
        supported = [item for item in members if engine.supports_pair(content, item.content)]
        start = len(pair_inputs)
        pair_inputs.extend((content, item.content) for item in supported)
        layouts.append((cosine, candidate, supported, start, len(supported) == len(members)))
    scores = engine.pair_scores(pair_inputs) if pair_inputs else []
    ranked = []
    for cosine, candidate, supported, start, complete in layouts:
        member_scores = scores[start:start + len(supported)]
        if not member_scores:
            continue
        minimum = min(member_scores)
        ranked.append({"candidate": candidate, "evidence": {
            "score": round(minimum, 6), "content_support": round(minimum, 6),
            "minimum_group_member_score": round(minimum, 6),
            "minimum_group_content_support": round(minimum, 6),
            "maximum_group_member_score": round(max(member_scores), 6),
            "sbert_cosine": round(cosine, 6), "method": "sbert_cross_encoder_content_v1",
            "minimum_group_sbert_cosine": round(min(group_cosines[candidate.group_id]), 6),
            "model_fingerprint": FINGERPRINT, "all_members_checked": complete,
            "members_checked": len(supported), "title_used": False,
        }})
    return sorted(ranked, key=lambda row: (-row["evidence"]["score"], row["candidate"].id))


def label_corroboration_enabled() -> bool:
    return os.getenv("SEMANTIC_GROUPING_LABEL_CORROBORATION", "True").lower() in {
        "1", "true", "yes",
    }


def _specific_normalized_label(title: str) -> str:
    # Local imports avoid making the content extraction and linking modules part
    # of semantic model startup. Both helpers are established lessons-app rules.
    from .content_generator import is_structural_metadata_label
    from .learning_resource_linker import normalize_learning_object_title

    label = normalize_learning_object_title(title)
    if (
        not label
        or is_structural_metadata_label(title)
        or label in _GENERIC_INSTRUCTIONAL_LABELS
        or _GENERIC_NUMBERED_LABEL.fullmatch(label)
    ):
        return ""
    return label


def _label_corroborated_decision(
    *,
    source,
    content,
    kind,
    all_objects,
    eligible_groups,
    members,
    config,
    started_at,
):
    """Promote an already-supported exact-label twin without adding review work."""
    # Review mode is a deliberate no-auto-grouping safety setting used while
    # collecting teacher labels. Corroboration must not bypass that contract.
    if not label_corroboration_enabled() or mode() != "auto":
        return None
    if (
        source is None
        or source.kind != kind
        or source.represented_by_id is not None
        or not (source.material.generated_json or {}).get("learning_objects_confirmed")
    ):
        return None

    label = _specific_normalized_label(source.title)
    if not label:
        return None

    from .learning_resource_linker import normalize_learning_object_title

    # Count all eligible, active objects rather than only the cosine shortlist.
    # Duplicate same-label objects within either material make the label
    # ambiguous and therefore disable this automatic path.
    label_objects = [
        item for item in all_objects
        if item.kind == kind
        and item.group_id is not None
        and item.represented_by_id is None
        and (item.material.generated_json or {}).get("learning_objects_confirmed")
        and normalize_learning_object_title(item.title) == label
    ]
    source_matches = [item for item in label_objects if item.material_id == source.material_id]
    if len(source_matches) != 1 or source_matches[0].id != source.id:
        return None

    twins = [
        item for item in label_objects
        if item.material_id != source.material_id and item.group_id in eligible_groups
    ]
    if not twins:
        return None
    by_material = defaultdict(list)
    for twin in twins:
        by_material[twin.material_id].append(twin)
    if any(len(rows) != 1 for rows in by_material.values()):
        return None

    # With three or more PDFs, identical labels may already point at competing
    # groups. Do not choose one arbitrarily; ordinary semantic behavior remains.
    destination_group_ids = {twin.group_id for twin in twins}
    if len(destination_group_ids) != 1:
        return None
    destination_group_id = next(iter(destination_group_ids))
    destination_members = members[destination_group_id]
    if any(item.represented_by_id is not None for item in destination_members):
        return None

    # Explicitly score the exact-label destination group. It may not have
    # survived the ordinary cosine top-k shortlist.
    ranked = rank_groups(
        content,
        twins,
        {destination_group_id: destination_members},
        thresholds={**config, "top_k": 1},
    )
    if not ranked:
        return None
    match = ranked[0]
    evidence = dict(match["evidence"])
    if (
        not evidence["all_members_checked"]
        or evidence["score"] < config["review_threshold"]
    ):
        return None

    evidence.update(
        method="label_corroborated_sbert_cross_encoder",
        title_used=True,
        normalized_label=label,
        label_corroborated=True,
        auto_eligible=True,
        review_threshold=config["review_threshold"],
        auto_threshold=config["auto_threshold"],
        auto_calibrated=config["auto_threshold_source"] == "validated_calibration",
        minimum_sbert_cosine=config["minimum_sbert_cosine"],
        auto_threshold_source=config["auto_threshold_source"],
        elapsed_ms=round((perf_counter() - started_at) * 1000, 1),
    )
    return {**match, "evidence": evidence, "confidence": "high"}


def semantic_decision(material, title, content, kind, order, section_title="", source_object_id=None):
    from lessons.models import LearningObject, LearningObjectMatchSuggestion
    # Automatic matching may add a standalone object to a group, but must never
    # take an existing member away from its companions (including teacher groups).
    if source_object_id:
        source = LearningObject.objects.filter(pk=source_object_id).first()
        if source and source.group_id and LearningObject.objects.filter(
                group_id=source.group_id).exclude(pk=source_object_id).exists():
            return None
    start = perf_counter()
    config = policy()
    all_objects = list(LearningObject.objects.filter(
        material__outline_node_id=material.outline_node_id, group__isnull=False,
    ).select_related("material", "group", "represented_by"))
    members = defaultdict(list)
    for item in all_objects:
        if item.id != source_object_id:
            members[item.group_id].append(item)
    eligible_groups = {
        group_id for group_id, rows in members.items()
        if all(item.material_id != material.id and item.kind == kind
               and (item.material.generated_json or {}).get("learning_objects_confirmed") for item in rows)
    }
    rejected_ids = set()
    if source_object_id:
        from django.db.models import Q
        for left, right in LearningObjectMatchSuggestion.objects.filter(
            Q(source_learning_object_id=source_object_id) | Q(candidate_learning_object_id=source_object_id),
            status=LearningObjectMatchSuggestion.Status.REJECTED,
        ).values_list("source_learning_object_id", "candidate_learning_object_id"):
            rejected_ids.add(right if left == source_object_id else left)
    eligible_groups -= {group_id for group_id, rows in members.items() if any(row.id in rejected_ids for row in rows)}
    candidates = [row for row in all_objects if row.group_id in eligible_groups and row.id != source_object_id]
    ranked = rank_groups(content, candidates, members, thresholds=config)
    normal_decision = None
    if ranked:
        best = ranked[0]
        runner = ranked[1]["evidence"]["score"] if len(ranked) > 1 else 0.0
        evidence = best["evidence"]
        margin = evidence["score"] - runner
        auto = config["auto_threshold"]
        high = (mode() == "auto" and auto is not None and evidence["score"] >= auto
                and evidence["minimum_group_sbert_cosine"] >= config["minimum_sbert_cosine"]
                and evidence["all_members_checked"] and margin >= config["minimum_margin"])
        review = evidence["score"] >= config["review_threshold"]
        evidence.update(runner_up_score=runner, winner_margin=margin,
                        auto_eligible=high, review_threshold=config["review_threshold"],
                        auto_threshold=auto,
                        auto_calibrated=config["auto_threshold_source"] == "validated_calibration",
                        minimum_sbert_cosine=config["minimum_sbert_cosine"],
                        auto_threshold_source=config["auto_threshold_source"],
                        elapsed_ms=round((perf_counter() - start) * 1000, 1))
        normal_decision = {
            **best,
            "confidence": "high" if high else "medium" if review else None,
        }
        if high:
            logger.info(
                "Semantic grouping: material=%s source=%s candidate=%s score=%.4f auto=%s elapsed_ms=%s",
                material.id, source_object_id, best["candidate"].id,
                evidence["score"], high, evidence["elapsed_ms"],
            )
            return normal_decision

    corroborated = _label_corroborated_decision(
        source=source if source_object_id else None,
        content=content,
        kind=kind,
        all_objects=all_objects,
        eligible_groups=eligible_groups,
        members=members,
        config=config,
        started_at=start,
    )
    decision = corroborated or normal_decision
    if decision:
        evidence = decision["evidence"]
        logger.info(
            "Semantic grouping: material=%s source=%s candidate=%s score=%.4f auto=%s elapsed_ms=%s",
            material.id, source_object_id, decision["candidate"].id,
            evidence["score"], decision["confidence"] == "high", evidence["elapsed_ms"],
        )
    return decision
