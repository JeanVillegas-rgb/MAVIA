import logging
import sys
import textwrap
from collections import Counter

from django.conf import settings
from django.db import transaction
from django.db.models import Q

from config.console import name
from .bloom_classifier import BloomClassifier
from .question_generator import generate_questions, warm_question_model

logger = logging.getLogger(__name__)

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def thinking_order_counts():
    """Target questions per thinking order, read from settings at call time."""
    return {"LOT": settings.QUESTION_COUNT_LOT, "HOT": settings.QUESTION_COUNT_HOT}


def question_minimum():
    """LOT and HOT counts every concept needs before the Questions step is done."""
    return {"LOT": settings.QUESTION_MIN_LOT, "HOT": settings.QUESTION_MIN_HOT}


def _split(count, weights):
    """Divide count across formats by weight; the remainder goes to the first format."""
    total = sum(weights.values())
    split = {fmt: count * weight // total for fmt, weight in weights.items()}
    first = next(iter(weights))
    split[first] += count - sum(split.values())
    return {fmt: n for fmt, n in split.items() if n}


_COUNTS = thinking_order_counts()
_FORMAT_WEIGHTS = {"MCQ": settings.QUESTION_FORMAT_WEIGHT_MCQ, "TF": settings.QUESTION_FORMAT_WEIGHT_TF}
QUESTION_DISTRIBUTION = {
    "LOT": {
        "count": _COUNTS["LOT"],
        "format_split": _split(_COUNTS["LOT"], _FORMAT_WEIGHTS),
    },
    "HOT": {
        "count": _COUNTS["HOT"],
        "format_split": _split(_COUNTS["HOT"], _FORMAT_WEIGHTS),
    },
}

UNASSESSABLE_BLOOM_LEVELS = {"create"}

_classifier_cache = None


def _get_classifier():
    global _classifier_cache
    if _classifier_cache is None:
        _classifier_cache = BloomClassifier()
    return _classifier_cache


def concept_name(node):
    """The concept's name, for trace lines."""
    group = getattr(node, "group", None)
    return (group.label if group is not None and group.label else node.title) or "Untitled concept"


def _emit(on_event, event_type, message, **data):
    """Send a trace event to on_event, if one was given."""
    if on_event:
        on_event(event_type, message, data or None)


def _print_node_summary(node, kept, rejected):
    """One summary line per concept, judged against the minimum."""
    from .question_bank import tier_counts

    added = Counter(q.thinking_order for q in kept)
    breakdown = ", ".join(f"{added.get(order, 0)} {order}" for order in QUESTION_DISTRIBUTION)
    have = tier_counts(node)
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


def _object_text(item):
    """An object's text, with a definition the extractor split from its term joined back to the term."""
    content = (item.content or "").strip()
    title = (item.title or "").strip()
    if title and content[:1].islower():
        return f"{title} {content}"
    return content


def concept_source_text(node):
    """Every telling of the concept, Standard first: the text its questions are written from."""
    from course.version_assignment import bundle_roles, standard_material_id
    from lessons.services.concept_bundles import bundles_for_group, material_order

    if node.group_id is None:
        return _object_text(node)

    group = node.group
    bundles = {
        material_id: kept
        for material_id, objects in bundles_for_group(group).items()
        if (kept := [item for item in objects if (item.content or "").strip()])
    }
    standard_id = standard_material_id(group)
    if not any(item.id == node.id for item in bundles.get(standard_id) or []):
        return _object_text(node)

    roles = bundle_roles(group)
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
    return "\n".join(text for item in objects if (text := _object_text(item))) or _object_text(node)


def _is_concept_source(node):
    """Whether node is the one object of its concept that generates questions."""
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


def _draft_questions_for_node(
    node,
    classifier,
    on_event=None,
    correction="",
    orders=None,
):
    """Run the LLM calls for one concept and save the replies as drafts."""
    from question_generation.models import GeneratedQuestion

    resumed = list(
        GeneratedQuestion.objects.filter(node=node, status="draft").order_by("id")
    )

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
        targets = config["format_split"]
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
                outline_node=node.material.outline_node,
                source="generated",
                question_text=q["question"],
                question_format=q["format"],
                choices=q.get("choices"),
                correct_answer=q["correct_answer"],
                explanation=q.get("explanation", ""),
                thinking_order=thinking_order,
                status="draft",
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


def _log_check(result):
    """Log the checker's verdict under the draft's line."""
    if result["reason"]:
        logger.debug("      verdict: %s -- %s", result["verdict"], result["reason"])
    else:
        logger.debug("      verdict: %s", result["verdict"])


def _validate_drafts_for_node(node, on_event=None, stats=None):
    """Check every draft against the concept's own text and delete failures; returns (kept, failures)."""
    from question_generation.models import GeneratedQuestion
    from .grounding import verify

    drafts = list(
        GeneratedQuestion.objects.filter(node=node, status="draft").order_by("id")
    )
    if not drafts:
        return 0, []

    source = {"concept": concept_name(node), "text": concept_source_text(node)}
    logger.debug('  checked against "%s": "%s..."', source["concept"], source["text"][:200])
    failures, reject_ids, unverified = [], [], 0
    for draft in drafts:
        result = verify(draft, source)
        if result["stage"] in ("source_missing", "judge_unavailable"):
            unverified += 1
        if result["passed"]:
            draft.check_result = result["verdict"]
            draft.save(update_fields=["check_result"])
            logger.debug('  ✓ grounded — "%s"', draft.question_text)
            _log_check(result)
            continue
        failures.append((draft.question_text, result["reason"]))
        reject_ids.append(draft.id)
        logger.debug('  ⊘ ungrounded (%s) — "%s"', result["stage"], draft.question_text)
        _log_check(result)
        _emit(
            on_event, "question_ungrounded", draft.question_text,
            reason=result["reason"], stage=result["stage"],
            verdict=result["verdict"], node_id=node.id,
            source=result["source"],
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


def finalize_node_questions(node, classifier, on_event=None, stats=None, append=False, caps=None):
    """Deduplicate, classify, drop "create", trim to quota, then make the drafts final."""
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
    from lessons.services.question_workflow import question_fingerprint
    from .question_bank import topic_questions

    taken = topic_questions(node.material.outline_node)
    if not append:
        same_concept = Q(node__group_id=node.group_id) if node.group_id else Q(node=node)
        taken = taken.exclude(same_concept & Q(source="generated", teacher_edited=False))
    seen.update(question_fingerprint(text) for text in taken.values_list("question_text", flat=True))

    for draft in drafts:
        key = question_fingerprint(draft.question_text)
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
        draft.status = "review" if draft.check_result == "unverified" else "final"
        counts[thinking_order] += 1
        keep.append(draft)
        logger.debug(
            '  ✓ %s (%s) — "%s"', thinking_order, bloom_level, draft.question_text
        )

    for thinking_order, cap in ({} if append else caps).items():
        short = cap - counts.get(thinking_order, 0)
        if short > 0:
            _emit(
                on_event, "shortfall_warning",
                f"{thinking_order} came up {short} question(s) short — accepting as is",
                node_id=node.id, thinking_order=thinking_order, short=short,
            )

    with transaction.atomic():
        generated = GeneratedQuestion.objects.filter(source="generated")
        edited = Q(teacher_edited=True)
        if node.group_id:
            generated.filter(
                edited, node__group_id=node.group_id, status="final",
            ).exclude(node=node).update(node=node)
            obsolete = generated.filter(
                node__group_id=node.group_id,
            ).exclude(node=node).exclude(edited)
            obsolete.delete()

        replaced, _ = (
            (0, None) if append
            else generated.filter(node=node, status__in=("final", "review")).exclude(edited).delete()
        )
        if reject_ids:
            GeneratedQuestion.objects.filter(id__in=reject_ids).delete()
        from .bank_status import text_fingerprint
        source_stamp = text_fingerprint(concept_source_text(node))
        for question in keep:
            question.source_text_fingerprint = source_stamp
            question.content_fingerprint = question_fingerprint(question.question_text)
            question.paired_by = "auto"
        GeneratedQuestion.objects.bulk_update(
            keep,
            [
                "bloom_level", "thinking_order", "category", "status",
                "source_text_fingerprint", "content_fingerprint", "paired_by",
            ],
        )
        if keep:
            generated.filter(edited, node=node, status="final").update(
                source_text_fingerprint=source_stamp,
            )

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
    append=False,
    correction="",
):
    """One round for one concept: draft, check, classify and save; returns (kept, the correction for a next round, how many kept could not be checked)."""
    from question_generation.models import GeneratedQuestion
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
            return [], "", 0

    GeneratedQuestion.objects.filter(node=node, status="draft").delete()

    logger.info(
        "[Questions] CONCEPT: %s  (object %s)\n    REFERENCE CONTENT FOR THE QUESTIONS:\n%s",
        name(concept_name(node)), node.id, textwrap.indent(concept_source_text(node), "      "),
    )
    drafted = _draft_questions_for_node(
        node, classifier, on_event=on_event, correction=correction, orders=orders,
    )
    failures = []
    if enabled():
        _emit(
            on_event, "grounding_started",
            "Checking drafts against the lesson's source text",
            node_id=node.id,
        )
        _surviving, failures = _validate_drafts_for_node(node, on_event=on_event, stats=stats)

    if stats is not None:
        stats["total_drafted"] = stats.get("total_drafted", 0) + drafted

    _emit(
        on_event, "node_classifying",
        f"Classifying and filtering {drafted} draft(s)",
        node_id=node.id, drafted=drafted,
    )
    kept = finalize_node_questions(
        node, classifier, on_event=on_event, stats=stats, append=append, caps=caps,
    )
    unchecked = sum(question.status == "review" for question in kept)
    return kept, correction_note(failures), unchecked


def is_short(node):
    """True while the concept has fewer LOTS or HOTS questions than the minimum."""
    return bool(top_up_plan(node)[0])


def top_up_plan(node):
    """(orders, caps): the thinking orders a concept lacks and how many more fit."""
    from .question_bank import tier_counts

    have = tier_counts(node)
    minimum = question_minimum()
    orders = [order for order in QUESTION_DISTRIBUTION if have[order] < minimum[order]]
    caps = {
        order: max(0, max(config["count"], minimum[order]) - have[order])
        for order, config in QUESTION_DISTRIBUTION.items()
    }
    return orders, caps


def generate_questions_for_material(material, node_ids, on_event=None, *, append=False):
    """Generate questions for the given learning objects of one PDF."""
    candidates = list(
        material.learning_objects
        .exclude(content="")
        .filter(id__in=node_ids)
        .order_by("order")
    )
    nodes = [node for node in candidates if _is_concept_source(node)]
    if len(nodes) != len(candidates):
        logger.info(
            "[Questions] skipping %s learning object(s) taught through another object's "
            "concept bundle; their questions belong to that bundle's lead: %s",
            len(candidates) - len(nodes),
            ", ".join(
                f'"{node.title}" (#{node.id})'
                for node in candidates if node not in nodes
            ),
        )
    logger.info(
        "[Questions] PDF %s  started: %s concept(s) to write questions for",
        material.id, len(nodes),
    )
    _emit(
        on_event, "material_started",
        f"Generating questions for {len(nodes)} content nodes",
        material_id=material.id, material_title=material.title,
        node_count=len(nodes),
    )

    all_questions = []
    stats = {
        "total_drafted": 0,
        "excluded_create": 0,
        "duplicates": 0,
        "trimmed": 0,
        "ungrounded": 0,
        "unverified": 0,
    }
    classifier = None
    if nodes:
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
        logger.info("[Questions] (%s/%s) %s  writing questions", position, total_nodes, name(concept_name(node)))
        _emit(
            on_event, "node_started", f"Generating questions for: {concept_name(node)}",
            node_id=node.id, title=concept_name(node),
            index=position, total=total_nodes,
        )
        questions, correction = [], ""
        rounds = settings.QUESTION_GENERATION_ROUNDS
        for round_number in range(1, rounds + 1):
            if round_number > 1:
                if not is_short(node):
                    break
                _emit(
                    on_event, "round_started",
                    f"Round {round_number} of {rounds}: {concept_name(node)} is still short",
                    round=round_number, rounds=rounds, total=1,
                )
            kept, correction, unchecked = generate_questions_for_node(
                node, classifier, on_event=on_event, stats=stats,
                append=append or round_number > 1, correction=correction,
            )
            questions.extend(kept)
            if unchecked:
                break
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
        excluded_create=stats["excluded_create"],
        duplicates=stats["duplicates"],
        trimmed=stats["trimmed"],
        ungrounded=stats["ungrounded"],
        unverified=stats["unverified"],
        by_thinking_order=dict(Counter(q.thinking_order for q in all_questions)),
        by_bloom=dict(Counter(q.bloom_level for q in all_questions)),
    )
    return all_questions


