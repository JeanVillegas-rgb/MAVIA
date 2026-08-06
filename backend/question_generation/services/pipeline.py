import sys
from collections import Counter
from math import ceil

from django.db import transaction

from .bloom_classifier import BloomClassifier
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
# How many questions per difficulty per content node (LearningObject)
QUESTION_DISTRIBUTION = {
    "easy":   {"count": 5, "formats": ["MCQ", "TF"]},
    "medium": {"count": 5, "formats": ["MCQ", "TF"]},
    "hard":   {"count": 3, "formats": ["MCQ"]},
}
# Per node: 5 easy + 5 medium + 3 hard = 13 questions

# Pass 1 asks for more than the target so that classification drift still
# leaves enough in each bucket — overgenerate-and-select beats retrying.
OVERGENERATION_FACTOR = 1.5

# How many rebalance rounds to run when a difficulty level comes up short.
# A cap, not a guarantee — after this we keep what we have rather than
# block the whole material on one stubborn node.
MAX_REBALANCE_ROUNDS = 3

# Bloom levels the classifier may return but that MCQ/TF cannot assess.
# "create" questions (design/construct/compose a novel artifact) don't have
# a single gradeable answer, so they're dropped right after classification
# rather than being force-fit into the "hard" bucket.
UNASSESSABLE_BLOOM_LEVELS = {"create"}

# Display-only: what each level's strict rebalance prompt asks for, shown
# in the terminal trace so it's clear why a regenerated question looks the
# way it does. Purely descriptive — has no effect on the actual prompt.
_STRICT_PROMPT_HINTS = {
    "easy": "direct recall only",
    "medium": "predict outcome only",
    "hard": "evaluate/judge/justify only",
}

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


def _classify_and_tag(q, node, classifier, intended_difficulty):
    """Attach the node and the classifier's authoritative labels."""
    classification = classifier.classify(q["question"])

    q["node"] = node

    # intended = what we asked the LLM for
    q["intended_difficulty"] = intended_difficulty

    # classified = what the classifier says (THIS is authoritative)
    q["bloom_level"] = classification["bloom_level"]
    q["difficulty"] = classification["difficulty"]
    q["category"] = classification["category"]

    # flag mismatches (useful for thesis analysis)
    q["difficulty_match"] = q["intended_difficulty"] == q["difficulty"]
    return q


def _difficulty_shortfall(questions):
    """Compare classified difficulty counts against the target distribution."""
    counts = Counter(q["difficulty"] for q in questions)
    return {
        difficulty: config["count"] - counts.get(difficulty, 0)
        for difficulty, config in QUESTION_DISTRIBUTION.items()
        if counts.get(difficulty, 0) < config["count"]
    }


def _print_question_block(tagged, fmt, filtered):
    """Detailed per-question trace: what was asked for vs. what the
    classifier says, and what that means for whether it's kept."""
    bloom = tagged["bloom_level"]
    intended = tagged["intended_difficulty"]
    classified_label = "excluded" if filtered else tagged["difficulty"]
    print(f'  Q: "{tagged["question"]}"')
    print(f"  Format: {fmt} | Intended: {intended} | Classified: {bloom} → {classified_label}")
    if filtered:
        print(f"  ⊘ Filtered — {bloom}-level cannot be assessed via MCQ/TF")
    elif tagged["difficulty_match"]:
        print(f"  ✓ Match — keeping as {tagged['difficulty']}")
    else:
        print(f"  ✗ Mismatch — keeping as {tagged['difficulty']} (classifier is authoritative)")
    print()


def _print_pass1_distribution(all_questions):
    """Classified-distribution snapshot right after the overgenerated pass,
    before any rebalancing has happened."""
    counts = Counter(q["difficulty"] for q in all_questions)
    shortfall = _difficulty_shortfall(all_questions)
    print("Pass 1 complete — classified distribution:")
    for difficulty, config in QUESTION_DISTRIBUTION.items():
        needed = config["count"]
        have = counts.get(difficulty, 0)
        label = f"{difficulty}:"
        if difficulty in shortfall:
            status = f"SHORT (need {shortfall[difficulty]} more)"
        elif have > needed:
            status = f"OK ({have - needed} surplus, will trim)"
        else:
            status = "OK"
        print(f"  {label:<8}{have} / {needed} needed  → {status}")
    if shortfall:
        parts = ", ".join(
            f"{d} is short by {n} question{'s' if n != 1 else ''}"
            for d, n in shortfall.items()
        )
        print(f"\nRebalancing: {parts}")
    print()


