import re
import sys
from collections import Counter
from math import ceil

from django.db import transaction

from .bloom_classifier import (
    BLOOM_TO_DIFFICULTY,
    BLOOM_TO_THINKING_ORDER,
    BloomClassifier,
)
from .question_generator import GENERATION_LEVELS, LEVEL_FORMATS, generate_questions

# Windows consoles often default to a legacy codepage (e.g. cp1252) that
# can't encode the ✓/✗/⊘/→/─/═ trace symbols below, which would otherwise
# crash a run on the first print(). Force UTF-8 output so the trace is
# reliable regardless of the terminal's codepage.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# ── Configuration ──
# Per content node (LearningObject): a pool of 3 lower-order and 3 higher-order
# questions. LOT and HOT are cognitive categories, not difficulty tiers — the
# counts are equal because neither is "the harder half" of the bank.
#
# Three per band is what the checkpoint rule needs: a learner answers one LOT
# and one HOT to advance, and on a retry must be given a question they have not
# just seen, so each band needs alternates to draw from.
QUESTION_DISTRIBUTION = {
    "LOT": {"count": 3},
    "HOT": {"count": 3},
}

# Which Bloom levels feed each band. Generation aims at every level here, one
# prompt each; acceptance happens at the band level (see finalize), so a level
# the classifier under-fills is covered by its band partners rather than
# leaving the pool short.
LEVELS_BY_BAND = {
    band: tuple(
        level for level in GENERATION_LEVELS
        if BLOOM_TO_THINKING_ORDER.get(level) == band
    )
    for band in QUESTION_DISTRIBUTION
}

# Ask for more than the target in the one generation pass, so classification
# drift still leaves enough in each band. This is what replaced the old
# multi-round rebalancing: overgenerating inside the SAME set of LLM calls is
# far cheaper than issuing extra calls to correct a shortfall afterwards.
OVERGENERATION_FACTOR = 1.5

# Bloom levels the classifier may return but that MCQ/TF cannot assess.
# "create" questions (design/construct/compose a novel artifact) have no
# single gradeable answer, so they are dropped during post-processing.
# The trained model still predicts "create" — the exclusion is ours, applied
# at the application level, and the model is deliberately left alone. No prompt
# aims at it either, so a create-level row only ever arrives by classifier drift.
UNASSESSABLE_BLOOM_LEVELS = {"create"}

# loaded once per process — reloading RoBERTa on every run costs ~10s
_classifier_cache = None


def get_classifier():
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
    counts = Counter(q.thinking_order for q in kept)
    breakdown = ", ".join(f"{counts.get(order, 0)} {order}" for order in QUESTION_DISTRIBUTION)
    divider = "─" * 40
    print(divider)
    print(f'Node: "{node.title}"')
    print(f"Questions saved: {len(kept)} ({breakdown})")
    print(f"Drafts discarded: {len(rejected)}")
    levels = Counter(q.bloom_level for q in kept)
    for order, config in QUESTION_DISTRIBUTION.items():
        have = counts.get(order, 0)
        status = "OK" if have >= config["count"] else "SHORT → node withheld from learners"
        covered = ", ".join(
            f"{level} x{levels[level]}"
            for level in LEVELS_BY_BAND[order] if levels.get(level)
        )
        print(f"  {order + ':':<6}{have} / {config['count']} needed  → {status}")
        if covered:
            print(f"         levels: {covered}")
    print(divider)
    print()


def node_question_status(node):
    """Report whether one node's question pools are complete.

    Completeness is defined at the BAND level, not per Bloom level: the
    classifier decides what each generated question actually is, so demanding a
    specific level in a specific slot would leave nodes permanently unfillable
    without a regeneration loop. A band is what the checkpoint rule consumes,
    so a band is what has to be full.
    """
    from question_generation.models import GeneratedQuestion

    counts = Counter(
        GeneratedQuestion.objects.filter(node=node, status="final")
        .values_list("thinking_order", flat=True)
    )
    bands = {
        band: {
            "have": counts.get(band, 0),
            "needed": config["count"],
            "short": max(0, config["count"] - counts.get(band, 0)),
        }
        for band, config in QUESTION_DISTRIBUTION.items()
    }
    return {
        "node_id": node.id,
        "bands": bands,
        "is_complete": all(band["short"] == 0 for band in bands.values()),
    }


def is_node_complete(node):
    """True when this node may be delivered to a learner."""
    return node_question_status(node)["is_complete"]


def complete_node_ids(nodes):
    """Filter an iterable of nodes down to the ids safe to deliver.

    Read paths that serve learners should go through this. A node whose pools
    are short would break the checkpoint rule — there would be no alternate
    question to offer after a wrong answer.
    """
    return {node.id for node in nodes if is_node_complete(node)}


