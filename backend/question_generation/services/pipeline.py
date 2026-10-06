import logging
import re
import sys
from collections import Counter
from math import ceil

from django.conf import settings
from django.db import transaction
from django.db.models import Q

from config.console import name
from .bloom_classifier import BloomClassifier
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


def question_minimum():
    """LOTS and HOTS every concept needs before the Questions step is done."""
    return {
        "LOT": int(getattr(settings, "QUESTION_MIN_LOT", 4)),
        "HOT": int(getattr(settings, "QUESTION_MIN_HOT", 2)),
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
        # Multiple-choice only left HOT hostage to one format: when MCQ
        # generation broke, every HOT call produced nothing and the bucket was
        # filled by accident, from LOT output the Bloom classifier relabelled.
        # It stays majority multiple-choice -- a true/false question a learner
        # can guess right half the time is weak evidence of mastery -- but it
        # now has somewhere to fall back to.
        "format_split": _split(_COUNTS["HOT"], {"MCQ": 2, "TF": 1}),
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


def concept_name(node):
    """What a run is about: the concept, not the object its bank is filed under.

    A concept's questions are written from every object of its Standard version
    and filed under the first one, so naming that object ("Shape") read as if
    only it had been used.
    """
    group = getattr(node, "group", None)
    return (group.label if group is not None and group.label else node.title) or "Untitled concept"


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
    from lessons.services.question_workflow import concept_tier_counts

    added = Counter(q.thinking_order for q in kept)
    breakdown = ", ".join(f"{added.get(order, 0)} {order}" for order in QUESTION_DISTRIBUTION)
    # Judged on the concept's whole bank against the minimum: this run's own
    # output against its request said "short" for concepts that were not.
    have = concept_tier_counts(node)
    minimum = question_minimum()
    short = [
        f"{order} {have[order]} of {minimum[order]}"
        for order in QUESTION_DISTRIBUTION if have[order] < minimum[order]
    ]
    logger.info(
        "[Questions] %s  added %s (%s), discarded %s -- now %s, %s  (object %s)",
        name(concept_name(node)), len(kept), breakdown, len(rejected),
        " / ".join(f"{have[order]} {order}" for order in QUESTION_DISTRIBUTION),
        f"still short: {', '.join(short)}" if short else "meets the minimum",
        node.id,
    )


def _print_material_summary(material, node_count, all_questions, stats):
    counts = Counter(q.thinking_order for q in all_questions)
    distribution = ", ".join(f"{counts.get(order, 0)} {order}" for order in QUESTION_DISTRIBUTION)
    logger.info(
        "[Questions] PDF %s  done: %s questions for %s concepts (%s) from %s drafts; "
        "dropped %s not grounded in the lesson, %s duplicates, %s wrong level, %s extra",
        material.id, len(all_questions), node_count, distribution,
        stats["total_drafted"], stats["ungrounded"], stats["duplicates"],
        stats["excluded_create"], stats["trimmed"],
    )


def concept_source_text(node):
    """The text a concept's questions are written from: every telling of it.

    A concept holds one bundle per PDF, and the adaptive engine serves those
    bundles as the Standard, Simplified and Elaborated versions of one concept.
    A learner escalated to Elaborated hears another PDF's wording, so a bank
    written from the Standard bundle alone asks about text that learner was
    never read.

    It also starves the higher-order half of the bank. A HOT question has to
    combine two or more stated facts; measured on topic 276, the Standard
    bundle for "Solid" is 32 words and often does not hold two, so the model
    supplied the second from its own knowledge. Every telling together is
    220 words of the same concept -- more facts, no change of subject.
    """
    from course.version_assignment import bundle_roles, standard_material_id
    from lessons.services.concept_bundles import (
        bundle_text,
        bundles_for_group,
        material_order,
    )

    if node.group_id is None:
        return node.content or ""

    group = node.group
    # Blank-content objects are dropped, matching what version_bundles saw
    # through _eligible_bundles. Without this a whitespace-only sibling passes
    # the membership test below, reads the whole concept as its own source and
    # generates a second, identical bank.
    bundles = {
        material_id: kept
        for material_id, objects in bundles_for_group(group).items()
        if (kept := [item for item in objects if (item.content or "").strip()])
    }
    standard_id = standard_material_id(group)
    if not any(item.id == node.id for item in bundles.get(standard_id) or []):
        return node.content or ""

    roles = bundle_roles(group)
    # The Standard telling leads, then the rest in upload order, so the model
    # reads the concept the way the topic teaches it.
    rank = {
        material_id: index
        for index, material_id in enumerate(material_order(group.outline_node))
    }
    others = sorted(
        (material_id for material_id in bundles if material_id != standard_id),
        key=lambda material_id: (rank.get(material_id, len(rank)), material_id),
    )

    objects = []
    for material_id in [standard_id, *others]:
        if material_id != standard_id and material_id not in roles:
            continue
        objects.extend(bundles.get(material_id) or [])
    return bundle_text(objects) or (node.content or "")


def _is_concept_source(node):
    """Whether ``node`` is the one object of its concept that generates.

    A concept owns exactly one bank and it belongs to the Standard bundle's
    lead -- ``finalize_node_questions`` deletes every other bank in the group
    as soon as that lead finalizes. So an object from another PDF's telling
    is not merely redundant: its bank is already condemned when it is made.
    Measured on topic 276, that was 22 of 42 objects and 52% of a 117-minute
    run, spent on questions nothing would ever read.

    Two cases still generate from their own text, because no one else will
    speak for them: an object in no group at all, and one whose group has no
    Standard bundle to own it.
    """
    if node.group_id is None:
        return True
    from course.version_assignment import version_bundles
    from lessons.services.concept_bundles import bundle_lead

    standard = version_bundles(node.group).get("STANDARD") or []
    if not standard:
        return True
    if not any(item.id == node.id for item in standard):
        return False
    lead = bundle_lead(standard)
    return lead is not None and lead.id == node.id


# ── Phase 1: generation (LLM) ──

def _draft_questions_for_node(
    node,
    classifier,
    on_event=None,
    generation_fingerprint="",
    correction="",
    orders=None,
):
    """Run every LLM call for one node and persist the results as drafts.

    Nothing is classified, deduplicated or trimmed here — the questions go
    to the database exactly as the LLM produced them (after structural
    validation), so a crash later in the run cannot lose generated work.

    ``orders`` limits the calls to those thinking orders; a top-up asks only
    for the ones the concept is short of.
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
        if orders is not None and thinking_order not in orders:
            continue
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
        logger.debug("[Questions] %s  drafting %s %s question(s)", name(concept_name(node)), summary, thinking_order)
        def record_metrics(metrics, order=thinking_order):
            _emit(
                on_event,
                "llm_metrics",
                f"{order} model call completed",
                node_id=node.id,
                thinking_order=order,
                **metrics,
            )

        def record_error(attempt, reason, order=thinking_order):
            _emit(
                on_event,
                "question_generation_attempt_failed",
                f"{order} question generation attempt {attempt} failed: {reason}",
                node_id=node.id,
                thinking_order=order,
                attempt=attempt,
                reason=reason,
            )

        def record_rate_limit_wait(seconds, retry, order=thinking_order):
            logger.info("[Questions] %s  rate limit reached; waiting %.0fs before retrying", name(concept_name(node)), seconds)
            _emit(
                on_event,
                "groq_rate_limit_wait",
                f"Groq rate limit reached; waiting {seconds:.1f}s before retrying {order}",
                node_id=node.id,
                thinking_order=order,
                wait_seconds=round(seconds, 1),
                retry=retry,
            )

        questions = generate_questions(
            content=concept_source_text(node),
            thinking_order=thinking_order,
            format_split=padded,
            on_metrics=record_metrics,
            on_error=record_error,
            on_rate_limit_wait=record_rate_limit_wait,
            correction=correction,
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


# ── Phase 1b: grounding gate (corrective RAG) ──

def _validate_drafts_for_node(node, index, on_event=None, stats=None):
    """Check every draft against the topic's source text; delete what fails.

    Returns ``(surviving_count, failures)`` where each failure is
    ``(question_text, reason)`` — the material a corrective retry needs to
    tell the model what not to repeat.

    Deleting rather than flagging is deliberate. A draft the source does not
    support has no downstream use: the classifier would happily assign it a
    Bloom level, and a teacher reviewing forty questions cannot be the thing
    that catches a wrong answer key. See services/grounding.py.
    """
    from question_generation.models import GeneratedQuestion
    from .grounding import verify

    drafts = list(
        GeneratedQuestion.objects.filter(node=node, status="draft").order_by("id")
    )
    if not drafts:
        return 0, []

    failures, reject_ids, unverified = [], [], 0
    for draft in drafts:
        result = verify(draft, index)
        if result["stage"] in ("retrieval_unavailable", "judge_unavailable"):
            unverified += 1
        if result["passed"]:
            logger.debug('  ✓ grounded — "%s"', draft.question_text)
            continue
        failures.append((draft.question_text, result["reason"]))
        reject_ids.append(draft.id)
        logger.debug('  ⊘ ungrounded (%s) — "%s"', result["stage"], draft.question_text)
        _emit(
            on_event, "question_ungrounded", draft.question_text,
            reason=result["reason"], stage=result["stage"],
            verdict=result["verdict"], node_id=node.id,
            retrieved=result["retrieved"],
        )

    if reject_ids:
        GeneratedQuestion.objects.filter(id__in=reject_ids).delete()
    if stats is not None:
        stats["ungrounded"] = stats.get("ungrounded", 0) + len(reject_ids)
        stats["unverified"] = stats.get("unverified", 0) + unverified

    surviving = len(drafts) - len(reject_ids)
    _emit(
        on_event, "grounding_checked",
        f"Grounding gate kept {surviving} of {len(drafts)} draft(s)",
        node_id=node.id, kept=surviving, rejected=len(reject_ids),
        unverified=unverified,
    )
    return surviving, failures


def _bank_is_short(node, orders=None):
    """True while this node's surviving drafts cannot fill the quota.

    Measured on drafts rather than final rows because the gate runs before
    classification: a thinking order is judged by what was *requested* of it,
    which is what the draft still carries at this point.
    """
    from question_generation.models import GeneratedQuestion

    counts = Counter(
        GeneratedQuestion.objects.filter(node=node, status="draft")
        .values_list("thinking_order", flat=True)
    )
    return any(
        counts.get(order, 0) < config["count"]
        for order, config in QUESTION_DISTRIBUTION.items()
        if orders is None or order in orders
    )


# ── Phase 2: post-processing (deterministic, no LLM) ──

def finalize_node_questions(node, classifier, on_event=None, stats=None, append=False, caps=None):
    """Classify, deduplicate and trim one node's drafts, then promote them.

    Runs entirely over rows already in the database and needs no LLM. The
    order matters: deduplicate first (so identical text isn't classified
    twice), then classify, then drop unassessable levels, then trim to quota.

    The promotion is atomic — the previous run's final questions survive
    until the moment this node's replacements are ready, so an interrupted
    run never leaves a node with no questions.

    ``caps`` is how many of each thinking order this run may keep; a top-up
    passes what is left below the target after the concept's existing
    questions, so it fills a tier instead of piling onto it.

    NOTE: deleting a question cascades to its LearnerResponse rows —
    regenerating resets learner history for that node's questions.
    """
    from question_generation.models import GeneratedQuestion

    if caps is None:
        caps = {order: config["count"] for order, config in QUESTION_DISTRIBUTION.items()}

    drafts = list(
        GeneratedQuestion.objects.filter(node=node, status="draft").order_by("id")
    )

    keep = []
    reject_ids = []
    counts = Counter()
    seen = set()
    duplicates = excluded_create = trimmed = 0
    if append:
        # "Generate more" adds to the bank: a draft repeating a question the
        # concept already has is a duplicate like any other.
        seen.update(
            _dedup_key(text) for text in GeneratedQuestion.objects.filter(
                node=node, status="final",
            ).values_list("question_text", flat=True)
        )

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

        if counts[thinking_order] >= caps.get(thinking_order, 0):
            trimmed += 1
            reject_ids.append(draft.id)
            logger.debug('  ⊘ surplus %s — "%s"', thinking_order, draft.question_text)
            continue

        draft.bloom_level = bloom_level
        draft.thinking_order = thinking_order
        draft.category = classification["category"]
        draft.status = "final"
        counts[thinking_order] += 1
        keep.append(draft)
        logger.debug(
            '  ✓ %s (%s) — "%s"', thinking_order, bloom_level, draft.question_text
        )

    # A top-up's caps are room left, not a target; its summary line reports
    # the concept against the minimum instead.
    for thinking_order, cap in ({} if append else caps).items():
        short = cap - counts.get(thinking_order, 0)
        if short > 0:
            _emit(
                on_event, "shortfall_warning",
                f"{thinking_order} came up {short} question(s) short — accepting as is",
                node_id=node.id, thinking_order=thinking_order, short=short,
            )

    with transaction.atomic():
        # A concept owns one question bank, grounded in its Standard source.
        # When that bank is regenerated, remove older generated banks attached
        # to other source objects in the same group.
        # Questions the teacher edited are kept through a regeneration, next
        # to the new ones -- an older bank's included, moved to this one.
        # Only rows generation wrote: a printed or teacher-written question's
        # learner-facing copy shares this table, and a regeneration used to
        # delete the ones paired to the concept's other objects.
        generated = GeneratedQuestion.objects.filter(
            Q(teacher_question__isnull=True)
            | Q(teacher_question__source_type="generated")
        )
        edited = Q(teacher_question__teacher_edited=True)
        if node.group_id:
            generated.filter(
                edited, node__group_id=node.group_id, status="final",
            ).exclude(node=node).update(node=node)
            obsolete = generated.filter(
                node__group_id=node.group_id,
            ).exclude(node=node).exclude(edited)
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
        replaced, _ = (
            (0, None) if append
            else generated.filter(node=node, status="final").exclude(edited).delete()
        )
        if reject_ids:
            GeneratedQuestion.objects.filter(id__in=reject_ids).delete()
        # Stamped with the text the bank was written from, so a later change
        # to the concept shows the bank as out of date. Kept edited questions
        # join the new bank, so they carry its stamps too.
        from .bank_status import text_fingerprint
        source_stamp = text_fingerprint(concept_source_text(node))
        for question in keep:
            question.source_text_fingerprint = source_stamp
        GeneratedQuestion.objects.bulk_update(
            keep, ["bloom_level", "thinking_order", "category", "status", "source_text_fingerprint"]
        )
        if keep:
            generated.filter(edited, node=node, status="final").update(
                source_text_fingerprint=source_stamp,
                generation_fingerprint=keep[0].generation_fingerprint,
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
    grounding_index=None,
    append=False,
):
    """Generate, ground-check and finalize one LearningObject's question bank.

    Three phases. Every LLM call happens first and lands in the database as
    drafts. The grounding gate then checks each draft against the topic's own
    source text and deletes what it cannot support, retrying generation with
    the rejection reasons attached while the bank is short. Finally a single
    deterministic pass classifies, deduplicates and trims the survivors.

    The corrective loop is bounded by QUESTION_VALIDATION_MAX_RETRIES and
    stops early once the quota is met, because each pass is a full set of LLM
    calls. A bank still short after the last pass is accepted as-is, the same
    way a thinking-order shortfall already is — a thin bank a learner can
    trust beats a full one it cannot.

    ``grounding_index`` is the topic's retrieval index, built once per run by
    the caller. Without one the gate is skipped entirely, which keeps direct
    service and test calls network-free.

    With ``append`` the run is a top-up: it asks only for the thinking
    orders the concept is short of, and keeps only what fills them. A concept
    already at its minimum gets no model call at all.

    on_event(event_type, message, data) receives trace events when provided.
    stats, when given a dict, accumulates run totals for a material summary.
    """
    from .grounding import correction_note, enabled

    orders = caps = None
    if append:
        orders, caps = top_up_plan(node)
        if not orders:
            logger.info(
                "[Questions] %s  already meets the minimum; nothing to add  (object %s)",
                name(concept_name(node)), node.id,
            )
            _emit(
                on_event, "node_already_met",
                f"{concept_name(node)} already meets the minimum",
                node_id=node.id,
            )
            return []

    gate_on = grounding_index is not None and enabled()
    max_retries = int(getattr(settings, "QUESTION_VALIDATION_MAX_RETRIES", 1))
    correction, drafted = "", 0

    for attempt in range(max_retries + 1):
        drafted += _draft_questions_for_node(
            node,
            classifier,
            on_event=on_event,
            generation_fingerprint=generation_fingerprint,
            correction=correction,
            orders=orders,
        )
        if not gate_on:
            break

        _emit(
            on_event, "grounding_started",
            f"Checking drafts against the lesson's source text (pass {attempt + 1})",
            node_id=node.id, attempt=attempt + 1,
        )
        _surviving, failures = _validate_drafts_for_node(
            node, grounding_index, on_event=on_event, stats=stats,
        )
        if not failures or not _bank_is_short(node, orders) or attempt == max_retries:
            break
        correction = correction_note(failures)
        _emit(
            on_event, "grounding_retry",
            f"Regenerating {len(failures)} rejected question(s) with correction feedback",
            node_id=node.id, attempt=attempt + 1, rejected=len(failures),
        )

    if stats is not None:
        stats["total_drafted"] = stats.get("total_drafted", 0) + drafted

    _emit(
        on_event, "node_classifying",
        f"Classifying and filtering {drafted} draft(s)",
        node_id=node.id, drafted=drafted,
    )
    return finalize_node_questions(
        node, classifier, on_event=on_event, stats=stats, append=append, caps=caps,
    )


def top_up_plan(node):
    """What a top-up of ``node``'s concept asks for and may keep.

    Returns ``(orders, caps)``: the thinking orders below the minimum, and per
    order how many more fit under the bank's target. Counted from every
    question that fills the minimum -- generated, the teacher's own, and
    printed ones the teacher edited and confirmed.
    """
    from lessons.services.question_workflow import concept_tier_counts

    have = concept_tier_counts(node)
    minimum = question_minimum()
    orders = [order for order in QUESTION_DISTRIBUTION if have[order] < minimum[order]]
    caps = {
        order: max(0, max(config["count"], minimum[order]) - have[order])
        for order, config in QUESTION_DISTRIBUTION.items()
    }
    return orders, caps


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
    append=False,
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
        # whole Standard bundle and saved against that bundle's lead.
        logger.info(
            "[Questions] skipping %s learning object(s) taught through another object's "
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
    logger.info(
        "[Questions] PDF %s  started: %s concepts to write questions for, %s unchanged and reused",
        material.id, len(nodes_to_generate), len(nodes) - len(nodes_to_generate),
    )
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
        "ungrounded": 0,
        "unverified": 0,
        "reused_nodes": len(nodes) - len(nodes_to_generate),
    }
    classifier = None
    grounding_index = None
    if nodes_to_generate:
        # One index per run, shared by every concept: they all search the same
        # topic corpus, and embedding the PDFs once per node would dominate the
        # run. Built before the first LLM call so an unusable index is reported
        # up front rather than discovered after a node's worth of generation.
        from .grounding import build_index, enabled as grounding_enabled

        if grounding_enabled() and material.outline_node_id:
            grounding_index = build_index(material.outline_node)
            _emit(
                on_event, "grounding_index_built",
                f"Indexed {len(grounding_index.chunks)} source passage(s) for validation",
                material_id=material.id,
                chunks=len(grounding_index.chunks),
                searchable=grounding_index.searchable,
                reason=grounding_index.reason,
            )
        # Production background runs always provide the trace callback. Keep
        # direct service/test calls network-free unless they actually generate.
        if on_event:
            _emit(
                on_event,
                "model_warming",
                "Question model is ready" if settings.LLM_PROVIDER == "groq"
                else "Loading question model into Ollama",
            )

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
                f"Reused unchanged question bank: {concept_name(node)}",
                node_id=node.id,
                title=concept_name(node),
                count=len(cached),
                index=position,
                total=total_nodes,
            )
            continue
        logger.info("[Questions] (%s/%s) %s  writing questions", position, total_nodes, name(concept_name(node)))
        # index/total are what let the teacher's dialog draw a real progress
        # bar. Without them it can only spin, and a spinner cannot tell slow
        # apart from stuck -- which is the whole complaint about this step.
        _emit(
            on_event, "node_started", f"Generating questions for: {concept_name(node)}",
            node_id=node.id, title=concept_name(node),
            index=position, total=total_nodes,
        )
        questions = generate_questions_for_node(
            node,
            classifier,
            on_event=on_event,
            stats=stats,
            generation_fingerprint=fingerprints[node.id],
            grounding_index=grounding_index,
            append=append,
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
        ungrounded=stats["ungrounded"],
        unverified=stats["unverified"],
        by_thinking_order=dict(Counter(q.thinking_order for q in all_questions)),
        by_bloom=dict(Counter(q.bloom_level for q in all_questions)),
    )
    return all_questions


def _prepare_generation(outline_node, on_event):
    """The topic's grounding index, a warm question model and the classifier.

    Built once per run and shared by every concept in it.
    """
    from .grounding import build_index, enabled as grounding_enabled

    grounding_index = None
    if grounding_enabled():
        grounding_index = build_index(outline_node)
        _emit(
            on_event, "grounding_index_built",
            f"Indexed {len(grounding_index.chunks)} source passage(s) for validation",
            chunks=len(grounding_index.chunks),
            searchable=grounding_index.searchable,
            reason=grounding_index.reason,
        )
    if on_event:
        _emit(
            on_event, "model_warming",
            "Question model is ready" if settings.LLM_PROVIDER == "groq"
            else "Loading question model into Ollama",
        )
        warm_question_model(
            on_metrics=lambda metrics: _emit(on_event, "model_warmed", "Question model is ready", **metrics),
        )
    if _classifier_cache is None:
        _emit(on_event, "classifier_loading", "Loading Bloom's classifier model")
    return grounding_index, _get_classifier()


def generate_questions_for_topic(outline_node, nodes, on_event=None, rounds=None):
    """Fill every concept of a topic to its minimum, in rounds.

    ``nodes`` are the concepts' Standard leads, the objects their banks are
    filed under. Each round tops up only the concepts still short, and only
    in the thinking orders they lack; a concept that fails is reported and
    left out of later rounds without stopping the others.

    Returns ``{"added", "still_short", "failed"}``: how many questions were
    kept, and the concepts left short or failed, by name.
    """
    from lessons.services.question_workflow import concept_tier_counts

    rounds = rounds or int(getattr(settings, "QUESTION_GENERATION_ROUNDS", 3))
    minimum = question_minimum()
    goal = " + ".join(f"{minimum[order]} {order}" for order in QUESTION_DISTRIBUTION)

    def is_short(node):
        have = concept_tier_counts(node)
        return any(have[order] < minimum[order] for order in QUESTION_DISTRIBUTION)

    targets = [node for node in nodes if is_short(node)]
    logger.info(
        "[Questions] topic %s  started: %s of %s concepts short of %s",
        outline_node.id, len(targets), len(nodes), goal,
    )
    _emit(
        on_event, "topic_started",
        f"{len(targets)} of {len(nodes)} concepts need questions",
        concept_count=len(nodes), short_count=len(targets), minimum=minimum,
    )
    stats = {"total_drafted": 0, "excluded_create": 0, "duplicates": 0, "trimmed": 0,
             "ungrounded": 0, "unverified": 0}
    added, failed = 0, {}
    if targets:
        grounding_index, classifier = _prepare_generation(outline_node, on_event)
    for round_number in range(1, rounds + 1):
        if not targets:
            break
        logger.info(
            "[Questions] round %s of %s: %s concept(s) short of %s",
            round_number, rounds, len(targets), goal,
        )
        _emit(
            on_event, "round_started",
            f"Round {round_number} of {rounds}: {len(targets)} concept(s) still short",
            round=round_number, rounds=rounds, total=len(targets),
        )
        for position, node in enumerate(targets, start=1):
            have = concept_tier_counts(node)
            wanted = [order for order in QUESTION_DISTRIBUTION if have[order] < minimum[order]]
            logger.info(
                "[Questions] round %s/%s (%s/%s) %s  writing %s questions (has %s)",
                round_number, rounds, position, len(targets), name(concept_name(node)),
                " and ".join(wanted),
                " / ".join(f"{have[order]} {order}" for order in QUESTION_DISTRIBUTION),
            )
            _emit(
                on_event, "node_started", f"Generating questions for: {concept_name(node)}",
                node_id=node.id, title=concept_name(node),
                index=position, total=len(targets), round=round_number, rounds=rounds,
            )
            fingerprint = question_bank_fingerprint(concept_source_text(node), QUESTION_DISTRIBUTION)
            try:
                kept = generate_questions_for_node(
                    node, classifier, on_event=on_event, stats=stats,
                    generation_fingerprint=fingerprint,
                    grounding_index=grounding_index, append=True,
                )
            except Exception as exc:  # noqa: BLE001 -- one concept must not stop the rest
                logger.exception("[Questions] %s  failed: %s", name(concept_name(node)), exc)
                failed[node.id] = f"{concept_name(node)}: {exc}"
                _emit(
                    on_event, "node_failed", f"{concept_name(node)}: {exc}",
                    node_id=node.id, round=round_number,
                )
                continue
            added += len(kept)
            _emit(
                on_event, "node_finished",
                f"Finished node: saved {len(kept)} questions",
                node_id=node.id, count=len(kept),
                index=position, total=len(targets), round=round_number, rounds=rounds,
                by_thinking_order=dict(Counter(q.thinking_order for q in kept)),
            )
        targets = [node for node in targets if node.id not in failed and is_short(node)]

    still_short = [concept_name(node) for node in targets]
    logger.info(
        "[Questions] topic %s  done: %s questions added from %s drafts; dropped %s not grounded "
        "in the lesson, %s duplicates, %s wrong level, %s extra%s%s",
        outline_node.id, added, stats["total_drafted"], stats["ungrounded"],
        stats["duplicates"], stats["excluded_create"], stats["trimmed"],
        f"; still short: {', '.join(still_short)}" if still_short else "",
        f"; failed: {len(failed)}" if failed else "",
    )
    _emit(
        on_event, "topic_finished",
        f"Added {added} questions",
        added=added, still_short=still_short, failed=list(failed.values()),
        drafted=stats["total_drafted"], ungrounded=stats["ungrounded"],
    )
    return {"added": added, "still_short": still_short, "failed": list(failed.values())}
