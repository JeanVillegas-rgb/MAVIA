from collections import Counter
import logging
import os
import re
from time import perf_counter

from django.db import transaction
from django.db.models import Q
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from config.console import name, percent, took
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
    LABEL_FIELDS,
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
    # A lettered list marker splits a concept one PDF enumerates as "A.
    # Melting" from the "Melting" another simply names, the way a numbered one
    # did before the rule above. The separator is what distinguishes a marker
    # from the article opening "A Solid Keeps Its Shape", so it is required,
    # and a heading has to follow it -- without that, a bare "A." would fold
    # to nothing and match every other heading that did the same.
    value = re.sub(r"^\s*[a-z]\s*[.)]\s+(?=\S)", "", value)
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
    from .content_generator import is_recap_section

    best_by_group = {}
    for candidate in candidates:
        group_members = candidate.group.learning_objects.exclude(pk=source_object_id)
        if group_members.filter(material_id=material.id).exists():
            continue
        if any(is_recap_section(member.title, member.section_title) for member in group_members):
            # Nothing is paired into a summary's concept.
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
    from .content_generator import is_recap_section
    if is_recap_section(title, section_title):
        # A summary restates several concepts, so it is no version of any one
        # of them in another PDF; it stays its own step.
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
            logger.exception("[Grouping] the similarity model is unavailable; nothing was grouped automatically")
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
            # Labelled in the Questions step, not at upload.
            values = enriched_question_values(**structure, classify=False)
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


