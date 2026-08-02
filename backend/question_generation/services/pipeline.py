from collections import Counter
from math import ceil

from django.db import transaction

from .bloom_classifier import BloomClassifier
from .question_generator import generate_questions

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


def generate_questions_for_node(node, classifier, on_event=None):
    """Generate a full set of classified questions for one LearningObject.

    Runs an overgenerated pass, then checks the CLASSIFIED difficulty counts
    against QUESTION_DISTRIBUTION. Levels that come up short get regenerated
    with a stricter prompt, up to MAX_REBALANCE_ROUNDS times. Questions that
    classify at a different level than intended still count toward that
    level's quota — the classifier's label is what matters. The result is
    trimmed to the target distribution.

    on_event(event_type, message, data) receives trace events when provided.
    """
    all_questions = []

    def _generate(difficulty, fmt, count, strict):
        questions = generate_questions(
            content=node.content,
            difficulty=difficulty,
            format_type=fmt,
            count=count,
            strict=strict,
        )
        for q in questions:
            tagged = _classify_and_tag(q, node, classifier, difficulty)
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

    # ── Pass 2: rebalance levels the classifier says are short ──
    for round_num in range(1, MAX_REBALANCE_ROUNDS + 1):
        shortfall = _difficulty_shortfall(all_questions)
        if not shortfall:
            break

        print(f"  Rebalance round {round_num}: short {shortfall}")
        _emit(
            on_event, "rebalance_round",
            f"Round {round_num}/{MAX_REBALANCE_ROUNDS}: regenerating with strict prompts",
            round=round_num, shortfall=shortfall,
        )
        for difficulty, needed in shortfall.items():
            formats = QUESTION_DISTRIBUTION[difficulty]["formats"]
            # one batched LLM call per short level, rotating format across rounds
            fmt = formats[(round_num - 1) % len(formats)]
            _generate(difficulty, fmt, needed, strict=True)

    remaining = _difficulty_shortfall(all_questions)
    if remaining:
        print(
            f"  WARNING: still short after {MAX_REBALANCE_ROUNDS} rebalance "
            f"rounds for node {node.id}: {remaining} — keeping what we have"
        )
        _emit(
            on_event, "shortfall_warning",
            f"Still short after {MAX_REBALANCE_ROUNDS} rebalance rounds — keeping what we have",
            node_id=node.id, remaining=remaining,
        )

    # only the curated final set is stored — rebalance surplus is dropped
    final = _select_final_questions(all_questions)
    dropped = len(all_questions) - len(final)
    if dropped:
        _emit(
            on_event, "node_trimmed",
            f"Kept {len(final)} of {len(all_questions)} questions "
            f"(dropped {dropped} surplus from rebalancing)",
            node_id=node.id, kept=len(final), dropped=dropped,
        )
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
    for node in nodes:
        print(f"Generating questions for: {node.title}")
        _emit(
            on_event, "node_started", f"Generating questions for: {node.title}",
            node_id=node.id, title=node.title,
        )
        questions = generate_questions_for_node(node, classifier, on_event=on_event)
        created = save_node_questions(node, questions)
        all_questions.extend(questions)
        print(f"  Generated {len(questions)} questions")
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

    print(f"Total questions generated: {len(all_questions)}")
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

    def _corrected_for_storage(question):
        """Persist the classifier's difficulty as the final accepted label."""
        corrected = dict(question)
        original_intended = corrected.get("intended_difficulty")
        classified = corrected.get("difficulty")
        if original_intended != classified or corrected.get("difficulty_match") is not True:
            print(
                "Corrected question difficulty before save: "
                f"intended={original_intended} -> stored={classified}; "
                f"question={corrected.get('question', '')[:120]}"
            )
        corrected["intended_difficulty"] = corrected["difficulty"]
        corrected["difficulty_match"] = True
        return corrected

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
        for q in (_corrected_for_storage(question) for question in questions)
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