def _print_final_distribution(all_questions, final, remaining):
    """Classified-distribution snapshot after rebalancing (and trimming),
    whether it fully closed the gap or the round cap was hit first."""
    pretrim_counts = Counter(q["difficulty"] for q in all_questions)
    final_counts = Counter(q["difficulty"] for q in final)
    exhausted = bool(remaining)
    heading = (
        f"Rebalance exhausted ({MAX_REBALANCE_ROUNDS}/{MAX_REBALANCE_ROUNDS} rounds) "
        "— accepting shortfall:"
        if exhausted else
        "Rebalance complete — final distribution:"
    )
    print(heading)
    for difficulty, config in QUESTION_DISTRIBUTION.items():
        needed = config["count"]
        have = final_counts.get(difficulty, 0)
        pretrim_have = pretrim_counts.get(difficulty, 0)
        label = f"{difficulty}:"
        if difficulty in remaining:
            status = "SHORT (could not generate enough)"
        elif pretrim_have > needed:
            status = f"OK (trimmed from {pretrim_have})"
        else:
            status = "OK"
        print(f"  {label:<8}{have} / {needed} needed  → {status}")
    print()


def _print_node_summary(node, final, match_count, total_generated, filtered_create_count):
    final_counts = Counter(q["difficulty"] for q in final)
    breakdown = ", ".join(f"{final_counts.get(d, 0)} {d}" for d in ("easy", "medium", "hard"))
    match_rate = (match_count / total_generated) if total_generated else 0
    divider = "─" * 40
    print(divider)
    print(f'Node: "{node.title}"')
    print(f"Questions saved: {len(final)} ({breakdown})")
    print(f"Match rate: {match_count}/{total_generated} generated ({match_rate:.1%})")
    print(f"Create-level filtered: {filtered_create_count}")
    print(divider)
    print()


def _print_material_summary(material, node_count, all_questions, stats):
    diff_dist = Counter(q["difficulty"] for q in all_questions)
    overall_generated = stats["total_generated"]
    overall_matched = stats["total_matched"]
    overall_rate = (overall_matched / overall_generated) if overall_generated else 0
    distribution = ", ".join(f"{diff_dist.get(d, 0)} {d}" for d in ("easy", "medium", "hard"))
    divider = "═" * 40
    print(divider)
    print(f'Pipeline complete: "{material.title}"')
    print(f"Nodes processed: {node_count}")
    print(f"Total questions saved: {len(all_questions)}")
    print(f"Overall match rate: {overall_matched}/{overall_generated} ({overall_rate:.1%})")
    print(f"Distribution: {distribution}")
    print(f"Create-level filtered: {stats['filtered_create']}")
    print(divider)
    print()


def _select_final_questions(questions):
    """Trim to the target distribution: up to `count` per classified difficulty.

    Rebalancing overshoots (a strict retry that drifts still lands somewhere),
    so only the final curated set is kept — questions whose intended difficulty
    matched their classification are preferred.
    """
    final = []
    for difficulty, config in QUESTION_DISTRIBUTION.items():
        bucket = [q for q in questions if q["difficulty"] == difficulty]
        bucket.sort(key=lambda q: not q["difficulty_match"])  # stable: matches first
        final.extend(bucket[:config["count"]])
    return final