def _print_material_summary(material, node_count, all_questions, stats):
    counts = Counter(q.thinking_order for q in all_questions)
    distribution = ", ".join(f"{counts.get(order, 0)} {order}" for order in QUESTION_DISTRIBUTION)
    divider = "═" * 40
    print(divider)
    print(f'Pipeline complete: "{material.title}"')
    print(f"Nodes processed: {node_count}")
    print(f"Total questions saved: {len(all_questions)}")
    print(f"Distribution: {distribution}")
    print(f"Drafts generated: {stats['total_drafted']}")
    print(f"Create-level excluded: {stats['excluded_create']}")
    print(f"Duplicates removed: {stats['duplicates']}")
    print(f"Surplus trimmed: {stats['trimmed']}")
    print(divider)
    print()


# ── Phase 1: generation (LLM) ──

def _draft_questions_for_node(node, on_event=None):
    """Run every LLM call for one node and persist the results as drafts.

    Nothing is classified, deduplicated or trimmed here — the questions go
    to the database exactly as the LLM produced them (after structural
    validation), so a crash later in the run cannot lose generated work.
    """
    from question_generation.models import GeneratedQuestion

    # clear drafts orphaned by an earlier crashed run so they can't be
    # mistaken for this run's output
    GeneratedQuestion.objects.filter(node=node, status="draft").delete()

    drafted = 0
    for band, levels in LEVELS_BY_BAND.items():
        target = QUESTION_DISTRIBUTION[band]["count"]
        # Spread the padded band target across the levels that feed it, so each
        # level gets its own prompt and no level is silently never asked for.
        per_level = max(1, ceil(target * OVERGENERATION_FACTOR / len(levels)))

        for level in levels:
            for fmt in LEVEL_FORMATS[level]:
                print(f"Generating {per_level} {level} ({band}) {fmt} question(s) for: {node.title}")
                questions = generate_questions(
                    content=node.content,
                    bloom_level=level,
                    format_type=fmt,
                    count=per_level,
                )
                batch = [
                    GeneratedQuestion(
                        node=node,
                        question_text=q["question"],
                        question_format=q["format"],
                        choices=q.get("choices"),
                        correct_answer=q["correct_answer"],
                        explanation=q.get("explanation", ""),
                        status="draft",
                    )
                    for q in questions
                ]
                GeneratedQuestion.objects.bulk_create(batch)
                drafted += len(batch)
                for q in batch:
                    print(f'  Q: "{q.question_text}" [{level}/{fmt}, drafted]')
                _emit(
                    on_event, "questions_drafted",
                    f"Saved {len(batch)} {level} {fmt} draft(s)",
                    node_id=node.id, count=len(batch),
                    requested=per_level, bloom_level=level,
                    thinking_order=band, format=fmt,
                )
    print()
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

    reject_ids = []
    seen = set()
    duplicates = excluded_create = trimmed = 0

    # ── Pass 1: deduplicate, then classify every survivor ──
    candidates = []
    for draft in drafts:
        key = _dedup_key(draft.question_text)
        if key in seen:
            duplicates += 1
            reject_ids.append(draft.id)
            print(f'  ⊘ Duplicate — "{draft.question_text}"')
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
            print(f'  ⊘ Excluded ({bloom_level}) — "{draft.question_text}"')
            _emit(
                on_event, "question_dropped", draft.question_text,
                reason=f"{bloom_level}-level question cannot be assessed by MCQ/TF",
                bloom_level=bloom_level, node_id=node.id,
            )
            continue

        candidates.append((draft, classification))

    # ── Pass 2: fill each band, preferring level coverage ──
    # The classifier is authoritative, so a band is filled with whatever it
    # actually produced rather than with fixed per-level slots. Taking one
    # question per distinct level first means a band of 3 spreads across the
    # levels available before it doubles up on any one of them.
    keep = []
    counts = Counter()
    for band, config in QUESTION_DISTRIBUTION.items():
        in_band = [c for c in candidates if c[1]["thinking_order"] == band]

        chosen, covered = [], set()
        for draft, classification in in_band:
            level = classification["bloom_level"]
            if level not in covered and len(chosen) < config["count"]:
                covered.add(level)
                chosen.append((draft, classification))
        for draft, classification in in_band:
            if len(chosen) >= config["count"]:
                break
            if not any(draft.id == d.id for d, _ in chosen):
                chosen.append((draft, classification))

        chosen_ids = {d.id for d, _ in chosen}
        for draft, _ in in_band:
            if draft.id not in chosen_ids:
                trimmed += 1
                reject_ids.append(draft.id)
                print(f'  ⊘ Surplus {band} — "{draft.question_text}"')

        for draft, classification in chosen:
            bloom_level = classification["bloom_level"]
            draft.bloom_level = bloom_level
            draft.thinking_order = band
            # A different axis from thinking_order, kept for the adaptive
            # engine's difficulty-based remediation — see GeneratedQuestion.
            draft.difficulty = (
                classification.get("difficulty")
                or BLOOM_TO_DIFFICULTY.get(bloom_level, "")
            )
            draft.category = classification["category"]
            draft.status = "final"
            counts[band] += 1
            keep.append(draft)
            print(f'  ✓ {band} ({bloom_level}) — "{draft.question_text}"')

    print()

    # A band that came up short is NOT accepted as-is. The questions are still
    # saved so the teacher can see what did generate and author the missing
    # ones, but the node is incomplete and is withheld from learners until its
    # pools are full — see is_node_complete(). Silently shipping a short pool
    # would break the checkpoint rule, which needs one LOT and one HOT to
    # advance plus alternates to draw from on a retry.
    for band, config in QUESTION_DISTRIBUTION.items():
        short = config["count"] - counts.get(band, 0)
        if short > 0:
            _emit(
                on_event, "incomplete_node",
                f"{band} pool is {short} question(s) short — this node is withheld "
                f"from learners until a teacher adds them",
                node_id=node.id, thinking_order=band, short=short,
            )

    with transaction.atomic():
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

    if replaced:
        print(f"Replaced {replaced} existing rows for node {node.id}")
    print(f"Saved {len(keep)} questions for node {node.id}")

    if stats is not None:
        stats["excluded_create"] = stats.get("excluded_create", 0) + excluded_create
        stats["duplicates"] = stats.get("duplicates", 0) + duplicates
        stats["trimmed"] = stats.get("trimmed", 0) + trimmed

    _print_node_summary(node, keep, reject_ids)
    return keep