def _prepare_generation(on_event):
    """Warm the model and load the classifier."""
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
    return _get_classifier()


def generate_questions_for_topic(outline_node, nodes, on_event=None, rounds=None):
    """Top up every concept of a topic to its minimum, in rounds."""
    from .question_bank import tier_counts

    rounds = rounds or settings.QUESTION_GENERATION_ROUNDS
    minimum = question_minimum()
    goal = " + ".join(f"{minimum[order]} {order}" for order in QUESTION_DISTRIBUTION)

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
    added, failed, corrections, unchecked_nodes = 0, {}, {}, set()
    if targets:
        classifier = _prepare_generation(on_event)
    for round_number in range(1, rounds + 1):
        if not targets:
            break
        logger.info(
            "[Questions] ROUND %s of %s: %s concept(s) short of %s: %s",
            round_number, rounds, len(targets), goal,
            ", ".join(name(concept_name(node)) for node in targets),
        )
        _emit(
            on_event, "round_started",
            f"Round {round_number} of {rounds}: {len(targets)} concept(s) still short",
            round=round_number, rounds=rounds, total=len(targets),
        )
        for position, node in enumerate(targets, start=1):
            have = tier_counts(node)
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
            try:
                kept, corrections[node.id], unchecked = generate_questions_for_node(
                    node, classifier, on_event=on_event, stats=stats, append=True,
                    correction=corrections.get(node.id, ""),
                )
                if unchecked:
                    unchecked_nodes.add(node.id)
            except Exception as exc:
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
        targets = [
            node for node in targets
            if node.id not in failed and node.id not in unchecked_nodes and is_short(node)
        ]

    still_short = [concept_name(node) for node in nodes if node.id not in failed and is_short(node)]
    logger.info(
        "[Questions] topic %s  done: %s questions added from %s drafts; dropped %s not grounded "
        "in the lesson, %s duplicates, %s wrong level, %s extra%s%s",
        outline_node.id, added, stats["total_drafted"], stats["ungrounded"],
        stats["duplicates"], stats["excluded_create"], stats["trimmed"],
        f"; still short: {', '.join(still_short)}" if still_short else "",
        f"; failed: {len(failed)}" if failed else "",
    )
    from question_generation.models import GeneratedQuestion
    unchecked_total = GeneratedQuestion.objects.filter(
        node__in=nodes, status="review", check_result="unverified",
    ).count()
    summary = f"Added {added} questions"
    if unchecked_total:
        summary += (
            f"; {unchecked_total} could not be checked because the AI service was unavailable. "
            "They are in Needs review: use Check all again"
        )
    _emit(
        on_event, "topic_finished", summary,
        added=added, still_short=still_short, failed=list(failed.values()),
        drafted=stats["total_drafted"], ungrounded=stats["ungrounded"], unchecked=unchecked_total,
    )
    return {
        "added": added, "still_short": still_short, "failed": list(failed.values()),
        "unchecked": unchecked_total,
    }
