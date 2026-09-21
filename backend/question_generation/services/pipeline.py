import logging
import re
import sys
from collections import Counter
from math import ceil

from django.conf import settings
from django.db import transaction

from .bloom_classifier import BLOOM_TO_DIFFICULTY, BloomClassifier
from .question_generator import (
    generate_questions,
    question_bank_fingerprint,
    warm_question_model,
)

logger = logging.getLogger(__name__)

# Windows consoles often default to a legacy codepage (e.g. cp1252) that
# can't encode the ✓/✗/⊘/→/─/═ symbols this module emits, which would
# otherwise crash a run the first time one is written. Force UTF-8 output so
# the trace is reliable regardless of the terminal's codepage.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# ── Configuration ──
# How many questions per thinking order per content node (LearningObject).
# LOT and HOT are cognitive categories, not difficulty tiers — the counts are
# equal because neither is "the harder half" of the bank.
def thinking_order_counts():
    """Target questions per thinking order, overridable per deployment.

    Read at call time rather than import time so a settings override in a test
    or a changed .env takes effect without reloading the module.
    """
    return {
        "LOT": int(getattr(settings, "QUESTION_COUNT_LOT", 3)),
        "HOT": int(getattr(settings, "QUESTION_COUNT_HOT", 3)),
    }


def _split(count, weights):
    """Divide ``count`` across formats by weight, giving the remainder to the
    first format so the split always sums back to ``count``."""
    total = sum(weights.values())
    split = {fmt: count * weight // total for fmt, weight in weights.items()}
    first = next(iter(weights))
    split[first] += count - sum(split.values())
    return {fmt: n for fmt, n in split.items() if n}


# How many questions per thinking order per content node (LearningObject).
# LOT and HOT are cognitive categories, not difficulty tiers -- the counts are
# equal because neither is "the harder half" of the bank.
#
# Each thinking order is ONE LLM call. The format split is requested inside
# that call rather than split across calls: multiple-choice and true/false
# questions come back in the same structured response, so LOT no longer costs
# two round trips. The split is stated explicitly because true/false is much
# cheaper for the model to produce, and left to itself the bank drifts toward
# it -- and a true/false question a learner can guess right half the time is
# weak evidence of mastery.
_COUNTS = thinking_order_counts()
QUESTION_DISTRIBUTION = {
    "LOT": {
        "count": _COUNTS["LOT"],
        "format_split": _split(_COUNTS["LOT"], {"MCQ": 2, "TF": 1}),
    },
    "HOT": {
        "count": _COUNTS["HOT"],
        "format_split": _split(_COUNTS["HOT"], {"MCQ": 1}),
    },
}
# Per node: one call per thinking order, 3 LOT + 3 HOT = 6 questions

# Ask for more than the target in the one generation pass, so classification
# drift still leaves enough in each bucket. This is what replaced the old
# multi-round rebalancing: overgenerating inside the SAME set of LLM calls is
# far cheaper than issuing extra calls to correct a shortfall afterwards.
OVERGENERATION_FACTOR = max(
    1.0,
    float(getattr(settings, "QUESTION_OVERGENERATION_FACTOR", 1.0)),
)

# Bloom levels the classifier may return but that MCQ/TF cannot assess.
# "create" questions (design/construct/compose a novel artifact) have no
# single gradeable answer, so they are dropped during post-processing.
# The trained model still predicts "create" — the exclusion is ours, applied
# at the application level, and the model is deliberately left alone.
UNASSESSABLE_BLOOM_LEVELS = {"create"}

# loaded once per process — reloading RoBERTa on every run costs ~10s
_classifier_cache = None


def _get_classifier():
    global _classifier_cache
    if _classifier_cache is None:
        _classifier_cache = BloomClassifier()
    return _classifier_cache


def _emit(on_event, event_type, message, **data):
    """Send a trace event to the optional callback. No-op when tracing is off."""
    if on_event:
        on_event(event_type, message, data or None)


def _dedup_key(question_text):
    """Normalized form used to spot two questions that only differ cosmetically.

    Lowercased, punctuation dropped and whitespace collapsed, so
    "What is matter?" and "what is  matter" collide.
    """
    text = (question_text or "").lower()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    return re.sub(r"\s+", " ", text).strip()


# ── Trace printing ──

def _print_node_summary(node, kept, rejected):
    """One line per node. The per-question detail sits at debug level.

    This used to be an eight-line block framed by dividers, per node -- which
    for a 33-node material buried the one thing a reader wants from a trace:
    where it is now, and whether it is still moving.
    """
    counts = Counter(q.thinking_order for q in kept)
    breakdown = ", ".join(f"{counts.get(order, 0)} {order}" for order in QUESTION_DISTRIBUTION)
    short = [
        order for order, config in QUESTION_DISTRIBUTION.items()
        if counts.get(order, 0) < config["count"]
    ]
    logger.info(
        'node "%s": kept %s (%s), discarded %s%s',
        node.title, len(kept), breakdown, len(rejected),
        f" — short on {', '.join(short)}" if short else "",
    )


def _print_material_summary(material, node_count, all_questions, stats):
    counts = Counter(q.thinking_order for q in all_questions)
    distribution = ", ".join(f"{counts.get(order, 0)} {order}" for order in QUESTION_DISTRIBUTION)
    logger.info(
        'finished "%s": %s questions across %s nodes (%s) from %s drafts '
        "[excluded %s, duplicates %s, trimmed %s]",
        material.title, len(all_questions), node_count, distribution,
        stats["total_drafted"], stats["excluded_create"],
        stats["duplicates"], stats["trimmed"],
    )


def concept_source_text(node):
    """The text a concept's questions are written from.

    A concept may be taught as several objects of one PDF, and the Normal
    track speaks all of them. Generating from the lead object alone would ask
    about a fraction of what the student hears.
    """
    from course.version_assignment import version_bundles
    from lessons.services.concept_bundles import bundle_text

    if node.group_id is None:
        return node.content or ""
    normal = version_bundles(node.group).get("NORMAL") or []
    if not any(item.id == node.id for item in normal):
        return node.content or ""
    return bundle_text(normal) or (node.content or "")


def _is_concept_source(node):
    """Whether ``node`` is the one object of its concept that generates.

    A concept's Normal bundle can be several objects (see
    ``concept_source_text``); every one of them would otherwise read the same
    bundle text and generate the same bank. Only the bundle's lead -- the
    object questions are saved against -- generates. An ungrouped object, or
    one whose bundle is not the concept's Normal source, is unaffected and
    always generates from its own text.
    """
    if node.group_id is None:
        return True
    from course.version_assignment import version_bundles
    from lessons.services.concept_bundles import bundle_lead

    normal = version_bundles(node.group).get("NORMAL") or []
    if not any(item.id == node.id for item in normal):
        return True
    lead = bundle_lead(normal)
    return lead is not None and lead.id == node.id


# ── Phase 1: generation (LLM) ──

def _draft_questions_for_node(
    node,
    classifier,
    on_event=None,
    generation_fingerprint="",
):
    """Run every LLM call for one node and persist the results as drafts.

    Nothing is classified, deduplicated or trimmed here — the questions go
    to the database exactly as the LLM produced them (after structural
    validation), so a crash later in the run cannot lose generated work.
    """
    from question_generation.models import GeneratedQuestion

    draft_rows = GeneratedQuestion.objects.filter(node=node, status="draft")
    # A changed source, model, prompt, or generation contract invalidates old
    # partial work. Matching fingerprints are safe to resume.
    draft_rows.exclude(generation_fingerprint=generation_fingerprint).delete()
    resumed = list(
        draft_rows.filter(generation_fingerprint=generation_fingerprint).order_by("id")
    )

    # Drafts created before resumable generation did not record which request
    # produced them. Classify those once so an in-progress bank can still
    # receive credit on its first retry after this upgrade.
    inferred = []
    for draft in resumed:
        if draft.thinking_order:
            continue
        classification = classifier.classify(draft.question_text)
        thinking_order = classification.get("thinking_order")
        if thinking_order in QUESTION_DISTRIBUTION:
            draft.thinking_order = thinking_order
            inferred.append(draft)
    if inferred:
        GeneratedQuestion.objects.bulk_update(inferred, ["thinking_order"])

    resumed_counts = Counter(
        (draft.thinking_order, draft.question_format)
        for draft in resumed
        if draft.thinking_order in QUESTION_DISTRIBUTION
    )
    if resumed:
        _emit(
            on_event,
            "questions_resumed",
            f"Resumed {len(resumed)} compatible saved draft(s)",
            node_id=node.id,
            count=len(resumed),
            by_thinking_order=dict(
                Counter(draft.thinking_order or "unclassified" for draft in resumed)
            ),
        )

    drafted = len(resumed)
    for thinking_order, config in QUESTION_DISTRIBUTION.items():
        # Pad each format so classification drift still leaves enough in the
        # bucket, then ask for the whole mix in one call.
        targets = {
            fmt: max(1, ceil(n * OVERGENERATION_FACTOR))
            for fmt, n in config["format_split"].items()
        }
        padded = {
            fmt: target - resumed_counts.get((thinking_order, fmt), 0)
            for fmt, target in targets.items()
            if target - resumed_counts.get((thinking_order, fmt), 0) > 0
        }
        if not padded:
            _emit(
                on_event,
                "generation_step_resumed",
                f"Reused completed {thinking_order} drafts",
                node_id=node.id,
                thinking_order=thinking_order,
                count=sum(
                    resumed_counts.get((thinking_order, fmt), 0)
                    for fmt in targets
                ),
            )
            continue
        summary = " + ".join(f"{n} {fmt}" for fmt, n in padded.items())
        print(f"Generating {summary} {thinking_order} question(s) for: {node.title}")
        def record_metrics(metrics, order=thinking_order):
            _emit(
                on_event,
                "llm_metrics",
                f"{order} model call completed",
                node_id=node.id,
                thinking_order=order,
                **metrics,
            )

        questions = generate_questions(
            content=concept_source_text(node),
            thinking_order=thinking_order,
            format_split=padded,
            on_metrics=record_metrics,
        )
        batch = [
            GeneratedQuestion(
                node=node,
                question_text=q["question"],
                question_format=q["format"],
                choices=q.get("choices"),
                correct_answer=q["correct_answer"],
                explanation=q.get("explanation", ""),
                # While this row is a draft, this is the generation request
                # that produced it. Finalization replaces it with the Bloom
                # classifier's authoritative result.
                thinking_order=thinking_order,
                status="draft",
                generation_fingerprint=generation_fingerprint,
            )
            for q in questions
        ]
        GeneratedQuestion.objects.bulk_create(batch)
        drafted += len(batch)
        for q in batch:
            logger.debug('  Q: "%s" [%s, drafted]', q.question_text, q.question_format)
        _emit(
            on_event, "questions_drafted",
            f"Saved {len(batch)} {thinking_order} draft(s)",
            node_id=node.id, count=len(batch),
            requested=sum(padded.values()), thinking_order=thinking_order,
            formats=sorted(padded),
        )
    return drafted


# ── Phase 2: post-processing (deterministic, no LLM) ──

def finalize_node_questions(node, classifier, on_event=None, stats=None):
    """Classify, deduplicate and trim one node's drafts, then promote them.

    Runs entirely over rows already in the database and needs no LLM. The
    order matters: deduplicate first (so identical text isn't classified
    twice), then classify, then drop unassessable levels, then trim to quota.

    The promotion is atomic — the previous run's final questions survive
    until the moment this node's replacements are ready, so an interrupted
    run never leaves a node with no questions.

    NOTE: deleting a question cascades to its LearnerResponse rows —
    regenerating resets learner history for that node's questions.
    """
    from question_generation.models import GeneratedQuestion

    drafts = list(
        GeneratedQuestion.objects.filter(node=node, status="draft").order_by("id")
    )

    keep = []
    reject_ids = []
    counts = Counter()
    seen = set()
    duplicates = excluded_create = trimmed = 0

    for draft in drafts:
        key = _dedup_key(draft.question_text)
        if key in seen:
            duplicates += 1
            reject_ids.append(draft.id)
            logger.debug('  ⊘ duplicate — "%s"', draft.question_text)
            _emit(
                on_event, "question_dropped", draft.question_text,
                reason="duplicate of an earlier question", node_id=node.id,
            )
            continue
        seen.add(key)

        classification = classifier.classify(draft.question_text)
        bloom_level = classification["bloom_level"]
        thinking_order = classification["thinking_order"]

        if bloom_level in UNASSESSABLE_BLOOM_LEVELS or thinking_order is None:
            excluded_create += 1
            reject_ids.append(draft.id)
            logger.debug('  ⊘ excluded (%s) — "%s"', bloom_level, draft.question_text)
            _emit(
                on_event, "question_dropped", draft.question_text,
                reason=f"{bloom_level}-level question cannot be assessed by MCQ/TF",
                bloom_level=bloom_level, node_id=node.id,
            )
            continue

        if counts[thinking_order] >= QUESTION_DISTRIBUTION[thinking_order]["count"]:
            trimmed += 1
            reject_ids.append(draft.id)
            logger.debug('  ⊘ surplus %s — "%s"', thinking_order, draft.question_text)
            continue

        draft.bloom_level = bloom_level
        draft.thinking_order = thinking_order
        # A different axis from thinking_order, kept for the adaptive
        # engine's difficulty-based remediation — see GeneratedQuestion.
        # Derived straight from bloom_level rather than trusted from the
        # classifier's return value, so a classifier/stub that only returns
        # bloom_level/thinking_order/category still works.
        draft.difficulty = classification.get("difficulty") or BLOOM_TO_DIFFICULTY.get(bloom_level, "")
        draft.category = classification["category"]
        draft.status = "final"
        counts[thinking_order] += 1
        keep.append(draft)
        logger.debug(
            '  ✓ %s (%s) — "%s"', thinking_order, bloom_level, draft.question_text
        )

    for thinking_order, config in QUESTION_DISTRIBUTION.items():
        short = config["count"] - counts.get(thinking_order, 0)
        if short > 0:
            _emit(
                on_event, "shortfall_warning",
                f"{thinking_order} came up {short} question(s) short — accepting as is",
                node_id=node.id, thinking_order=thinking_order, short=short,
            )

    with transaction.atomic():
        # A concept owns one question bank, grounded in its Normal source.
        # When that bank is regenerated, remove older generated banks attached
        # to Simplified, Elaborated, or Extra source objects in the same group.
        if node.group_id:
            obsolete = GeneratedQuestion.objects.filter(
                node__group_id=node.group_id,
            ).exclude(node=node)
            obsolete_ids = list(obsolete.values_list("id", flat=True))
            if obsolete_ids:
                from lessons.models import Question
                Question.objects.filter(
                    source_type=Question.SourceType.GENERATED,
                    adaptive_question_id__in=obsolete_ids,
                ).delete()
                obsolete.delete()

        # the previous run's questions are replaced only now, once this run
        # actually has something to replace them with
        replaced, _ = GeneratedQuestion.objects.filter(node=node, status="final").delete()
        if reject_ids:
            GeneratedQuestion.objects.filter(id__in=reject_ids).delete()
        GeneratedQuestion.objects.bulk_update(
            keep, ["bloom_level", "thinking_order", "difficulty", "category", "status"]
        )

        material = node.material
        generated_json = material.generated_json or {}
        if generated_json:
            generated_json["question_audio_generated"] = False
            generated_json["audio_playlist_generated"] = False
            material.generated_json = generated_json
            material.save(update_fields=["generated_json"])

    # Keep the teacher's Step 2 list and the learner-facing adaptive bank in
    # one visible workflow. The adaptive rows remain authoritative for play.
    from lessons.services.question_workflow import mirror_generated_questions
    mirror_generated_questions(node, keep)

    if replaced:
        logger.debug("replaced %s existing rows for node %s", replaced, node.id)
    logger.debug("saved %s questions for node %s", len(keep), node.id)

    if stats is not None:
        stats["excluded_create"] = stats.get("excluded_create", 0) + excluded_create
        stats["duplicates"] = stats.get("duplicates", 0) + duplicates
        stats["trimmed"] = stats.get("trimmed", 0) + trimmed

    _print_node_summary(node, keep, reject_ids)
    return keep


def generate_questions_for_node(
    node,
    classifier,
    on_event=None,
    stats=None,
    generation_fingerprint="",
):
    """Generate and finalize one LearningObject's question bank.

    Two phases: every LLM call happens first and lands in the database as
    drafts, then a single deterministic pass classifies, deduplicates and
    trims them. There is no regeneration loop — LOT and HOT are wide enough
    that ordinary prompt drift lands inside the intended bucket, and a bucket
    that still comes up short is accepted with a warning rather than paid for
    with more LLM calls.

    on_event(event_type, message, data) receives trace events when provided.
    stats, when given a dict, accumulates run totals for a material summary.
    """
    drafted = _draft_questions_for_node(
        node,
        classifier,
        on_event=on_event,
        generation_fingerprint=generation_fingerprint,
    )
    if stats is not None:
        stats["total_drafted"] = stats.get("total_drafted", 0) + drafted

    _emit(
        on_event, "node_classifying",
        f"Classifying and filtering {drafted} draft(s)",
        node_id=node.id, drafted=drafted,
    )
    return finalize_node_questions(node, classifier, on_event=on_event, stats=stats)


def _complete_current_bank(node, fingerprint):
    """Return a reusable complete bank, or an empty list when regeneration is needed."""
    final = list(node.generated_questions.filter(status="final").order_by("id"))
    if not final or any(q.generation_fingerprint != fingerprint for q in final):
        return []
    counts = Counter(q.thinking_order for q in final)
    if any(
        counts.get(order, 0) < config["count"]
        for order, config in QUESTION_DISTRIBUTION.items()
    ):
        return []
    return final


def generate_questions_for_material(
    material,
    on_event=None,
    node_ids=None,
    *,
    skip_complete=False,
):
    """Full pipeline: LearningMaterial → classified questions for its text
    learning objects, straight from the database (no JSON handoff).

    With node_ids, only those learning objects are (re)generated; other
    nodes' stored questions are left untouched. Each node is finalized as
    soon as it finishes, so an interrupted run keeps every completed node's
    questions."""
    from question_generation.models import GeneratedQuestion

    nodes_qs = (
        material.learning_objects
        .exclude(content="")
        .order_by("order")
    )
    if node_ids is not None:
        nodes_qs = nodes_qs.filter(id__in=node_ids)
    candidates = list(nodes_qs)
    nodes = [node for node in candidates if _is_concept_source(node)]
    if len(nodes) != len(candidates):
        # Asking for one of these by id generates nothing at all, which used to
        # happen in silence. Its questions exist -- they are written from the
        # whole Normal bundle and saved against that bundle's lead.
        logger.info(
            "Skipping %s learning object(s) taught through another object's "
            "concept bundle; their questions belong to that bundle's lead: %s",
            len(candidates) - len(nodes),
            ", ".join(
                f'"{node.title}" (#{node.id})'
                for node in candidates if node not in nodes
            ),
        )
    fingerprints = {
        node.id: question_bank_fingerprint(concept_source_text(node), QUESTION_DISTRIBUTION)
        for node in nodes
    }
    reusable = {
        node.id: _complete_current_bank(node, fingerprints[node.id])
        for node in nodes
    } if skip_complete else {}
    nodes_to_generate = [node for node in nodes if not reusable.get(node.id)]
    if node_ids is None:
        # full-material run: drop stale questions on nodes this run will not
        # touch (e.g. a learning object whose content was emptied since the
        # last run)
        GeneratedQuestion.objects.filter(node__material=material).exclude(
            node__in=nodes).delete()
    _emit(
        on_event, "material_started",
        f"Generating questions for {len(nodes)} content nodes",
        material_id=material.id, material_title=material.title,
        node_count=len(nodes), generate_count=len(nodes_to_generate),
        reuse_count=len(nodes) - len(nodes_to_generate),
    )

    all_questions = []
    stats = {
        "total_drafted": 0,
        "excluded_create": 0,
        "duplicates": 0,
        "trimmed": 0,
        "reused_nodes": len(nodes) - len(nodes_to_generate),
    }
    classifier = None
    if nodes_to_generate:
        # Production background runs always provide the trace callback. Keep
        # direct service/test calls network-free unless they actually generate.
        if on_event:
            _emit(on_event, "model_warming", "Loading question model into Ollama")

            def record_warm_metrics(metrics):
                _emit(
                    on_event,
                    "model_warmed",
                    "Question model is ready",
                    **metrics,
                )

            warm_question_model(on_metrics=record_warm_metrics)
        if _classifier_cache is None:
            _emit(on_event, "classifier_loading", "Loading Bloom's classifier model")
        classifier = _get_classifier()

    total_nodes = len(nodes)
    for position, node in enumerate(nodes, start=1):
        cached = reusable.get(node.id)
        if cached:
            all_questions.extend(cached)
            _emit(
                on_event,
                "node_skipped",
                f"Reused unchanged question bank: {node.title}",
                node_id=node.id,
                title=node.title,
                count=len(cached),
                index=position,
                total=total_nodes,
            )
            continue
        logger.info('(%s/%s) generating questions for "%s"', position, total_nodes, node.title)
        # index/total are what let the teacher's dialog draw a real progress
        # bar. Without them it can only spin, and a spinner cannot tell slow
        # apart from stuck -- which is the whole complaint about this step.
        _emit(
            on_event, "node_started", f"Generating questions for: {node.title}",
            node_id=node.id, title=node.title,
            index=position, total=total_nodes,
        )
        questions = generate_questions_for_node(
            node,
            classifier,
            on_event=on_event,
            stats=stats,
            generation_fingerprint=fingerprints[node.id],
        )
        all_questions.extend(questions)
        _emit(
            on_event, "node_finished",
            f"Finished node: saved {len(questions)} questions",
            node_id=node.id,
            count=len(questions),
            index=position, total=total_nodes,
            by_thinking_order=dict(Counter(q.thinking_order for q in questions)),
        )

    _print_material_summary(material, len(nodes), all_questions, stats)
    _emit(
        on_event, "material_finished",
        f"Completed {len(all_questions)} questions from {stats['total_drafted']} new drafts",
        total=len(all_questions),
        drafted=stats["total_drafted"],
        reused_nodes=stats["reused_nodes"],
        excluded_create=stats["excluded_create"],
        duplicates=stats["duplicates"],
        trimmed=stats["trimmed"],
        by_thinking_order=dict(Counter(q.thinking_order for q in all_questions)),
        by_bloom=dict(Counter(q.bloom_level for q in all_questions)),
    )
    return all_questions