def prior_grouping_fingerprints(material: LearningMaterial) -> dict[tuple[str, int], str]:
    """The text each object was grouped against, keyed like the prior groups.

    Regeneration recreates every object and restores its old group by title and
    position. Carrying the old fingerprint across means a passage whose wording
    changed on re-extraction is still noticed as edited, rather than slipping
    back into its old group as if nothing had happened.
    """
    return {
        (normalize_learning_object_title(item.title), item.order): item.grouping_content_hash
        for item in material.learning_objects.exclude(group__isnull=True)
        if item.grouping_content_hash
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


def attach_orphan_objects_to_their_section(material: LearningMaterial) -> list[int]:
    """Fold an uncorroborated object into the concept its section already names.

    A PDF writes things no other PDF has as a separate object: a "Key idea"
    callout, a bulleted term like "Melting -- solid to liquid, caused by adding
    heat." Cross-PDF grouping cannot place them -- there is nothing to match --
    so each becomes a concept of its own, taught as a step and handed to question
    generation as forty characters of source text.

    **Being alone in its group is the test, not having a section.** "Solid",
    "Liquid" and "Gas" also sit under a heading ("Matter") and must stay three
    concepts, because other PDFs teach them and they are grouped accordingly.
    Filing every object under its section head swallowed them once before, and
    the learning-path criteria's same-name veto then deleted their edges.

    The object keeps its own row, so a question can still be generated from it
    and remediation can still target it; only its group changes.

    Returns the ids moved.
    """
    from .content_generator import _section_heading_title

    if material.outline_node_id is None:
        return []

    siblings = list(
        material.learning_objects.filter(group__isnull=False).order_by("order", "id")
    )
    if not siblings:
        return []

    # Group sizes are counted across the whole topic: corroboration is what a
    # companion in ANOTHER PDF provides, so a per-material count would read
    # every cross-PDF member as absent and move objects that are real concepts.
    sizes = Counter(
        LearningObject.objects.filter(
            material__outline_node_id=material.outline_node_id,
            group_id__in={item.group_id for item in siblings},
        ).values_list("group_id", flat=True)
    )
    referenced_sections = {
        (item.section_title or "").strip()
        for item in siblings
        if (item.section_title or "").strip()
    }
    heads = {
        (item.title or "").strip(): item
        for item in siblings
        if (item.title or "").strip() in referenced_sections
    }
    # A long section is cut into "SOLID (Part 1 of 3)" pieces, so no object is
    # titled "SOLID" itself; its first piece heads the section.
    for item in siblings:
        base = _PART_SUFFIX.sub("", item.title or "").strip()
        if base != (item.title or "").strip() and base in referenced_sections:
            heads.setdefault(base, item)
    # Each later piece is paired with the nearest earlier "Part 1" of the same
    # passage: same title, same section, same number of parts. Several
    # passages can share a title ("Diagram description (Part 1 of 2)" under
    # each state of matter), and must never be fused.
    first_piece_of = {}
    open_passages = {}
    for item in siblings:
        marker = _PART_SUFFIX.search(item.title or "")
        if not marker:
            continue
        key = (
            _PART_SUFFIX.sub("", item.title).strip().casefold(),
            (item.section_title or "").strip().casefold(),
            int(marker.group("total")),
        )
        if int(marker.group("number")) == 1:
            open_passages[key] = item
        elif key in open_passages:
            first_piece_of[item.pk] = open_passages[key]

    moved = []
    joins = dict((material.generated_json or {}).get(SECTION_JOINS_KEY) or {})
    # A section whose heading has no text of its own ("How Flowering Plants
    # Reproduce" over steps 1-4) has no head object. Its first part stands in
    # for the head, so the steps are taught together as one section rather
    # than as four concepts.
    first_parts = {}
    named_after_section = {}
    for item in siblings:
        section = (item.section_title or "").strip()
        if sizes[item.group_id] > 1 or item.kept_apart_from_section:
            continue
        # A later piece of one split passage ("What Is Matter? (Part 2 of 2)")
        # is the same passage as its first piece: it follows that piece into
        # whatever concept the first piece was matched to.
        first_piece = first_piece_of.get(item.pk)
        if first_piece is not None and first_piece.group_id != item.group_id:
            logger.info(
                "[Grouping] %s follows its first part into the same concept  (object %s -> concept %s)",
                name(item.title), item.id, first_piece.group_id,
            )
            item.group_id = first_piece.group_id
            item.save(update_fields=["group"])
            moved.append(item.id)
            joins[str(item.id)] = first_piece.group_id
            continue
        if not section:
            continue
        if _is_glossary_section(section):
            # "Key Vocabulary": each term is something the lesson teaches and
            # a question can target, so the terms stay concepts of their own.
            continue
        head = heads.get(section)
        if head is None and not _section_heading_title(item.title or ""):
            head = first_parts.setdefault(section, item)
        if head is None or head.pk == item.pk or head.group_id == item.group_id:
            continue
        if _section_heading_title(item.title or ""):
            # Its own title is a numbered heading, so this is a section head
            # whose section failed to register -- not a part of the one above.
            # Object 556 ("6. Changing From One State to Another") carries the
            # PREVIOUS section's title, and folding it would file the changes of
            # state under "Comparing the Three States".
            logger.info(
                "[Grouping] %s looks like a section heading itself, so it is not folded into section %s  (object %s)",
                name(item.title), name(section), item.id,
            )
            continue
        logger.info(
            "[Grouping] %s joins its section %s  (object %s -> concept %s)",
            name(item.title), name(section), item.id, head.group_id,
        )
        item.group_id = head.group_id
        item.save(update_fields=["group"])
        moved.append(item.id)
        # Recorded so the join can be released when another PDF arrives:
        # whether a part is taught elsewhere is only known once every PDF is in.
        joins[str(item.id)] = head.group_id
        if first_parts.get(section) is head:
            named_after_section[head.group_id] = section
    # A first part standing in for a heading with no text of its own gave the
    # concept its own name: "Shape" instead of "Comparing the Three States".
    # Once other parts of the section join it, the concept is the section.
    for group_id, section in named_after_section.items():
        group = LearningObjectGroup.objects.filter(pk=group_id).first()
        # Checked in Python: excluding label_locked=True in the query also
        # excluded every group without the key, since a missing key is NULL.
        if group and not (group.version_selection or {}).get("label_locked"):
            group.label = section[:255]
            group.save(update_fields=["label"])
    if moved:
        generated = material.generated_json or {}
        generated[SECTION_JOINS_KEY] = joins
        material.generated_json = generated
        material.save(update_fields=["generated_json"])
    return moved


SECTION_JOINS_KEY = "section_joins"
# The chunker's marker on a piece of a split passage: "SOLID (Part 2 of 3)".
_PART_SUFFIX = re.compile(
    r"\s*\(\s*part\s+(?P<number>\d+)\s+of\s+(?P<total>\d+)\s*\)\s*$", re.IGNORECASE,
)

_GLOSSARY_SECTION = re.compile(
    r"(?:key\s+)?(?:vocabulary|glossary|terms|words)(?:\s+to\s+(?:know|remember))?"
    r"|definition\s+of\s+terms|key\s+terms|word\s+bank|new\s+words",
    re.IGNORECASE,
)


def _is_glossary_section(section: str) -> bool:
    return bool(_GLOSSARY_SECTION.fullmatch(re.sub(r"[^\w\s]", " ", section or "").strip()))


def release_section_joins(material: LearningMaterial) -> list[int]:
    """Undo the section joins in ``material``'s topic, before a new PDF is matched.

    A part joined its section because no other PDF taught it -- but only the
    PDFs uploaded so far were asked. Uploaded first, PDF 1's "Solid", "Liquid"
    and "Gas" all joined "Matter"; PDF 2's and PDF 3's sections on each state
    then matched that one swollen concept, and the topic lost its three states.
    Released, every part is its own concept again while the new PDF is
    matched, and joining is redone across the whole topic afterwards, so the
    result no longer depends on upload order.

    Only a join still in place is released: a part a teacher has since moved
    elsewhere stays where they put it. Returns the ids released.
    """
    if material.outline_node_id is None:
        return []
    released = []
    topic_materials = LearningMaterial.objects.filter(outline_node_id=material.outline_node_id)
    for other in topic_materials:
        # The caller's own instance is updated, so a later save of it cannot
        # write the old joins back.
        target = material if other.pk == material.pk else other
        joins = (target.generated_json or {}).get(SECTION_JOINS_KEY) or {}
        if not joins:
            continue
        for object_id, joined_group_id in joins.items():
            item = LearningObject.objects.filter(pk=int(object_id), material_id=target.pk).first()
            if item is None or item.group_id != joined_group_id:
                continue
            item.group = LearningObjectGroup.objects.create(
                outline_node_id=target.outline_node_id,
                label=(item.title or "")[:255],
            )
            item.save(update_fields=["group"])
            released.append(item.id)
        generated = target.generated_json or {}
        generated.pop(SECTION_JOINS_KEY, None)
        target.generated_json = generated
        target.save(update_fields=["generated_json"])
    return released


def join_sections_across_topic(material: LearningMaterial) -> list[int]:
    """Join each uncorroborated part to its section, judged against every PDF."""
    if material.outline_node_id is None:
        return []
    moved = []
    for other in LearningMaterial.objects.filter(
        outline_node_id=material.outline_node_id,
        generated_json__learning_objects_confirmed=True,
    ).order_by("id"):
        target = material if other.pk == material.pk else other
        moved.extend(attach_orphan_objects_to_their_section(target))
    return moved


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


def _nominated_candidate(
    matcher,
    learning_object,
    cache,
    *,
    allow_grouped_source=False,
):
    """The single object this one picks as its own cross-PDF equivalent.

    Memoised per refresh: in a lopsided pair of PDFs many objects nominate the
    same partner, so the reciprocal lookup is asked about far fewer objects
    than there are pairs.
    """
    cache_key = (learning_object.id, bool(allow_grouped_source))
    if cache_key not in cache:
        try:
            matcher_kwargs = {
                "section_title": learning_object.section_title,
                "source_object_id": learning_object.id,
            }
            if allow_grouped_source:
                # This is a read-only reciprocal check. It excludes the
                # candidate's current group and asks whether the candidate
                # nominates the new object's group back; no membership changes
                # are made by semantic_decision().
                matcher_kwargs["allow_grouped_source"] = True
            cache[cache_key] = matcher(
                learning_object.material,
                learning_object.title,
                learning_object.content,
                learning_object.kind,
                learning_object.order,
                **matcher_kwargs,
            )
        except Exception:
            # A failed lookup is not evidence against the pair. Record the
            # failure and let the pair through rather than silently emptying
            # the teacher's queue on a transient model error.
            logger.exception(
                "[Grouping] could not check which concept %s matches back; the pair is kept for review  (object %s)",
                name(learning_object.title), learning_object.id,
            )
            cache[cache_key] = "unavailable"
    return cache[cache_key]


def _is_mutual_best_match(
    matcher,
    candidate_object,
    source_object,
    cache,
    *,
    allow_grouped_candidate=False,
):
    """True when the candidate nominates the source back.

    "Both teach the same concept" is a symmetric claim, but the matcher only
    ever asks one direction: every object picks its own closest partner in the
    other PDF, and nothing stops a dozen objects picking the same one. Those
    nominations are mutually exclusive -- at most one of them can be the same
    concept -- so proposing all of them turns one real question into a dozen
    declines. Requiring the nomination to run both ways makes the pair a claim
    about the two objects rather than about one of them.
    """
    decision = _nominated_candidate(
        matcher,
        candidate_object,
        cache,
        allow_grouped_source=allow_grouped_candidate,
    )
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


_GROUPING_OUTCOMES = {
    "grouped": "grouped automatically",
    "review": "sent to teacher review",
    "separate": "too weak, stays its own concept",
    "not matched back": "not sent: the other object matches a different concept better",
    "declined": "the teacher declined this pair before, left as is",
    "teacher decided": "the teacher already decided this pair, left as is",
}


def _log_grouping_outcome(source_object, result, decision=None):
    """One line per learning object: what it was compared with and what happened.

    The ids and timing sit at the end so the line reads as a sentence first
    and can still be traced to the database.
    """
    if result == "already grouped":
        logger.debug("[Grouping] %s is already in a concept with others; left as is  (object %s)",
                     name(source_object.title), source_object.id)
        return
    if not decision:
        logger.info(
            "[Grouping] %s  no similar concept in the other PDFs -> stays its own concept  (object %s)",
            name(source_object.title), source_object.id,
        )
        return
    evidence = decision.get("evidence") or {}
    candidate = decision["candidate"]
    seconds = (evidence.get("elapsed_ms") or 0) / 1000
    logger.info(
        "[Grouping] %s -> %s  %s similar -> %s  (object %s -> %s, %.1fs)",
        name(source_object.title), name(candidate.title), percent(evidence.get("score", 0)),
        _GROUPING_OUTCOMES[result], source_object.id, candidate.id, seconds,
    )


def refresh_learning_object_match_suggestions(material: LearningMaterial) -> None:
    """Persist explainable cross-PDF candidates for this material.

    High-confidence semantic matches have already been scored against every
    member of the candidate group, so every qualifying object may join that
    group. Medium-confidence pair suggestions still require reciprocal
    nomination to avoid flooding the teacher's queue.
    """
    if material.outline_node_id is None:
        return
    # A newly extracted material is still a teacher draft. It cannot create or
    # review cross-PDF connections yet, so loading the CPU semantic models here
    # only delays the upload response and consumes substantial memory. Keep the
    # review queue empty and initialize semantic inference only after the
    # teacher confirms the learning objects.
    if not learning_objects_are_confirmed(material):
        LearningObjectMatchSuggestion.objects.filter(
            Q(source_learning_object__material=material)
            | Q(candidate_learning_object__material=material),
            status=LearningObjectMatchSuggestion.Status.PENDING,
        ).delete()
        return
    # Numbered parts are an explicit authored continuation, not a semantic
    # guess. Reconcile them even when the semantic models are unavailable.
    from .unit_matching import reconcile_numbered_parts
    reconcile_numbered_parts(material.outline_node)
    from . import semantic_grouping
    semantic_runtime = None
    if semantic_grouping.mode() != "legacy":
        try:
            semantic_grouping.policy()
            semantic_runtime = semantic_grouping.runtime()
        except Exception as exc:
            logger.exception("[Grouping] PDF %s  the similarity model is unavailable; the review queue is left as it was", material.id)
            data = dict(material.generated_json or {})
            data["grouping_warning"] = f"Connections could not be evaluated for {material.title}: {exc}"
            material.generated_json = data
            material.save(update_fields=["generated_json"])
            return
    retained_ids = []
    reciprocal_cache = {}
    learning_objects = list(
        material.learning_objects.select_related("material", "group").order_by("order", "id")
    )
    semantic_active = semantic_grouping.mode() != "legacy"
    if semantic_active:
        try:
            cached_count = semantic_grouping.precompute_embeddings(
                (item.content for item in learning_objects),
                runtime_instance=semantic_runtime,
            )
            logger.info(
                "[Grouping] PDF %s  cached embeddings for %s confirmed learning objects",
                material.id,
                cached_count,
            )
        except Exception as exc:
            logger.exception(
                "[Grouping] PDF %s  confirmed embeddings could not be cached; the review queue is left as it was",
                material.id,
            )
            data = dict(material.generated_json or {})
            data["grouping_warning"] = f"Confirmed content could not be prepared for comparison for {material.title}: {exc}"
            material.generated_json = data
            material.save(update_fields=["generated_json"])
            return
    matcher = semantic_grouping.semantic_decision if semantic_active else _match_decision
    started = perf_counter()
    tally = Counter()

    def outcome(source_object, result, decision=None):
        tally[result] += 1
        _log_grouping_outcome(source_object, result, decision)

    logger.info(
        "[Grouping] PDF %s  comparing %s learning objects with the other PDFs in this topic",
        material.id, len(learning_objects),
    )
    for source_object in learning_objects:
        if semantic_active and source_object.group_id and LearningObject.objects.filter(
                group_id=source_object.group_id).exclude(pk=source_object.id).exists():
            # Keep existing groups intact during background refresh. Changes to
            # membership must come from an explicit teacher action.
            retained_ids.extend(LearningObjectMatchSuggestion.objects.filter(
                Q(source_learning_object=source_object) | Q(candidate_learning_object=source_object)
            ).values_list("id", flat=True))
            outcome(source_object, "already grouped")
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
        except Exception as exc:
            if not semantic_active:
                raise
            # An inference/cache failure is not a low-confidence decision.
            # In particular, do not delete existing pending suggestions below.
            logger.exception("[Grouping] PDF %s  the similarity check failed partway; the rest of the review queue is left as it was", material.id)
            data = dict(material.generated_json or {})
            data["grouping_warning"] = f"Connections could not be fully evaluated for {material.title}: {exc}"
            material.generated_json = data
            material.save(update_fields=["generated_json"])
            return
        if not decision or decision["confidence"] is None:
            outcome(source_object, "separate", decision)
            continue
        candidate_object = decision["candidate"]
        source, candidate = _canonical_match_pair(source_object, candidate_object)
        existing = LearningObjectMatchSuggestion.objects.filter(
            source_learning_object=source,
            candidate_learning_object=candidate,
        ).first()
        if existing and (existing.evidence or {}).get("teacher_reviewed"):
            retained_ids.append(existing.id)
            outcome(source_object, "teacher decided", decision)
            continue
        is_high_confidence = (
            decision["confidence"]
            == LearningObjectMatchSuggestion.Confidence.HIGH
        )
        if not is_high_confidence and not _is_mutual_best_match(
            matcher,
            candidate_object,
            source_object,
            reciprocal_cache,
            # A medium match to an established group used to vanish here:
            # semantic_decision refused to evaluate the grouped candidate, so
            # reciprocity could never succeed. Allowing this read-only lookup
            # preserves mutual-best protection and creates review work only
            # when the grouped candidate genuinely nominates the source back.
            allow_grouped_candidate=semantic_active,
        ):
            outcome(source_object, "not matched back", decision)
            continue
        if (
            is_high_confidence
            and candidate_object.group_id
            and (not existing or existing.status != LearningObjectMatchSuggestion.Status.REJECTED)
            and source_object.group_id != candidate_object.group_id
        ):
            old_group_id = source_object.group_id
            source_object.group_id = candidate_object.group_id
            source_object.mark_grouping_current()
            source_object.save(update_fields=["group", "grouping_content_hash"])
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
        outcome(
            source_object,
            "declined" if status_value == LearningObjectMatchSuggestion.Status.REJECTED
            else "grouped" if same_group else "review",
            decision,
        )

    logger.info(
        "[Grouping] PDF %s  done: %s grouped automatically, %s sent to teacher review, %s stay separate  (%s)",
        material.id,
        tally["grouped"],
        tally["review"],
        tally["separate"] + tally["not matched back"],
        took(started),
    )
    stale_pending = LearningObjectMatchSuggestion.objects.filter(
        Q(source_learning_object__material=material)
        | Q(candidate_learning_object__material=material),
        status=LearningObjectMatchSuggestion.Status.PENDING,
    )
    if retained_ids:
        stale_pending = stale_pending.exclude(id__in=retained_ids)
    stale_pending.delete()
    if (material.generated_json or {}).get("grouping_warning"):
        data = dict(material.generated_json)
        data.pop("grouping_warning", None)
        material.generated_json = data
        material.save(update_fields=["generated_json"])

    if semantic_active:
        from .unit_matching import refresh_heading_unit_suggestions
        try:
            refresh_heading_unit_suggestions(material.outline_node)
        except Exception:  # noqa: BLE001 -- unit proposals must never break grouping
            logger.exception("[Grouping] matching section headings across PDFs could not be refreshed")


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


QUESTION_PAIRING_METHOD = "sbert_concept"


def question_pairing_debug_configuration() -> dict:
    """Cut-offs for pairing a printed question to the concept it asks about.

    Scores are sentence-encoder cosines between the question and a concept's
    text. Confirming needs both a high score and a clear lead over the
    runner-up concept; anything closer waits for the teacher.
    """
    auto_threshold = _env_score("QUESTION_PAIR_AUTO_THRESHOLD", 0.55)
    return {
        "method": QUESTION_PAIRING_METHOD,
        "weights": {},
        "thresholds": {
            "auto_confirm": auto_threshold,
            "teacher_review": min(auto_threshold, _env_score("QUESTION_PAIR_REVIEW_THRESHOLD", 0.30)),
            "minimum_margin": _env_score("QUESTION_PAIR_MINIMUM_MARGIN", 0.05),
        },
    }


# The label a printed question is numbered with: "Question 1", "2.", "3)".
_QUESTION_NUMBER = re.compile(r"^\s*(?:question\s*)?\d+\s*(?:[.)-]\s*|$)", re.I)


def is_empty_prompt(prompt: str) -> bool:
    """True for a printed "question" with nothing to ask once its number is gone.

    Extraction stores the "Question 1" heading above a question as a question
    of its own, and sometimes an empty prompt. Neither can be paired.
    """
    return not re.sub(r"[\W_]+", "", _QUESTION_NUMBER.sub("", prompt or ""))


def _question_encoder():
    """The sentence encoder grouping uses; unavailable while it is switched off."""
    from . import semantic_grouping

    if semantic_grouping.mode() == "legacy":
        raise semantic_grouping.SemanticUnavailable("semantic grouping is switched off")
    return semantic_grouping.runtime()


def _rank_concepts(question_vector, members):
    """``[(score, learning_object)]``, one per concept, best first.

    A concept scores its best-matching object: a question asks about one part
    of a concept, so averaging would mark a concept down for teaching more
    than the question covers.
    """
    best = {}
    for learning_object, vector in members:
        score = sum(a * b for a, b in zip(question_vector, vector))
        key = learning_object.group_id or f"object:{learning_object.id}"
        if key not in best or score > best[key][0]:
            best[key] = (score, learning_object)
    return sorted(best.values(), key=lambda row: (-row[0], row[1].order, row[1].id))


# The link a generated question is born with. It records which learning object
# the question was written from, so it is a fact, not a pairing guess.
GENERATED_PAIRING_METHOD = "generated_from_object"


def _questions_open_to_pairing(material: LearningMaterial) -> list[Question]:
    """The questions lexical pairing may re-decide, after settling generated ones.

    Pairing exists to guess which object an *extracted* question belongs to, so
    re-guessing is safe for those. A generated question is different: it was
    written from one specific object. Re-pairing it by word overlap used to
    throw that away -- most generated questions then scored below the
    confirmation threshold, lost their approval, and had their learner-facing
    copies deleted, for every concept in the file at once.

    So a question holding a generated link is never re-paired, and a generated
    question is never lexically paired at all. A generated question with no
    links left was written from an object that has since been deleted; it has
    no concept to belong to, so it is removed here -- and only it.
    """
    questions = list(material.questions.order_by("order", "id"))
    protected_ids = set(
        QuestionLearningObjectLink.objects.filter(
            question__material=material,
            method=GENERATED_PAIRING_METHOD,
        ).values_list("question_id", flat=True)
    )
    linked_ids = set(
        QuestionLearningObjectLink.objects.filter(
            question__material=material,
        ).values_list("question_id", flat=True)
    )

    open_questions, orphaned = [], []
    for question in questions:
        if question.id in protected_ids:
            continue
        if question.source_type == Question.SourceType.GENERATED:
            if question.id not in linked_ids:
                orphaned.append(question)
            continue
        open_questions.append(question)

    if orphaned:
        from question_generation.models import GeneratedQuestion

        adaptive_ids = [item.adaptive_question_id for item in orphaned if item.adaptive_question_id]
        Question.objects.filter(pk__in=[item.id for item in orphaned]).delete()
        if adaptive_ids:
            GeneratedQuestion.objects.filter(pk__in=adaptive_ids).delete()
        logger.info(
            "[Questions] PDF %s  removed %s generated question(s) whose learning object was deleted",
            material.id, len(orphaned),
        )
    return open_questions


def refresh_question_learning_object_links(material: LearningMaterial) -> None:
    """Auto-confirm strong pairs and preserve teacher decisions for uncertain ones."""
    questions = _questions_open_to_pairing(material)
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
    if not learning_objects:
        QuestionLearningObjectLink.objects.filter(
            question__in=questions,
        ).exclude(review_status__in=TEACHER_QUESTION_PAIRING_STATUSES).delete()
        return

    from . import semantic_grouping
    from .question_workflow import sync_question_to_adaptive

    open_questions = []
    for question in questions:
        existing = question.learning_object_links.order_by("-is_primary", "-relevance_score", "id").first()
        if existing and existing.review_status in TEACHER_QUESTION_PAIRING_STATUSES:
            continue
        if is_empty_prompt(question.prompt):
            # Nothing to pair: it stays unpaired, and out of the learners' bank.
            question.learning_object_links.all().delete()
            sync_question_to_adaptive(question)
            continue
        open_questions.append(question)
    if not open_questions:
        return

    # Scored by meaning against each concept's text: a question and the
    # passage that answers it often share few words, so word overlap paired
    # them with whichever object repeated the question's vocabulary.
    try:
        engine = _question_encoder()
        members = [item for item in learning_objects if engine.supports(item.content)]
        member_vectors = engine.embeddings([item.content for item in members])
        prompts = [question for question in open_questions if engine.supports(question.prompt)]
        question_vectors = engine.embeddings([question.prompt for question in prompts])
    except semantic_grouping.SemanticUnavailable as exc:
        logger.warning(
            "[Questions] PDF %s  printed questions left as they are: %s", material.id, exc,
        )
        return

    thresholds = question_pairing_debug_configuration()["thresholds"]
    candidates = list(zip(members, member_vectors))
    for question, vector in zip(prompts, question_vectors):
        ranked = _rank_concepts(vector, candidates)
        if not ranked:
            continue
        score, best = ranked[0]
        margin = score - ranked[1][0] if len(ranked) > 1 else score
        if score >= thresholds["auto_confirm"] and margin >= thresholds["minimum_margin"]:
            review_status = QuestionLearningObjectLink.ReviewStatus.AUTO_CONFIRMED
        elif score >= thresholds["teacher_review"]:
            review_status = QuestionLearningObjectLink.ReviewStatus.PENDING_REVIEW
        else:
            review_status = QuestionLearningObjectLink.ReviewStatus.UNMATCHED
        question.learning_object_links.all().delete()
        QuestionLearningObjectLink.objects.create(
            question=question,
            learning_object=best,
            relevance_score=round(score, 6),
            method=QUESTION_PAIRING_METHOD,
            is_primary=review_status == QuestionLearningObjectLink.ReviewStatus.AUTO_CONFIRMED,
            review_status=review_status,
        )
        question.refresh_from_db()
        sync_question_to_adaptive(question)
        logger.debug(
            "Question pairing: question=%s learning_object=%s group=%s score=%.4f "
            "margin=%.4f status=%s",
            question.id, best.id, best.group_id, score, margin, review_status,
        )


@transaction.atomic
def synchronize_detected_questions(material: LearningMaterial, classified_blocks: list[dict]) -> None:
    """Synchronize derived questions and rebuild their content-pair relationships."""
    existing = {
        question.content_fingerprint or question_fingerprint(question.prompt): question
        for question in material.questions.all()
    }
    retained_ids = []
    payloads = detected_question_payloads(classified_blocks)
    if payloads:
        logger.info(
            "[Upload] PDF %s  %s question(s) printed in the PDF found; labelled later, in the Questions step",
            material.id, len(payloads),
        )
    for order, payload in enumerate(payloads):
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
            # The same text, so a label it already has still holds; the
            # payload's labels are blank only because labelling is deferred.
            fields = [
                field for field, value in payload.items()
                if not (field in LABEL_FIELDS and not value)
            ]
            for field in fields:
                setattr(question, field, payload[field])
            question.order = order
            question.save(update_fields=[*fields, "order"])
        retained_ids.append(question.id)
    # Only questions this sync owns -- the ones extracted from the PDF -- can go
    # stale here. Generated and manually written questions never appear in the
    # PDF's blocks, so treating their absence as removal deleted every one of
    # them whenever a block's classification was changed.
    material.questions.filter(
        source_type=Question.SourceType.PDF,
    ).exclude(id__in=retained_ids).delete()
    refresh_question_learning_object_links(material)


def refresh_material_learning_relationships(material: LearningMaterial) -> None:
    """Refresh neutral groups and pairs after teacher edits to learning objects."""
    if not learning_objects_are_confirmed(material):
        # A draft is never matched against other PDFs, and its edits must not
        # reach the approved ones: releasing and redoing section joins works
        # on every PDF in the topic. The draft only keeps its own neutral
        # groups and question pairs until the teacher confirms it.
        ensure_learning_object_groups(material)
        remove_empty_learning_object_groups(material)
        refresh_question_learning_object_links(material)
        return
    # Parts joined to their sections are separated again first, so this PDF
    # is matched against the real "Solid", not a "Matter" that swallowed it.
    release_section_joins(material)
    ensure_learning_object_groups(material)
    # After cross-PDF matching has settled, so only genuine leftovers are seen,
    # and across every PDF in the topic, so upload order does not decide it.
    join_sections_across_topic(material)
    remove_empty_learning_object_groups(material)
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
