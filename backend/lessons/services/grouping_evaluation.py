"""Offline calibration: development thresholds, untouched held-out evaluation."""
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace

from .semantic_grouping import content_hash, FINGERPRINT
from .learning_resource_linker import learning_object_match_evidence, _match_configuration

LABELS = {"equivalent", "related", "unrelated"}


def read_labeled_pairs(path):
    rows = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        raise ValueError("No pairs supplied")
    split_ids = {"development": set(), "test": set()}
    pair_ids = set()
    for row in rows:
        if row.get("label") not in LABELS or row.get("split") not in split_ids or not row.get("reviewed_by", "").strip():
            raise ValueError("Every pair needs a human label, reviewed_by, and development/test split.")
        hashes = []
        for side in ("left", "right"):
            item = row[side]
            if not item.get("content", "").strip():
                raise ValueError("Blank content cannot be used for calibration")
            digest = content_hash(item["content"])
            hashes.append(digest)
            split_ids[row["split"]].add(digest)
            if item.get("metadata_id"):
                split_ids[row["split"]].add(item["metadata_id"])
        pair_key = tuple(sorted(hashes))
        if pair_key in pair_ids:
            raise ValueError("Duplicate/reversed content pairs found")
        pair_ids.add(pair_key)
    if split_ids["development"] & split_ids["test"]:
        raise ValueError("Data leakage: the same object or content occurs in development and test. Split by independent PDFs/topics.")
    for split in split_ids:
        labels = {row["label"] for row in rows if row["split"] == split}
        if "equivalent" not in labels or not labels & {"related", "unrelated"}:
            raise ValueError(f"{split} needs both equivalent and non-equivalent pairs")
    return rows


def metrics(rows, high, review):
    automatic = [row for row in rows if high is not None and row["semantic_score"] is not None and row["semantic_score"] >= high]
    false_auto = sum(row["label"] != "equivalent" for row in automatic)
    positives = sum(row["label"] == "equivalent" for row in rows)
    reviewed = sum(row["semantic_score"] is None or (row["semantic_score"] >= review and (high is None or row["semantic_score"] < high)) for row in rows)
    missed = sum(row["label"] == "equivalent" and row["semantic_score"] is not None and row["semantic_score"] < review for row in rows)
    n = len(automatic)
    precision = (n - false_auto) / n if n else None
    # Wilson lower bound communicates how little a small test set proves.
    z = 1.96
    lower = ((precision + z*z/(2*n) - z*math.sqrt(precision*(1-precision)/n + z*z/(4*n*n))) / (1+z*z/n)) if n else None
    return {"pairs": len(rows), "auto_predictions": n, "false_auto": false_auto,
            "auto_precision": precision, "auto_precision_wilson_lower_95": lower,
            "equivalent_pairs": positives, "negative_pairs": len(rows)-positives,
            "auto_recall": (n-false_auto)/positives if positives else None,
            "review_pairs": reviewed, "review_rate": reviewed/len(rows) if rows else None,
            "missed_equivalent_pairs": missed}


def calibrate(rows):
    development = [row for row in rows if row["split"] == "development"]
    values = sorted({row["semantic_score"] for row in development if row["semantic_score"] is not None})
    high = None
    for threshold in values:
        result = metrics(development, threshold, 0)
        if result["auto_predictions"] >= 10 and result["false_auto"] == 0:
            high = threshold
            break
    # Retain >=95% of supported development positives in auto/review, reducing
    # teacher load only within that constraint. Missing/long pairs need review.
    positives = [row["semantic_score"] for row in development if row["label"] == "equivalent" and row["semantic_score"] is not None]
    review = 0.0
    for threshold in values:
        if positives and sum(score >= threshold for score in positives)/len(positives) >= .95 and (high is None or threshold <= high):
            review = threshold
    return {"auto_threshold": high, "review_threshold": review, "minimum_margin": .05, "top_k": 10}


def evaluate(rows, engine):
    scored = []
    lexical_configuration = _match_configuration()
    for row in rows:
        left, right = row["left"], row["right"]
        candidate = SimpleNamespace(title=right.get("title", ""), content=right["content"],
                                    order=right.get("order", 0), section_title=right.get("section_title", ""))
        lexical = learning_object_match_evidence(left.get("title", ""), left["content"], left.get("order", 0), candidate, left.get("section_title", ""))
        score = engine.pair_scores([(left["content"], right["content"])])[0] if engine.supports_pair(left["content"], right["content"]) else None
        scored.append({**row, "semantic_score": score, "lexical_evidence": lexical})
    thresholds = calibrate(scored)
    held_out = [row for row in scored if row["split"] == "test"]
    validation = metrics(held_out, thresholds["auto_threshold"], thresholds["review_threshold"])
    baseline = [{**row, "semantic_score": row["lexical_evidence"]["score"]} for row in held_out]
    # Report the actual pair-level lexical auto guard, not raw-score precision.
    lexical_auto = [row for row in baseline if row["semantic_score"] >= lexical_configuration["auto_threshold"]
                    and row["lexical_evidence"]["content_support"] >= lexical_configuration["content_support_threshold"]]
    baseline_precision = sum(row["label"] == "equivalent" for row in lexical_auto)/len(lexical_auto) if lexical_auto else None
    return {"model_fingerprint": FINGERPRINT, "thresholds": thresholds, "validation": validation,
            "development": metrics([row for row in scored if row["split"] == "development"], thresholds["auto_threshold"], thresholds["review_threshold"]),
            "lexical_test": {"auto_predictions": len(lexical_auto), "auto_precision": baseline_precision},
            "approved_for_auto": False,
            "scope": "Pair-level STS calibration only. Retrieval recall and complete-group validation require the shadow audit and teacher inspection.",
            "dataset_sha256": hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest(),
            "scored_pairs": scored}