def generate_questions_for_node(node, classifier, on_event=None, stats=None):
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
    drafted = _draft_questions_for_node(node, on_event=on_event)
    if stats is not None:
        stats["total_drafted"] = stats.get("total_drafted", 0) + drafted

    _emit(
        on_event, "node_classifying",
        f"Classifying and filtering {drafted} draft(s)",
        node_id=node.id, drafted=drafted,
    )
    return finalize_node_questions(node, classifier, on_event=on_event, stats=stats)


def generate_questions_for_material(material, on_event=None, node_ids=None):
    """Full pipeline: LearningMaterial → classified questions for its text
    learning objects, straight from the database (no JSON handoff).

    With node_ids, only those learning objects are (re)generated; other
    nodes' stored questions are left untouched. Each node is finalized as
    soon as it finishes, so an interrupted run keeps every completed node's
    questions."""
    from question_generation.models import GeneratedQuestion

    if _classifier_cache is None:
        _emit(on_event, "classifier_loading", "Loading Bloom's classifier model")
    classifier = get_classifier()

    nodes_qs = (
        material.learning_objects
        .filter(kind="text")
        .exclude(content="")
        .order_by("order")
    )
    if node_ids is not None:
        nodes_qs = nodes_qs.filter(id__in=node_ids)
    nodes = list(nodes_qs)
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
        node_count=len(nodes),
    )

    all_questions = []
    stats = {"total_drafted": 0, "excluded_create": 0, "duplicates": 0, "trimmed": 0}
    for node in nodes:
        print(f"Generating questions for: {node.title}")
        _emit(
            on_event, "node_started", f"Generating questions for: {node.title}",
            node_id=node.id, title=node.title,
        )
        questions = generate_questions_for_node(
            node, classifier, on_event=on_event, stats=stats)
        all_questions.extend(questions)
        _emit(
            on_event, "node_finished",
            f"Finished node: saved {len(questions)} questions",
            node_id=node.id,
            count=len(questions),
            by_thinking_order=dict(Counter(q.thinking_order for q in questions)),
        )

    _print_material_summary(material, len(nodes), all_questions, stats)
    _emit(
        on_event, "material_finished",
        f"Generated {len(all_questions)} questions from {stats['total_drafted']} drafts",
        total=len(all_questions),
        drafted=stats["total_drafted"],
        excluded_create=stats["excluded_create"],
        duplicates=stats["duplicates"],
        trimmed=stats["trimmed"],
        by_thinking_order=dict(Counter(q.thinking_order for q in all_questions)),
        by_bloom=dict(Counter(q.bloom_level for q in all_questions)),
    )
    return all_questions
