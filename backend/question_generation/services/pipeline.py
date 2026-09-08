import re
import sys
from collections import Counter
from math import ceil

from django.db import transaction

from .bloom_classifier import BLOOM_TO_DIFFICULTY, BloomClassifier
from .question_generator import generate_questions

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
# How many questions per thinking order per content node (LearningObject).
# LOT and HOT are cognitive categories, not difficulty tiers — the counts are
# equal because neither is "the harder half" of the bank.
QUESTION_DISTRIBUTION = {
    "LOT": {"count": 5, "formats": ["MCQ", "TF"]},
    "HOT": {"count": 5, "formats": ["MCQ"]},
}
# Per node: 5 LOT + 5 HOT = 10 questions

# Ask for more than the target in the one generation pass, so classification
# drift still leaves enough in each bucket. This is what replaced the old
# multi-round rebalancing: overgenerating inside the SAME set of LLM calls is
# far cheaper than issuing extra calls to correct a shortfall afterwards.
OVERGENERATION_FACTOR = 1.5

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
    counts = Counter(q.thinking_order for q in kept)
    breakdown = ", ".join(f"{counts.get(order, 0)} {order}" for order in QUESTION_DISTRIBUTION)
    divider = "─" * 40
    print(divider)
    print(f'Node: "{node.title}"')
    print(f"Questions saved: {len(kept)} ({breakdown})")
    print(f"Drafts discarded: {len(rejected)}")
    for order, config in QUESTION_DISTRIBUTION.items():
        have = counts.get(order, 0)
        status = "OK" if have >= config["count"] else "SHORT (accepted)"
        print(f"  {order + ':':<6}{have} / {config['count']} needed  → {status}")
    print(divider)
    print()


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
    for thinking_order, config in QUESTION_DISTRIBUTION.items():
        formats = config["formats"]
        # distribute the padded count across formats
        per_format = max(1, ceil(config["count"] * OVERGENERATION_FACTOR / len(formats)))

        for fmt in formats:
            print(f"Generating {per_format} {thinking_order} {fmt} question(s) for: {node.title}")
            questions = generate_questions(
                content=node.content,
                thinking_order=thinking_order,
                format_type=fmt,
                count=per_format,
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
                print(f'  Q: "{q.question_text}" [{fmt}, drafted]')
            _emit(
                on_event, "questions_drafted",
                f"Saved {len(batch)} {thinking_order} {fmt} draft(s)",
                node_id=node.id, count=len(batch),
                requested=per_format, thinking_order=thinking_order, format=fmt,
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

        if counts[thinking_order] >= QUESTION_DISTRIBUTION[thinking_order]["count"]:
            trimmed += 1
            reject_ids.append(draft.id)
            print(f'  ⊘ Surplus {thinking_order} — "{draft.question_text}"')
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
        print(f'  ✓ {thinking_order} ({bloom_level}) — "{draft.question_text}"')

    print()

    for thinking_order, config in QUESTION_DISTRIBUTION.items():
        short = config["count"] - counts.get(thinking_order, 0)
        if short > 0:
            _emit(
                on_event, "shortfall_warning",
                f"{thinking_order} came up {short} question(s) short — accepting as is",
                node_id=node.id, thinking_order=thinking_order, short=short,
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

    # Keep the teacher's Step 2 list and the learner-facing adaptive bank in
    # one visible workflow. The adaptive rows remain authoritative for play.
    from lessons.services.question_workflow import mirror_generated_questions
    mirror_generated_questions(node, keep)

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
    classifier = _get_classifier()

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