def generate_questions_for_node(node, classifier, on_event=None, stats=None):
    """Generate a full set of classified questions for one LearningObject.

    Runs an overgenerated pass, then checks the CLASSIFIED difficulty counts
    against QUESTION_DISTRIBUTION. Levels that come up short get regenerated
    with a stricter prompt, up to MAX_REBALANCE_ROUNDS times. Questions that
    classify at a different level than intended still count toward that
    level's quota — the classifier's label is what matters. Questions the
    classifier puts at an unassessable level (see UNASSESSABLE_BLOOM_LEVELS)
    are dropped before they can count toward any quota. The result is
    trimmed to the target distribution.

    on_event(event_type, message, data) receives trace events when provided.
    stats, when given a dict, gets running totals added to it (generated,
    matched, create-filtered) for a material-level summary.
    """
    all_questions = []
    filtered_create_count = 0

    def _generate(difficulty, fmt, count, strict):
        nonlocal filtered_create_count
        questions = generate_questions(
            content=node.content,
            difficulty=difficulty,
            format_type=fmt,
            count=count,
            strict=strict,
        )
        for q in questions:
            tagged = _classify_and_tag(q, node, classifier, difficulty)
            filtered = tagged["bloom_level"] in UNASSESSABLE_BLOOM_LEVELS
            _print_question_block(tagged, fmt, filtered)
            if filtered:
                filtered_create_count += 1
                _emit(
                    on_event, "question_dropped",
                    tagged["question"],
                    reason=f"{tagged['bloom_level']}-level question cannot be assessed by MCQ/TF",
                    intended=difficulty,
                    bloom_level=tagged["bloom_level"],
                    format=fmt,
                    strict=strict,
                )
                continue
            all_questions.append(tagged)
            _emit(
                on_event, "question_generated",
                tagged["question"],
                intended=difficulty,
                classified=tagged["difficulty"],
                bloom_level=tagged["bloom_level"],
                format=fmt,
                match=tagged["difficulty_match"],
                strict=strict,
            )

    # ── Pass 1: overgenerate per the configured distribution ──
    for difficulty, config in QUESTION_DISTRIBUTION.items():
        count = config["count"]
        formats = config["formats"]

        # distribute the padded count across formats
        per_format = max(1, ceil(count * OVERGENERATION_FACTOR / len(formats)))

        for fmt in formats:
            _generate(difficulty, fmt, per_format, strict=False)

    _print_pass1_distribution(all_questions)

    # ── Pass 2: rebalance levels the classifier says are short ──
    for round_num in range(1, MAX_REBALANCE_ROUNDS + 1):
        shortfall = _difficulty_shortfall(all_questions)
        if not shortfall:
            break

        _emit(
            on_event, "rebalance_round",
            f"Round {round_num}/{MAX_REBALANCE_ROUNDS}: regenerating with strict prompts",
            round=round_num, shortfall=shortfall,
        )
        for difficulty, needed in shortfall.items():
            formats = QUESTION_DISTRIBUTION[difficulty]["formats"]
            # one batched LLM call per short level, rotating format across rounds
            fmt = formats[(round_num - 1) % len(formats)]
            print(f"Rebalance round {round_num}/{MAX_REBALANCE_ROUNDS} — targeting: {difficulty}")
            print(f"Using strict prompt ({_STRICT_PROMPT_HINTS.get(difficulty, 'stricter constraints')})")
            _generate(difficulty, fmt, needed, strict=True)
        print()

    remaining = _difficulty_shortfall(all_questions)
    if remaining:
        _emit(
            on_event, "shortfall_warning",
            f"Still short after {MAX_REBALANCE_ROUNDS} rebalance rounds — keeping what we have",
            node_id=node.id, remaining=remaining,
        )

    # only the curated final set is stored — rebalance surplus is dropped
    final = _select_final_questions(all_questions)
    _print_final_distribution(all_questions, final, remaining)

    dropped = len(all_questions) - len(final)
    if dropped:
        _emit(
            on_event, "node_trimmed",
            f"Kept {len(final)} of {len(all_questions)} questions "
            f"(dropped {dropped} surplus from rebalancing)",
            node_id=node.id, kept=len(final), dropped=dropped,
        )

    match_count = sum(1 for q in all_questions if q["difficulty_match"])
    _print_node_summary(node, final, match_count, len(all_questions), filtered_create_count)

    if stats is not None:
        stats["total_generated"] = stats.get("total_generated", 0) + len(all_questions)
        stats["total_matched"] = stats.get("total_matched", 0) + match_count
        stats["filtered_create"] = stats.get("filtered_create", 0) + filtered_create_count

    return final


