import logging
import os
import re

from django.db import transaction
from django.db.models import Q
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from lessons.models import (
    LearningMaterial,
    LearningObject,
    LearningObjectGroup,
    LearningObjectMatchSuggestion,
    Question,
    QuestionLearningObjectLink,
)
from .instructional_content_classifier import detect_instructional_document_role
from .question_workflow import (
    duplicate_in_topic,
    enriched_question_values,
    parse_question_structure,
    question_fingerprint,
)


logger = logging.getLogger(__name__)


_QUESTION_CONTAINER_LABELS = {
    "ask",
    "assessment",
    "assessments",
    "check your knowledge",
    "check your understanding",
    "directions",
    "exercise",
    "exercises",
    "instructions",
    "knowledge check",
    "practice",
    "practice questions",
    "question",
    "questions",
    "quiz",
    "review questions",
    "teacher check",
    "test",
    "true false",
    "true or false",
    "worksheet",
}


def learning_objects_are_confirmed(material: LearningMaterial) -> bool:
    return bool((material.generated_json or {}).get("learning_objects_confirmed"))


def normalize_learning_object_title(value: str) -> str:
    """Return a topic-neutral key used only to connect equivalent titles."""
    value = re.sub(
        r"\bpart\s+\d+\s*(?:of|/)\s*\d+\b",
        " ",
        value.casefold(),
    )
    value = re.sub(
        r"^\s*(?:(?:unit|lesson|chapter|section|topic)\s*)?\d+(?:\.\d+)*\s*[.):-]*\s*",
        "",
        value,
    )
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def _env_score(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return min(1.0, max(0.0, value))


def _match_configuration() -> dict[str, float]:
    weights = {
        "title": _env_score("LEARNING_OBJECT_MATCH_TITLE_WEIGHT", 0.35),
        "content": _env_score("LEARNING_OBJECT_MATCH_CONTENT_WEIGHT", 0.25),
        "character": _env_score("LEARNING_OBJECT_MATCH_CHARACTER_WEIGHT", 0.20),
        "keywords": _env_score("LEARNING_OBJECT_MATCH_KEYWORD_WEIGHT", 0.10),
        "structure": _env_score("LEARNING_OBJECT_MATCH_STRUCTURE_WEIGHT", 0.10),
    }
    total = sum(weights.values()) or 1.0
    return {
        **{name: value / total for name, value in weights.items()},
        "auto_threshold": _env_score("LEARNING_OBJECT_MATCH_AUTO_THRESHOLD", 0.50),
        "review_threshold": _env_score("LEARNING_OBJECT_MATCH_REVIEW_THRESHOLD", 0.30),
        "minimum_margin": _env_score("LEARNING_OBJECT_MATCH_MINIMUM_MARGIN", 0.08),
        "group_member_threshold": _env_score(
            "LEARNING_OBJECT_MATCH_GROUP_MEMBER_THRESHOLD",
            0.45,
        ),
        "content_support_threshold": _env_score(
            "LEARNING_OBJECT_MATCH_CONTENT_SUPPORT_THRESHOLD",
            0.30,
        ),
    }


def learning_object_match_debug_configuration() -> dict:
    """Expose the effective matcher settings for developer-console diagnostics."""
    from . import semantic_grouping
    semantic_mode = semantic_grouping.mode()
    if semantic_mode != "legacy":
        result = {
            "method": "sbert_cross_encoder_content_v1",
            "semantic_mode": semantic_mode,
            "weights": {},
            "thresholds": {},
        }
        try:
            semantic_policy = semantic_grouping.policy()
            result["semantic_policy"] = semantic_policy
            result["thresholds"] = {
                "auto_connect": semantic_policy["auto_threshold"],
                "teacher_review": semantic_policy["review_threshold"],
                "minimum_sbert_cosine": semantic_policy["minimum_sbert_cosine"],
                "minimum_winner_margin": semantic_policy["minimum_margin"],
            }
        except semantic_grouping.SemanticUnavailable as exc:
            result["semantic_error"] = str(exc)
        return result

    configuration = _match_configuration()
    return {
        "method": "hybrid_tfidf_v3_content_guard",
        "semantic_mode": semantic_mode,
        "weights": {
            "title_tfidf": configuration["title"],
            "content_tfidf": configuration["content"],
            "character_ngram": configuration["character"],
            "keyword_overlap": configuration["keywords"],
            "structure": configuration["structure"],
        },
        "thresholds": {
            "auto_connect": configuration["auto_threshold"],
            "teacher_review": configuration["review_threshold"],
            "minimum_winner_margin": configuration["minimum_margin"],
            "minimum_group_member": configuration["group_member_threshold"],
            "minimum_content_support": configuration["content_support_threshold"],
        },
    }


def _tfidf_pair_similarity(
    left: str,
    right: str,
    *,
    analyzer: str = "word",
    ngram_range: tuple[int, int] = (1, 2),
) -> float:
    if not left.strip() or not right.strip():
        return 0.0
    try:
        kwargs = {
            "lowercase": True,
            "analyzer": analyzer,
            "ngram_range": ngram_range,
        }
        if analyzer == "word":
            kwargs["stop_words"] = "english"
        matrix = TfidfVectorizer(**kwargs).fit_transform([left, right])
    except (TypeError, ValueError):
        return 0.0
    return float(cosine_similarity(matrix[0:1], matrix[1:2])[0, 0])


def _keyword_overlap(left: str, right: str) -> float:
    def keywords(value):
        return {
            token
            for token in re.findall(r"[a-z0-9]+", value.casefold())
            if len(token) > 2 and token not in ENGLISH_STOP_WORDS
        }

    left_keywords = keywords(left)
    right_keywords = keywords(right)
    union = left_keywords | right_keywords
    return len(left_keywords & right_keywords) / len(union) if union else 0.0


def _structure_similarity(
    section_title: str,
    order: int,
    candidate: LearningObject,
) -> float:
    order_score = 1.0 / (1.0 + abs(order - candidate.order))
    if not section_title.strip() or not candidate.section_title.strip():
        return order_score
    section_score = _tfidf_pair_similarity(section_title, candidate.section_title)
    return (0.75 * section_score) + (0.25 * order_score)


def learning_object_match_evidence(
    title: str,
    content: str,
    order: int,
    candidate: LearningObject,
    section_title: str = "",
) -> dict:
    """Return an explainable, configurable lexical and structural score."""
    configuration = _match_configuration()
    title_score = _tfidf_pair_similarity(title, candidate.title)
    content_score = _tfidf_pair_similarity(content, candidate.content)
    character_score = _tfidf_pair_similarity(
        f"{title} {content}",
        f"{candidate.title} {candidate.content}",
        analyzer="char_wb",
        ngram_range=(3, 5),
    )
    keyword_score = _keyword_overlap(
        f"{title} {content}",
        f"{candidate.title} {candidate.content}",
    )
    structure_score = _structure_similarity(section_title, order, candidate)
    exact_title = bool(
        normalize_learning_object_title(title)
        and normalize_learning_object_title(title)
        == normalize_learning_object_title(candidate.title)
    )
    score = (
        configuration["title"] * title_score
        + configuration["content"] * content_score
        + configuration["character"] * character_score
        + configuration["keywords"] * keyword_score
        + configuration["structure"] * structure_score
    )
    content_support = (
        (0.55 * content_score)
        + (0.30 * character_score)
        + (0.15 * keyword_score)
    )
    return {
        "score": round(min(1.0, score), 6),
        "title_tfidf": round(title_score, 6),
        "content_tfidf": round(content_score, 6),
        "character_ngram": round(character_score, 6),
        "keyword_overlap": round(keyword_score, 6),
        "structure": round(structure_score, 6),
        "content_support": round(content_support, 6),
        "exact_normalized_title": exact_title,
    }


def _rank_candidate_groups(
    material: LearningMaterial,
    title: str,
    content: str,
    kind: str,
    order: int,
    section_title: str = "",
    source_object_id: int | None = None,
) -> list[dict]:
    candidates = (
        LearningObject.objects.filter(
            material__outline_node_id=material.outline_node_id,
            material__generated_json__learning_objects_confirmed=True,
            kind=kind,
            group__isnull=False,
        )
        .exclude(material_id=material.id)
        .select_related("group", "material")
    )
    best_by_group = {}
    for candidate in candidates:
        group_members = candidate.group.learning_objects.exclude(pk=source_object_id)
        if group_members.filter(material_id=material.id).exists():
            continue
        evidence = learning_object_match_evidence(
            title,
            content,
            order,
            candidate,
            section_title=section_title,
        )
        current = best_by_group.get(candidate.group_id)
        if current is None or evidence["score"] > current["evidence"]["score"]:
            best_by_group[candidate.group_id] = {
                "candidate": candidate,
                "evidence": evidence,
            }
    return sorted(
        best_by_group.values(),
        key=lambda item: (-item["evidence"]["score"], item["candidate"].id),
    )


def _match_decision(
    material: LearningMaterial,
    title: str,
    content: str,
    kind: str,
    order: int,
    section_title: str = "",
    source_object_id: int | None = None,
) -> dict | None:
    if not learning_objects_are_confirmed(material):
        return None
    from . import semantic_grouping
    if semantic_grouping.mode() != "legacy":
        try:
            return semantic_grouping.semantic_decision(
                material, title, content, kind, order, section_title, source_object_id,
            )
        except Exception:
            # Never fall back to permissive lexical auto-linking after a model
            # failure. Leave the object separate and record the failure.
            logger.exception("Semantic grouping unavailable; no automatic grouping performed")
            return None
    return _legacy_match_decision(material, title, content, kind, order, section_title, source_object_id)


def _legacy_match_decision(material, title, content, kind, order, section_title="", source_object_id=None):
    if not learning_objects_are_confirmed(material):
        return None
    ranked = _rank_candidate_groups(
        material,
        title,
        content,
        kind,
        order,
        section_title=section_title,
        source_object_id=source_object_id,
    )
    if not ranked:
        logger.debug(
            "Learning-object matcher found no cross-PDF candidates: material=%s source_object=%s title=%r",
            material.id,
            source_object_id,
            title,
        )
        return None
    configuration = _match_configuration()
    best = ranked[0]
    runner_up_score = ranked[1]["evidence"]["score"] if len(ranked) > 1 else 0.0
    margin = best["evidence"]["score"] - runner_up_score
    group_member_scores = []
    group_member_content_supports = []
    for member in best["candidate"].group.learning_objects.filter(
        material__generated_json__learning_objects_confirmed=True,
    ).exclude(pk=source_object_id):
        if member.material_id == material.id:
            continue
        member_evidence = learning_object_match_evidence(
            title,
            content,
            order,
            member,
            section_title=section_title,
        )
        group_member_scores.append(member_evidence["score"])
        group_member_content_supports.append(member_evidence["content_support"])
    minimum_group_score = min(group_member_scores) if group_member_scores else best["evidence"]["score"]
    minimum_group_content_support = (
        min(group_member_content_supports)
        if group_member_content_supports
        else best["evidence"]["content_support"]
    )
    score = best["evidence"]["score"]
    if (
        score >= configuration["auto_threshold"]
        and best["evidence"]["content_support"] >= configuration["content_support_threshold"]
        and margin >= configuration["minimum_margin"]
        and minimum_group_score >= configuration["group_member_threshold"]
        and minimum_group_content_support >= configuration["content_support_threshold"]
    ):
        confidence = LearningObjectMatchSuggestion.Confidence.HIGH
    elif score >= configuration["review_threshold"]:
        confidence = LearningObjectMatchSuggestion.Confidence.MEDIUM
    else:
        confidence = None
    best["evidence"].update(
        {
            "runner_up_score": round(runner_up_score, 6),
            "winner_margin": round(margin, 6),
            "minimum_group_member_score": round(minimum_group_score, 6),
            "minimum_group_content_support": round(minimum_group_content_support, 6),
            "method": "hybrid_tfidf_v3_content_guard",
        }
    )
    logger.debug(
        "Learning-object match: material=%s source_object=%s candidate=%s score=%.4f "
        "auto_threshold=%.4f review_threshold=%.4f content_support=%.4f "
        "content_support_threshold=%.4f margin=%.4f minimum_margin=%.4f "
        "group_member_score=%.4f group_member_threshold=%.4f confidence=%s evidence=%s",
        material.id,
        source_object_id,
        best["candidate"].id,
        score,
        configuration["auto_threshold"],
        configuration["review_threshold"],
        best["evidence"]["content_support"],
        configuration["content_support_threshold"],
        margin,
        configuration["minimum_margin"],
        minimum_group_score,
        configuration["group_member_threshold"],
        confidence,
        best["evidence"],
    )
    return {**best, "confidence": confidence}


def _is_answer_line(text: str) -> bool:
    label = normalize_learning_object_title(text)
    return bool(
        re.match(
            r"^(?:a\s*:|(?:answer|answers|correct answer|expected answer|sample answer|suggested answer)\b(?:\s*[:.-])?)",
            text.strip(),
            flags=re.IGNORECASE,
        )
        or label in {
            "answer",
            "answers",
            "correct answer",
            "expected answer",
            "sample answer",
            "suggested answer",
        }
    )


def _clean_question_line(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    # Remove isolated PDF glyph/font artifacts while preserving authored words.
    text = re.sub(r"^[A-Za-z]?\s*[\x00-\x1f\u2022\u25cf\u25aa]+\s*", "", text).strip()
    return text


def _is_form_identity_line(text: str) -> bool:
    label = normalize_learning_object_title(text)
    fields = {"date", "name", "score", "section", "student"}
    return bool(re.search(r"_{3,}", text or "") and len(set(label.split()) & fields) >= 2)


def _is_question_line(text: str, *, allow_numbered_statement: bool = False) -> bool:
    stripped = _clean_question_line(text)
    if not stripped or _is_answer_line(stripped) or _is_form_identity_line(stripped):
        return False
    if stripped.endswith("?"):
        return True
    if re.match(r"^(?:q\s*:|q\d+\b|question\s+\d+\b)", stripped, flags=re.IGNORECASE):
        return True
    numbered_prompt = re.sub(r"^\d+[.)]\s*", "", stripped)
    if allow_numbered_statement and numbered_prompt != stripped:
        return True
    if numbered_prompt != stripped and re.match(
        r"^(?:answer|calculate|choose|classify|compare|define|describe|discuss|explain|"
        r"give|identify|list|match|name|select|solve|state|write)\b",
        numbered_prompt,
        flags=re.IGNORECASE,
    ):
        return True
    if re.search(r"_{3,}", stripped):
        without_number = re.sub(r"^\d+[.)]\s*", "", stripped)
        return bool(
            without_number != stripped
            or (stripped[:1].isupper() and len(normalize_learning_object_title(stripped).split()) >= 2)
        )
    return len(re.findall(r"(?:^|\s)[A-Da-d][.)]\s+", stripped)) >= 2


def _starts_new_question(text: str) -> bool:
    cleaned = re.sub(r"^\d+[.)]\s*", "", _clean_question_line(text))
    return bool(
        re.match(
            r"^(?:q\s*:|question\s+\d+\b|who|what|when|where|why|how|which|whose|whom|"
            r"is|are|am|do|does|did|can|could|will|would|should|has|have|had|may|might|were|was|"
            r"answer|calculate|choose|classify|compare|define|describe|discuss|explain|give|"
            r"identify|list|match|name|select|solve|state|write)\b",
            cleaned,
            flags=re.IGNORECASE,
        )
    )


def _question_prompts_from_block(
    text: str,
    *,
    allow_numbered_statements: bool = False,
) -> list[str]:
    """Extract question prompts while leaving headings and supplied answers out."""
    raw_lines = [_clean_question_line(line) for line in (text or "").splitlines()]
    lines = [line for line in raw_lines if line]
    prompts = []
    current = []

    def flush():
        if not current:
            return
        prompt = "\n".join(current).strip()
        if _is_question_line(prompt, allow_numbered_statement=allow_numbered_statements):
            prompts.append(prompt)
        current.clear()

    for line in lines:
        label = normalize_learning_object_title(line)
        if label in _QUESTION_CONTAINER_LABELS:
            continue
        if _is_answer_line(line):
            flush()
            continue

        # Some teacher guides place Q and A on the same physical line. Keep
        # the authored question and discard the supplied answer portion.
        question_only = re.split(r"\s+(?:A|Answer)\s*:\s*", line, maxsplit=1, flags=re.IGNORECASE)[0].strip()
        starts_question = _is_question_line(
            question_only,
            allow_numbered_statement=allow_numbered_statements,
        )
        starts_question_phrase = _starts_new_question(question_only)
        starts_numbered_question = bool(re.match(r"^\d+[.)]\s*", question_only))
        is_choice = bool(re.match(r"^[A-Da-d][.)]\s+", question_only))

        if starts_numbered_question and current:
            flush()
        if (
            starts_question_phrase
            and current
            and _clean_question_line("\n".join(current)).endswith("?")
        ):
            flush()
        if starts_question or starts_question_phrase:
            current.append(question_only)
        elif current and is_choice:
            current.append(question_only)
        elif current and not re.match(r"^(?:expected|correct|sample|suggested)\s+answer\b", label):
            current.append(question_only)

    flush()
    return prompts


def detected_question_payloads(classified_blocks: list[dict]) -> list[dict]:
    """Return database-ready questions, including True/False worksheet statements."""
    payloads = []
    document_is_assessment = (
        detect_instructional_document_role(classified_blocks or []) == "assessment"
    )
    seen_fingerprints = set()
    for block in classified_blocks or []:
        category = block.get("category")
        is_contextual_assessment_block = (
            document_is_assessment
            and category in {"lesson_content", "needs_review"}
        )
        if category != "assessment" and not is_contextual_assessment_block:
            continue
        for prompt in _question_prompts_from_block(
            block.get("text") or "",
            allow_numbered_statements=is_contextual_assessment_block,
        ):
            excerpt = block.get("text") or prompt
            structure = parse_question_structure(prompt, excerpt)
            values = enriched_question_values(**structure)
            fingerprint = values["content_fingerprint"]
            if not fingerprint or fingerprint in seen_fingerprints:
                continue
            seen_fingerprints.add(fingerprint)
            payloads.append({
                **values,
                "source_type": Question.SourceType.PDF,
                "source_page": block.get("page"),
                "source_block_id": block.get("block_id"),
                "source_excerpt": excerpt,
            })
    return payloads


def prior_learning_object_groups(material: LearningMaterial) -> dict[tuple[str, int], int]:
    """Remember group membership before regeneration replaces derived objects."""
    return {
        (normalize_learning_object_title(item.title), item.order): item.group_id
        for item in material.learning_objects.exclude(group__isnull=True)
    }


def resolve_learning_object_group(
    material: LearningMaterial,
    title: str,
    kind: str,
    order: int,
    content: str = "",
    section_title: str = "",
    prior_groups: dict[tuple[str, int], int] | None = None,
) -> LearningObjectGroup | None:
    """Auto-connect only a high-confidence, clear cross-PDF group winner."""
    if material.outline_node_id is None:
        return None

    normalized_title = normalize_learning_object_title(title)
    preferred_group_id = (prior_groups or {}).get((normalized_title, order))
    if preferred_group_id:
        preferred = LearningObjectGroup.objects.filter(
            pk=preferred_group_id,
            outline_node_id=material.outline_node_id,
        ).first()
        if preferred:
            return preferred

    from . import semantic_grouping
    if semantic_grouping.mode() != "legacy":
        # Semantic matching runs after the object has an ID, so rejected pairs
        # and the persisted automatic decision can both be handled correctly.
        return LearningObjectGroup.objects.create(
            outline_node_id=material.outline_node_id, label=title[:255],
        )

    decision = _match_decision(
        material,
        title,
        content,
        kind,
        order,
        section_title=section_title,
    )
    if decision and decision["confidence"] == LearningObjectMatchSuggestion.Confidence.HIGH:
        return decision["candidate"].group

    return LearningObjectGroup.objects.create(
        outline_node_id=material.outline_node_id,
        label=title[:255],
    )


def ensure_learning_object_groups(material: LearningMaterial) -> None:
    """Give every object a neutral group and reuse matching cross-PDF groups."""
    if material.outline_node_id is None:
        return
    for item in material.learning_objects.filter(group__isnull=True).order_by("order", "id"):
        item.group = resolve_learning_object_group(
            material,
            item.title,
            item.kind,
            item.order,
            content=item.content,
            section_title=item.section_title,
        )
        item.save(update_fields=["group"])
    refresh_learning_object_match_suggestions(material)


def _canonical_match_pair(
    first: LearningObject,
    second: LearningObject,
) -> tuple[LearningObject, LearningObject]:
    return (first, second) if first.id < second.id else (second, first)


def record_teacher_match_decision(
    first: LearningObject,
    second: LearningObject,
    *,
    accepted: bool,
) -> LearningObjectMatchSuggestion | None:
    """Persist a teacher decision without pretending it was algorithmic confidence."""
    if (
        first.id == second.id
        or first.material_id == second.material_id
        or first.material.outline_node_id != second.material.outline_node_id
        or first.kind != second.kind
    ):
        return None
    source, candidate = _canonical_match_pair(first, second)
    existing = LearningObjectMatchSuggestion.objects.filter(
        source_learning_object=source,
        candidate_learning_object=candidate,
    ).first()
    evidence = dict(existing.evidence or {}) if existing else {}
    evidence.update({"teacher_reviewed": True, "teacher_decision": "accepted" if accepted else "rejected"})
    suggestion, _ = LearningObjectMatchSuggestion.objects.update_or_create(
        source_learning_object=source,
        candidate_learning_object=candidate,
        defaults={
            "outline_node_id": source.material.outline_node_id,
            "similarity_score": existing.similarity_score if existing else 0.0,
            "confidence": LearningObjectMatchSuggestion.Confidence.TEACHER_CONFIRMED,
            "evidence": evidence,
            "status": (
                LearningObjectMatchSuggestion.Status.ACCEPTED
                if accepted
                else LearningObjectMatchSuggestion.Status.REJECTED
            ),
        },
    )
    return suggestion


def _nominated_candidate(matcher, learning_object, cache):
    """The single object this one picks as its own cross-PDF equivalent.

    Memoised per refresh: in a lopsided pair of PDFs many objects nominate the
    same partner, so the reciprocal lookup is asked about far fewer objects
    than there are pairs.
    """
    if learning_object.id not in cache:
        try:
            cache[learning_object.id] = matcher(
                learning_object.material,
                learning_object.title,
                learning_object.content,
                learning_object.kind,
                learning_object.order,
                section_title=learning_object.section_title,
                source_object_id=learning_object.id,
            )
        except Exception:
            # A failed lookup is not evidence against the pair. Record the
            # failure and let the pair through rather than silently emptying
            # the teacher's queue on a transient model error.
            logger.exception(
                "Reciprocal match lookup failed for learning object %s",
                learning_object.id,
            )
            cache[learning_object.id] = "unavailable"
    return cache[learning_object.id]


def _is_mutual_best_match(matcher, candidate_object, source_object, cache):
    """True when the candidate nominates the source back.

    "Both teach the same concept" is a symmetric claim, but the matcher only
    ever asks one direction: every object picks its own closest partner in the
    other PDF, and nothing stops a dozen objects picking the same one. Those
    nominations are mutually exclusive -- at most one of them can be the same
    concept -- so proposing all of them turns one real question into a dozen
    declines. Requiring the nomination to run both ways makes the pair a claim
    about the two objects rather than about one of them.
    """
    decision = _nominated_candidate(matcher, candidate_object, cache)
    if decision == "unavailable":
        return True
    if not decision:
        return False

    reciprocal = decision.get("candidate")
    if reciprocal and reciprocal.id == source_object.id:
        return True

    # Only a DECISIVE rival nomination is evidence against the pair. When the
    # candidate is torn between equally good partners -- three PDFs each
    # carrying the same concept, say -- its pick is arbitrary, and vetoing on
    # it would hide a real duplicate the teacher needs to resolve. Ambiguity is
    # a reason to ask, not a reason to stay silent.
    margin = (decision.get("evidence") or {}).get("winner_margin")
    if margin is not None and margin <= 0:
        return True
    return False


def refresh_learning_object_match_suggestions(material: LearningMaterial) -> None:
    """Persist explainable cross-PDF candidates for this material.

    High-confidence semantic matches have already been scored against every
    member of the candidate group, so every qualifying object may join that
    group. Medium-confidence pair suggestions still require reciprocal
    nomination to avoid flooding the teacher's queue.
    """
    if material.outline_node_id is None:
        return
    from . import semantic_grouping
    if semantic_grouping.mode() != "legacy":
        try:
            semantic_grouping.policy()
            semantic_grouping.runtime()
        except Exception:
            logger.exception("Semantic grouping unavailable; preserving the existing review queue")
            return
    if not learning_objects_are_confirmed(material):
        LearningObjectMatchSuggestion.objects.filter(
            Q(source_learning_object__material=material)
            | Q(candidate_learning_object__material=material),
            status=LearningObjectMatchSuggestion.Status.PENDING,
        ).delete()
        return
    retained_ids = []
    reciprocal_cache = {}
    learning_objects = list(
        material.learning_objects.select_related("material", "group").order_by("order", "id")
    )
    semantic_active = semantic_grouping.mode() != "legacy"
    matcher = semantic_grouping.semantic_decision if semantic_active else _match_decision
    for source_object in learning_objects:
        if semantic_active and source_object.group_id and LearningObject.objects.filter(
                group_id=source_object.group_id).exclude(pk=source_object.id).exists():
            # Keep existing groups intact during background refresh. Changes to
            # membership must come from an explicit teacher action.
            retained_ids.extend(LearningObjectMatchSuggestion.objects.filter(
                Q(source_learning_object=source_object) | Q(candidate_learning_object=source_object)
            ).values_list("id", flat=True))
            continue
        try:
            decision = matcher(
                material,
                source_object.title,
                source_object.content,
                source_object.kind,
                source_object.order,
                section_title=source_object.section_title,
                source_object_id=source_object.id,
            )
        except Exception:
            if not semantic_active:
                raise
            # An inference/cache failure is not a low-confidence decision.
            # In particular, do not delete existing pending suggestions below.
            logger.exception("Semantic inference failed; preserving the remaining review queue")
            return
        if not decision or decision["confidence"] is None:
            continue
        candidate_object = decision["candidate"]
        source, candidate = _canonical_match_pair(source_object, candidate_object)
        existing = LearningObjectMatchSuggestion.objects.filter(
            source_learning_object=source,
            candidate_learning_object=candidate,
        ).first()
        if existing and (existing.evidence or {}).get("teacher_reviewed"):
            retained_ids.append(existing.id)
            continue
        is_high_confidence = (
            decision["confidence"]
            == LearningObjectMatchSuggestion.Confidence.HIGH
        )
        if not is_high_confidence and not _is_mutual_best_match(
            matcher, candidate_object, source_object, reciprocal_cache
        ):
            continue
        if (
            is_high_confidence
            and candidate_object.group_id
            and (not existing or existing.status != LearningObjectMatchSuggestion.Status.REJECTED)
            and source_object.group_id != candidate_object.group_id
        ):
            old_group_id = source_object.group_id
            source_object.group_id = candidate_object.group_id
            source_object.save(update_fields=["group"])
            if old_group_id:
                LearningObjectGroup.objects.filter(
                    pk=old_group_id,
                    learning_objects__isnull=True,
                ).delete()
        same_group = bool(
            source_object.group_id
            and source_object.group_id == candidate_object.group_id
        )
        status_value = (
            LearningObjectMatchSuggestion.Status.ACCEPTED
            if same_group
            else LearningObjectMatchSuggestion.Status.PENDING
        )
        if existing and existing.status == LearningObjectMatchSuggestion.Status.REJECTED:
            status_value = existing.status
        suggestion, _ = LearningObjectMatchSuggestion.objects.update_or_create(
            source_learning_object=source,
            candidate_learning_object=candidate,
            defaults={
                "outline_node_id": material.outline_node_id,
                "similarity_score": decision["evidence"]["score"],
                "confidence": decision["confidence"],
                "evidence": decision["evidence"],
                "status": status_value,
            },
        )
        retained_ids.append(suggestion.id)

    stale_pending = LearningObjectMatchSuggestion.objects.filter(
        Q(source_learning_object__material=material)
        | Q(candidate_learning_object__material=material),
        status=LearningObjectMatchSuggestion.Status.PENDING,
    )
    if retained_ids:
        stale_pending = stale_pending.exclude(id__in=retained_ids)
    stale_pending.delete()


def remove_empty_learning_object_groups(material: LearningMaterial) -> None:
    if material.outline_node_id is None:
        return
    LearningObjectGroup.objects.filter(
        outline_node_id=material.outline_node_id,
        learning_objects__isnull=True,
    ).delete()


CONFIRMED_QUESTION_PAIRING_STATUSES = (
    QuestionLearningObjectLink.ReviewStatus.AUTO_CONFIRMED,
    QuestionLearningObjectLink.ReviewStatus.TEACHER_CONFIRMED,
)

TEACHER_QUESTION_PAIRING_STATUSES = (
    QuestionLearningObjectLink.ReviewStatus.TEACHER_CONFIRMED,
    QuestionLearningObjectLink.ReviewStatus.TEACHER_UNPAIRED,
)


def question_pairing_debug_configuration() -> dict:
    auto_threshold = _env_score("QUESTION_PAIR_AUTO_THRESHOLD", 0.55)
    review_threshold = min(
        auto_threshold,
        _env_score("QUESTION_PAIR_REVIEW_THRESHOLD", 0.25),
    )
    weights = {
        "lexical_tfidf": _env_score("QUESTION_PAIR_LEXICAL_WEIGHT", 0.70),
        "source_block_proximity": _env_score(
            "QUESTION_PAIR_BLOCK_PROXIMITY_WEIGHT",
            0.20,
        ),
        "same_page": _env_score("QUESTION_PAIR_SAME_PAGE_WEIGHT", 0.10),
    }
    weight_total = sum(weights.values()) or 1.0
    return {
        "method": "layout_tfidf",
        "weights": {
            name: value / weight_total
            for name, value in weights.items()
        },
        "thresholds": {
            "auto_confirm": auto_threshold,
            "teacher_review": review_threshold,
        },
    }


def _lexical_scores(question: Question, learning_objects: list[LearningObject]) -> list[float]:
    documents = [question.prompt] + [f"{item.title} {item.content}" for item in learning_objects]
    try:
        matrix = TfidfVectorizer(lowercase=True, stop_words="english", ngram_range=(1, 2)).fit_transform(documents)
    except (TypeError, ValueError):
        return [0.0] * len(learning_objects)
    base_scores = cosine_similarity(matrix[0:1], matrix[1:]).ravel()

    # Character n-grams compare related word forms such as "fill" and "fills"
    # without using subject-specific rules.
    content_documents = [question.prompt] + [item.content for item in learning_objects]
    try:
        character_matrix = TfidfVectorizer(
            lowercase=True,
            analyzer="char_wb",
            ngram_range=(3, 5),
        ).fit_transform(content_documents)
        character_scores = cosine_similarity(
            character_matrix[0:1], character_matrix[1:]
        ).ravel()
    except (TypeError, ValueError):
        character_scores = [0.0] * len(learning_objects)

    generic_title_words = {
        "example", "examples", "fact", "facts", "key", "lesson", "matter",
        "part", "point", "points", "remember", "state", "states", "student",
        "students",
    }
    question_words = set(normalize_learning_object_title(question.prompt).split()) - generic_title_words
    scores = []
    for learning_object, base_score, character_score in zip(
        learning_objects,
        base_scores,
        character_scores,
    ):
        title_words = (
            set(normalize_learning_object_title(learning_object.title).split())
            - generic_title_words
        )
        title_coverage = (
            len(title_words & question_words) / len(title_words)
            if title_words
            else 0.0
        )
        direct_title_score = (0.65 * float(base_score)) + (0.35 * title_coverage)
        morphology_score = (0.65 * float(base_score)) + (0.35 * float(character_score))
        scores.append(max(float(base_score), direct_title_score, morphology_score))
    return scores


def _pairing_score(
    question: Question,
    learning_object: LearningObject,
    lexical_score: float,
    weights: dict[str, float] | None = None,
) -> float:
    weights = weights or question_pairing_debug_configuration()["weights"]
    same_material = question.material_id == learning_object.material_id
    if not same_material:
        # Page and block positions have no meaning across different PDFs. Use
        # the full lexical score when pairing a question-only document to its topic.
        return min(1.0, lexical_score)
    same_page = bool(
        question.source_page
        and learning_object.source_page
        and question.source_page == learning_object.source_page
    )
    proximity = 0.0
    if question.source_block_id and learning_object.source_block_id:
        distance = question.source_block_id - learning_object.source_block_id
        if distance >= 0:
            proximity = 1.0 / (1.0 + (distance / 5.0))
        else:
            proximity = 0.15 / (1.0 + (abs(distance) / 5.0))
    return min(
        1.0,
        (weights["lexical_tfidf"] * lexical_score)
        + (weights["source_block_proximity"] * proximity)
        + (weights["same_page"] if same_page else 0.0),
    )


def refresh_question_learning_object_links(material: LearningMaterial) -> None:
    """Auto-confirm strong pairs and preserve teacher decisions for uncertain ones."""
    learning_objects = (
        list(material.learning_objects.order_by("order", "id"))
        if learning_objects_are_confirmed(material)
        else []
    )
    uses_topic_candidates = not learning_objects and material.outline_node_id is not None
    if uses_topic_candidates:
        learning_objects = list(
            LearningObject.objects.filter(
                material__outline_node_id=material.outline_node_id,
                material__generated_json__learning_objects_confirmed=True,
            )
            .exclude(material_id=material.id)
            .select_related("material", "group")
            .order_by("material_id", "order", "id")
        )
    questions = list(material.questions.order_by("order", "id"))
    if not learning_objects:
        QuestionLearningObjectLink.objects.filter(
            question__material=material,
        ).exclude(review_status__in=TEACHER_QUESTION_PAIRING_STATUSES).delete()
        return

    configuration = question_pairing_debug_configuration()
    thresholds = configuration["thresholds"]
    weights = configuration["weights"]
    for question in questions:
        existing = question.learning_object_links.order_by("-is_primary", "-relevance_score", "id").first()
        if existing and existing.review_status in TEACHER_QUESTION_PAIRING_STATUSES:
            continue
        question.learning_object_links.all().delete()
        lexical_scores = _lexical_scores(question, learning_objects)
        ranked = [
            (
                _pairing_score(
                    question,
                    learning_object,
                    lexical_score,
                    weights,
                ),
                learning_object,
            )
            for learning_object, lexical_score in zip(learning_objects, lexical_scores)
        ]
        score, best = max(ranked, key=lambda pair: (pair[0], -pair[1].order, -pair[1].id))
        if score >= thresholds["auto_confirm"]:
            review_status = QuestionLearningObjectLink.ReviewStatus.AUTO_CONFIRMED
            is_primary = True
        elif score >= thresholds["teacher_review"]:
            review_status = QuestionLearningObjectLink.ReviewStatus.PENDING_REVIEW
            is_primary = False
        else:
            review_status = QuestionLearningObjectLink.ReviewStatus.UNMATCHED
            is_primary = False
        QuestionLearningObjectLink.objects.create(
            question=question,
            learning_object=best,
            relevance_score=round(score, 6),
            method="topic_tfidf" if uses_topic_candidates else "layout_tfidf",
            is_primary=is_primary,
            review_status=review_status,
        )
        from .question_workflow import sync_question_to_adaptive
        question.refresh_from_db()
        sync_question_to_adaptive(question)
        logger.debug(
            "Question pairing: question=%s learning_object=%s group=%s score=%.4f "
            "auto_threshold=%.4f review_threshold=%.4f status=%s",
            question.id,
            best.id,
            best.group_id,
            score,
            thresholds["auto_confirm"],
            thresholds["teacher_review"],
            review_status,
        )


@transaction.atomic
def synchronize_detected_questions(material: LearningMaterial, classified_blocks: list[dict]) -> None:
    """Synchronize derived questions and rebuild their content-pair relationships."""
    existing = {
        question.content_fingerprint or question_fingerprint(question.prompt): question
        for question in material.questions.all()
    }
    retained_ids = []
    for order, payload in enumerate(detected_question_payloads(classified_blocks)):
        key = payload["content_fingerprint"]
        question = existing.get(key)
        if question is None:
            question = duplicate_in_topic(material, key)
        if question is None:
            question = Question.objects.create(material=material, order=order, **payload)
        elif question.material_id != material.id:
            # The same question already exists elsewhere in this topic. Keep
            # one canonical row instead of copying it into every uploaded PDF.
            continue
        else:
            for field, value in payload.items():
                setattr(question, field, value)
            question.order = order
            question.save(update_fields=[*payload.keys(), "order"])
        retained_ids.append(question.id)
    material.questions.exclude(id__in=retained_ids).delete()
    refresh_question_learning_object_links(material)


def refresh_material_learning_relationships(material: LearningMaterial) -> None:
    """Refresh neutral groups and pairs after teacher edits to learning objects."""
    ensure_learning_object_groups(material)
    refresh_question_learning_object_links(material)


def question_snapshots(material: LearningMaterial) -> list[dict]:
    """Return the stable handoff contract consumed by a learning-path module."""
    snapshots = []
    for question in material.questions.prefetch_related(
        "learning_object_links__learning_object__group"
    ).order_by("order", "id"):
        links = [
            {
                "learning_object_id": link.learning_object_id,
                "learning_object_group_id": link.learning_object.group_id,
                "relevance_score": link.relevance_score,
                "method": link.method,
                "is_primary": link.is_primary,
                "review_status": link.review_status,
            }
            for link in question.learning_object_links.all()
            if link.review_status in CONFIRMED_QUESTION_PAIRING_STATUSES
        ]
        snapshots.append(
            {
                "id": question.id,
                "prompt": question.prompt,
                "source_type": question.source_type,
                "question_type": question.question_type,
                "choices": question.choices,
                "correct_answer": question.correct_answer,
                "bloom_level": question.bloom_level,
                "thinking_order": question.thinking_order,
                "difficulty": question.difficulty,
                "category": question.category,
                "validation_status": question.validation_status,
                "validation_issues": question.validation_issues,
                "order": question.order,
                "source_page": question.source_page,
                "source_block_id": question.source_block_id,
                "source_excerpt": question.source_excerpt,
                "learning_object_links": links,
            }
        )
    return snapshots