def generate_questions_for_material(material, on_event=None, node_ids=None):
    """Full pipeline: LearningMaterial → classified questions for its text
    learning objects, straight from the database (no JSON handoff).

    With node_ids, only those learning objects are (re)generated; other
    nodes' stored questions are left untouched. Each node's curated set is
    saved to the DB as soon as the node finishes, so an interrupted run
    keeps every completed node's questions."""
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
    stats = {"total_generated": 0, "total_matched": 0, "filtered_create": 0}
    for node in nodes:
        print(f"Generating questions for: {node.title}")
        _emit(
            on_event, "node_started", f"Generating questions for: {node.title}",
            node_id=node.id, title=node.title,
        )
        questions = generate_questions_for_node(node, classifier, on_event=on_event, stats=stats)
        created = save_node_questions(node, questions)
        all_questions.extend(questions)
        _emit(
            on_event, "node_finished",
            f"Finished node: saved {len(created)} questions",
            node_id=node.id,
            count=len(questions),
            saved=len(created),
            by_difficulty=dict(Counter(q["difficulty"] for q in questions)),
        )

    match_count = sum(1 for q in all_questions if q["difficulty_match"])
    match_rate = match_count / len(all_questions) if all_questions else 0
    diff_dist = Counter(q["difficulty"] for q in all_questions)
    bloom_dist = Counter(q["bloom_level"] for q in all_questions)

    _print_material_summary(material, len(nodes), all_questions, stats)
    _emit(
        on_event, "material_finished",
        f"Generated {len(all_questions)} questions, "
        f"difficulty match rate {match_rate:.0%}",
        total=len(all_questions),
        match_rate=round(match_rate, 3),
        by_difficulty=dict(diff_dist),
        by_bloom=dict(bloom_dist),
    )
    return all_questions


def save_node_questions(node, questions):
    """Atomically replace one node's stored questions with its latest final
    set, so the DB always holds exactly one run's output per node.

    NOTE: deleting a question cascades to its LearnerResponse rows —
    regenerating resets learner history for that node's questions.
    """
    from question_generation.models import GeneratedQuestion

    def _log_difficulty_mismatch(question):
        """Note when the LLM's intended level and the classifier disagree.

        Serving already uses `difficulty` (the classifier's label), so this
        is purely a thesis audit log. `intended_difficulty` and
        `difficulty_match` are stored as computed in _classify_and_tag() —
        they must NOT be overwritten here, or the mismatch-rate data they
        exist to capture would be lost.
        """
        if question.get("intended_difficulty") != question.get("difficulty"):
            print(
                "Difficulty mismatch at save: "
                f"intended={question.get('intended_difficulty')} -> "
                f"classified={question.get('difficulty')}; "
                f"question={question.get('question', '')[:120]}"
            )
        return question

    db_objects = [
        GeneratedQuestion(
            node=q["node"],
            question_text=q["question"],
            question_format=q["format"],
            choices=q.get("choices"),
            correct_answer=q["correct_answer"],
            explanation=q.get("explanation", ""),
            bloom_level=q["bloom_level"],
            difficulty=q["difficulty"],
            category=q["category"],
            intended_difficulty=q["intended_difficulty"],
            difficulty_match=q["difficulty_match"],
        )
        for q in (_log_difficulty_mismatch(question) for question in questions)
    ]

    with transaction.atomic():
        deleted, _ = GeneratedQuestion.objects.filter(node=node).delete()
        created = GeneratedQuestion.objects.bulk_create(db_objects)
        material = node.material
        generated_json = material.generated_json or {}
        if generated_json:
            generated_json["question_audio_generated"] = False
            generated_json["audio_playlist_generated"] = False
            material.generated_json = generated_json
            material.save(update_fields=["generated_json"])
    if deleted:
        print(f"Replaced {deleted} existing rows for node {node.id}")
    print(f"Saved {len(created)} questions for node {node.id}")
    return created
