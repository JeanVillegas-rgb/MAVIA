from __future__ import annotations

import contextlib
import io
import logging
import os
import re
from dataclasses import dataclass, field

import fitz

from lessons.models import CourseGroup, OutlineNode


logger = logging.getLogger(__name__)


@dataclass
class ParsedOutlineNode:
    title: str
    depth: int
    order: int
    related_info: dict = field(default_factory=dict)
    children: list[ParsedOutlineNode] = field(default_factory=list)


def _normalize_line(line: str) -> str:
    line = line.strip()
    line = re.sub(r"^(?:â€¢|â—|[•●\-\*\+])\s+", "", line)
    line = re.sub(r"^[\-\*\+•●]\s+", "", line)
    line = re.sub(r"^\d+[\.\)]\s+", "", line)
    line = re.sub(r"^#+\s*", "", line)
    return line.strip()


_OUTLINE_TITLE_PREFIX_RE = re.compile(
    r"^(?:module|unit|chapter|lesson|section|topic)\s*\d*(?:\.\d+)*\s*[:\-–]\s*",
    re.IGNORECASE,
)

_NOISE_PATTERNS = [
    r"\btitle page\b",
    r"\btable of contents\b",
    r"\bcontents\b",
    r"\bcourse outline\b",
    r"\bcredit units?\b",
    r"\babstract\b",
    r"\backnowledg(e)?ment\b",
    r"\blist of tables?\b",
    r"\blist of figures?\b",
    r"\breferences?\b",
    r"\bappendix\b",
    r"\bcurriculum vitae\b",
    r"\bstudent\b",
    r"\buniversity\b",
    r"\bcollege\b",
    r"\bcourse code\b",
    r"\bacademic year\b",
    r"\bsemester\b",
    r"\bsyllabus\b",
    r"\bself[-\s]?introduction\b",
    r"\bexpectations?\b",
    r"\bclass discussion\b",
    r"\bgroup discussion\b",
    r"\bseatwork\b",
    r"\bclassroom orientation\b",
    r"\bclassroom rules?\b",
    r"\bsetting of classroom\b",
    r"\bquiz\b",
    r"\bassignment\b",
    r"\bactivity\b",
    r"\bperformance task\b",
    r"\bassessment\b",
    r"\bshort test\b",
    r"\bteacher\b",
    r"\blearner\b",
    r"\bgrading\b",
    r"\bk to 12\b",
    r"\bk-12\b",
    r"\bgrade level\b",
    r"\btime allotment\b",
    r"\blearning competency\b",
    r"\blearning competencies\b",
    r"\blearning focus\b",
    r"\blearning objectives?\b",
    r"\bobjectives?\b",
    r"\bcompetencies\b",
    r"\bcourse orientation\b",
    r"\bcourse requirements\b",
    r"\bmidterm assessment\b",
    r"\bfinal examination\b",
    r"\bportfolio presentation\b",
    r"\blesson planning workshop\b",
    r"\bvmgo\b",
]

_NON_TITLE_VERBS = {
    "classify",
    "conduct",
    "construct",
    "describe",
    "design",
    "develop",
    "differentiate",
    "evaluate",
    "explain",
    "identify",
    "integrate",
    "interpret",
    "prepare",
    "present",
    "promote",
    "recognize",
    "select",
    "understand",
}

_NON_TOPIC_START_RE = re.compile(
    r"^(?:at the end|after this|students?\s+(?:will|should|are|can)|learners?\s+(?:will|should|are|can)|"
    r"pupils?\s+(?:will|should|are|can)|teacher\s+will|the\s+teacher|the\s+student|the\s+learner)\b",
    re.IGNORECASE,
)

_STRUCTURAL_TITLE_RE = re.compile(
    r"^(?P<prefix>module|unit|chapter|lesson|section|topic)\s*(?P<number>\d+(?:\.\d+)*)?\s*[:\-â€“]?\s*(?P<title>.*)$",
    re.IGNORECASE,
)


def _clean_topic_title(title: str) -> str:
    title = re.sub(r"\s+", " ", title).strip(" \t\r\n:;,-–")
    title = _OUTLINE_TITLE_PREFIX_RE.sub("", title).strip(" \t\r\n:;,-–")
    return title


def _clean_related_info(value) -> dict:
    if not isinstance(value, dict):
        return {}

    cleaned = {}
    for key, item in value.items():
        clean_key = re.sub(r"\s+", "_", str(key).strip().lower())
        if not clean_key:
            continue
        if isinstance(item, str):
            clean_value = re.sub(r"\s+", " ", item).strip()
            if clean_value:
                cleaned[clean_key] = clean_value
        elif isinstance(item, list):
            clean_items = [
                re.sub(r"\s+", " ", str(entry)).strip()
                for entry in item
                if re.sub(r"\s+", " ", str(entry)).strip()
            ]
            if clean_items:
                cleaned[clean_key] = clean_items
        elif isinstance(item, dict):
            nested = _clean_related_info(item)
            if nested:
                cleaned[clean_key] = nested
    return cleaned


def _looks_like_real_outline_item(title: str, raw_line: str) -> bool:
    lowered = title.lower().strip()
    raw_lower = raw_line.lower().strip()

    if not title:
        return False
    if len(title) < 3:
        return False
    if any(re.search(pattern, lowered) for pattern in _NOISE_PATTERNS):
        return False
    if "|" in title and any(word in raw_lower for word in ["university", "college", "student", "author", "bs "]):
        return False
    if lowered.startswith(("page ", "p.", "http", "www")):
        return False
    if re.fullmatch(r"(?:week|wk)\s*\d+[a-z]?", lowered):
        return False
    if re.fullmatch(r"(?:day|session|meeting)\s*\d+[a-z]?", lowered):
        return False
    if re.fullmatch(r"\d+\s*[-–]\s*\d+", lowered):
        return False
    if re.fullmatch(r"[ivxlcdm]+", lowered):
        return False
    if re.fullmatch(r"\d+", lowered):
        return False

    # Keep lines that look like hierarchy items, not document chrome.
    has_outline_markers = bool(
        re.search(r"^(chapter|unit|lesson|module|section|topic)\b", raw_lower)
        or re.search(r"^\d+(?:\.\d+)*\s+", raw_line.strip())
        or any(token in lowered for token in ["introduction", "methodology", "results", "conclusion", "recommendations"])
    )

    # A small number of general content titles without obvious chrome are also valid.
    return has_outline_markers


def _is_valid_outline_topic_title(title: str) -> bool:
    if not title:
        return False
    if _looks_like_fragment_or_non_topic(title):
        return False
    if any(re.search(pattern, title.lower()) for pattern in _NOISE_PATTERNS):
        return False
    return not _looks_like_schedule_or_admin_label(title)


def _normalized_title_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _has_source_evidence(title: str, source_text: str) -> bool:
    normalized_title = _normalized_title_text(title)
    if not normalized_title:
        return False
    normalized_source = _normalized_title_text(source_text)
    if normalized_title in normalized_source:
        return True

    words = normalized_title.split()
    if len(words) >= 3:
        return " ".join(words[:3]) in normalized_source
    return False


def _looks_like_fragment_or_non_topic(title: str) -> bool:
    lowered = title.lower().strip()
    words = re.findall(r"[A-Za-z0-9]+", title)
    letters = re.findall(r"[A-Za-z]", title)

    if len(title.strip()) < 3:
        return True
    if len(letters) < 2:
        return True
    if len(words) == 1 and len(words[0]) <= 2:
        return True
    if re.fullmatch(r"[A-Z]{1,4}", title.strip()):
        return True
    if len(words) > 14:
        return True
    if words and words[0].casefold() in _NON_TITLE_VERBS:
        return True
    if _NON_TOPIC_START_RE.search(lowered):
        return True
    if lowered.startswith(("to ", "by ", "given ", "using ", "activity ", "output ", "performance ")):
        return True
    return False


def _looks_like_schedule_or_admin_label(title: str) -> bool:
    lowered = title.lower().strip()
    return bool(
        re.fullmatch(r"(?:week|wk)\s*\d+[a-z]?", lowered)
        or re.fullmatch(r"(?:day|session|meeting)\s*\d+[a-z]?", lowered)
        or re.fullmatch(r"\d+\s*[-–]\s*\d+", lowered)
        or re.fullmatch(r"\d+", lowered)
    )


def _leading_depth(line: str) -> int:
    stripped = line.expandtabs(4)
    if stripped.startswith("#"):
        return stripped.count("#", 0, 6) - 1
    indent = len(stripped) - len(stripped.lstrip(" "))
    return max(indent // 2, 0)


def _parse_outline_lines(lines: list[str]) -> list[ParsedOutlineNode]:
    roots: list[ParsedOutlineNode] = []
    stack: list[ParsedOutlineNode] = []
    sibling_counts: dict[tuple[int | None, int], int] = {}

    for raw_line in lines:
        if not raw_line.strip():
            continue

        title = _clean_topic_title(_normalize_line(raw_line))
        if not title or title.lower() in {"table of contents", "course outline", "outline"}:
            continue
        if not _looks_like_real_outline_item(title, raw_line):
            continue

        depth = _leading_depth(raw_line)
        count_key = (id(stack[-1]) if stack else None, depth)
        order = sibling_counts.get(count_key, 0)
        sibling_counts[count_key] = order + 1

        node = ParsedOutlineNode(title=title, depth=depth, order=order)

        while stack and stack[-1].depth >= depth:
            stack.pop()

        if stack:
            stack[-1].children.append(node)
        else:
            roots.append(node)

        stack.append(node)

    return roots


def _dedupe_outline_nodes(nodes: list[ParsedOutlineNode]) -> list[ParsedOutlineNode]:
    def visit(items: list[ParsedOutlineNode]) -> list[ParsedOutlineNode]:
        seen_titles: set[str] = set()
        kept = []
        for item in items:
            key = item.title.casefold()
            if key in seen_titles:
                continue
            seen_titles.add(key)
            item.children = visit(item.children)
            kept.append(item)
        return kept

    return visit(nodes)


def _flatten_outline_nodes(nodes: list[ParsedOutlineNode]) -> list[ParsedOutlineNode]:
    flattened = []
    for node in nodes:
        flattened.append(node)
        flattened.extend(_flatten_outline_nodes(node.children))
    return flattened


def _normalize_outline_tree(nodes: list[ParsedOutlineNode], depth: int = 0) -> list[ParsedOutlineNode]:
    for index, node in enumerate(nodes):
        node.depth = depth
        node.order = index
        node.children = _normalize_outline_tree(node.children, depth + 1)
    return nodes


def _max_tree_depth(nodes: list[ParsedOutlineNode]) -> int:
    if not nodes:
        return 0
    return max(max(node.depth, _max_tree_depth(node.children)) for node in nodes)


def _flatten_descendants(nodes: list[ParsedOutlineNode]) -> list[ParsedOutlineNode]:
    flattened = []
    for node in nodes:
        flattened.append(ParsedOutlineNode(title=node.title, depth=1, order=len(flattened)))
        flattened.extend(_flatten_descendants(node.children))
    return flattened


def _looks_like_accidental_chain(nodes: list[ParsedOutlineNode]) -> bool:
    if len(nodes) != 1:
        return False
    max_depth = _max_tree_depth(nodes)
    descendant_count = len(_flatten_descendants(nodes[0].children))
    return max_depth > 2 and descendant_count >= 4


def _flatten_accidental_chain(nodes: list[ParsedOutlineNode]) -> list[ParsedOutlineNode]:
    if not _looks_like_accidental_chain(nodes):
        return nodes
    root = nodes[0]
    root.children = _flatten_descendants(root.children)
    return _normalize_outline_tree([root])


def _strip_grade_suffix(title: str) -> str:
    return re.sub(r"\s*\(grade\s+\d+\)\s*$", "", title, flags=re.IGNORECASE).strip()


def _is_table_chrome_line(line: str) -> bool:
    lowered = line.lower().strip()
    return lowered in {
        "week",
        "weeks",
        "topics / content",
        "learning focus",
        "activities / output",
    }


def _is_outline_boundary_line(line: str) -> bool:
    lowered = line.lower().strip()
    return bool(
        _is_table_chrome_line(line)
        or re.fullmatch(r"week\s*\d+", lowered)
        or re.fullmatch(r"weeks?\s*\d+\s*[–-]\s*\d+", lowered)
        or re.fullmatch(r"\d+(?:st|nd|rd|th)?", lowered)
        or lowered.startswith(("grade ", "class discussion", "group discussion", "hands-on", "worksheet"))
        or lowered.startswith(("small-group", "concept map", "demonstration", "peer review", "research"))
        or lowered.startswith(("community-based", "draft lesson", "final lesson"))
    )


def _collect_wrapped_title(lines: list[str], start_index: int) -> tuple[str, int]:
    title = lines[start_index].strip()
    index = start_index + 1
    while index < len(lines):
        candidate = lines[index].strip()
        lowered = candidate.lower()
        if not candidate:
            index += 1
            continue
        if re.search(r"^(?:module|unit|chapter|lesson|section|topic)\s*\d*\s*:", lowered):
            break
        if re.search(r"^[•●\-\*]\s*lesson\s+\d+\s*:", lowered):
            break
        if _is_outline_boundary_line(candidate):
            break
        if len(candidate) > 120:
            break
        if not _looks_like_wrapped_title_continuation(title, candidate):
            break
        title = f"{title} {candidate}".strip()
        index += 1
        if re.search(r"\)\s*$", title):
            break
    return title, index


_OUTLINE_ITEM_START_RE = re.compile(
    r"^(?:[•●\-\*]\s*)?(?:module|unit|chapter|lesson|section|topic)\s*\d*(?:\.\d+)*\s*[:\-–]?",
    re.IGNORECASE,
)

_BULLET_LINE_RE = re.compile(r"^(?:â€¢|â— |\?{1,4}|[^\x00-\x7F]{1,4}|[•●\-\*\+])\s*(.+)$")


def _title_has_dangling_end(title: str) -> bool:
    stripped = title.strip()
    if not stripped:
        return False
    lowered = stripped.lower()
    words = re.findall(r"[A-Za-z0-9]+", stripped)
    if stripped.count("(") > stripped.count(")"):
        return True
    if stripped.endswith((":", "-", "–", ",")):
        return True
    if not words:
        return False
    if " that " in f" {lowered} " and not any(v in lowered for v in [" undergo", " cause", " work", " affect", " occur"]):
        return True
    connectors = {
        "and",
        "or",
        "of",
        "in",
        "to",
        "for",
        "with",
        "among",
        "between",
        "within",
        "through",
        "using",
        "non",
        "that",
        "due",
        "based",
        "the",
        "a",
        "an",
        "by",
        "from",
        "about",
        "into",
        "on",
        "at",
        "as",
        "their",
        "its",
        "which",
        "vs",
        "versus",
    }
    if words[-1].casefold() in connectors or lowered.endswith((" non-", " non")):
        return True
    if len(words) >= 2 and words[-2].casefold() in {"and", "or", "of", "in", "to", "for", "with", "between", "among", "vs", "versus"}:
        return True
    last_word = words[-1].casefold()
    if len(last_word) >= 4 and last_word.endswith(("ive", "al", "ic", "ful", "ous", "less", "able", "ible", "ary")):
        return True
    return False


def _looks_like_plain_outline_title_start(line: str) -> bool:
    title = _clean_topic_title(_normalize_line(line))
    if not title or len(title) > 120:
        return False
    if _looks_like_schedule_or_admin_label(title):
        return False
    lowered = title.lower()
    if any(re.search(pattern, lowered) for pattern in _NOISE_PATTERNS):
        return False
    structural_line = bool(_STRUCTURAL_TITLE_RE.match(line.strip()))
    if structural_line:
        return True
    if _title_has_dangling_end(title):
        return False
    words = re.findall(r"[A-Za-z0-9]+", title)
    if len(words) == 1:
        return False
    return bool(
        1 <= len(words) <= 8
        and title[:1].isupper()
        and not title.endswith((".", ";", "?"))
        and not _NON_TOPIC_START_RE.search(lowered)
        and (not words or words[0].casefold() not in _NON_TITLE_VERBS)
    )


def _looks_like_title_continuation(current_title: str, candidate: str) -> bool:
    words = candidate.split()
    if not words or len(words) > 10:
        return False
    if _is_outline_boundary_line(candidate):
        return False
    if _STRUCTURAL_TITLE_RE.search(candidate) or _BULLET_LINE_RE.search(candidate) or re.match(r"^(?:[•●\-\*\+]|\d+[\.)])\s*", candidate):
        return False
    first_word = words[0].strip(",:;()").casefold()
    if first_word in _NON_TITLE_VERBS and not _title_has_dangling_end(current_title):
        return False
    if candidate[:1].islower():
        return True
    if len(words) == 1 and not current_title.rstrip().endswith((".", ";", ":")):
        return True

    if (
        current_title
        and not current_title.rstrip().endswith((".", ";", ":", "?", "!", "-", "–"))
        and len(current_title.split()) <= 12
        and 1 <= len(words) <= 10
        and candidate[0].isupper()
        and not _NON_TOPIC_START_RE.search(candidate.lower())
    ):
        return True

    return True


def _looks_like_wrapped_title_continuation(current_title: str, candidate: str) -> bool:
    candidate = candidate.strip()
    if not candidate:
        return False
    lowered = candidate.lower()
    words = re.findall(r"[A-Za-z0-9]+", candidate)
    if not words or len(words) > 10:
        return False
    if _is_outline_boundary_line(candidate):
        return False
    if any(re.search(pattern, lowered) for pattern in _NOISE_PATTERNS):
        return False
    if _STRUCTURAL_TITLE_RE.search(candidate) or _BULLET_LINE_RE.search(candidate) or re.match(r"^(?:[•●\-\*\+]|\d+[\.)])\s*", candidate):
        return False
    if lowered.startswith(("learning focus", "activities", "activity", "output", "remarks", "assessment")):
        return False
    if _NON_TOPIC_START_RE.search(lowered):
        return False
    if candidate.endswith((".", ";", "?")) and not _title_has_dangling_end(current_title):
        return False
    if _looks_like_schedule_or_admin_label(candidate):
        return False

    first_word = words[0].casefold()
    if first_word in _NON_TITLE_VERBS and not _title_has_dangling_end(current_title):
        return False

    if not current_title:
        return True
    if current_title.rstrip().endswith((".", ";", "?", "!")):
        return False

    if candidate[:1].islower():
        return True

    if _title_has_dangling_end(current_title):
        return True

    is_module_title = bool(re.search(r"^(?:module|unit|chapter)\b", current_title, flags=re.IGNORECASE))
    if is_module_title:
        title_part = re.sub(r"^(?:module|unit|chapter)\s*\d*(?:\.\d+)*\s*[:\-–]?\s*", "", current_title, flags=re.IGNORECASE).strip()
        title_words = re.findall(r"[A-Za-z0-9]+", title_part)
        return len(title_words) <= 1

    is_lesson_title = bool(re.search(r"^(?:lesson|section|topic)\b", current_title, flags=re.IGNORECASE))
    if is_lesson_title:
        title_part = re.sub(r"^(?:lesson|section|topic)\s*\d*(?:\.\d+)*\s*[:\-–]?\s*", "", current_title, flags=re.IGNORECASE).strip()
        title_words = re.findall(r"[A-Za-z0-9]+", title_part)
        return len(title_words) <= 2

    current_words = re.findall(r"[A-Za-z0-9]+", current_title)
    return len(current_words) <= 2


def _merge_wrapped_outline_candidate_lines(lines: list[str]) -> list[str]:
    merged: list[str] = []
    index = 0
    while index < len(lines):
        line = re.sub(r"\s+", " ", lines[index]).strip()
        if not line:
            index += 1
            continue

        structural = _STRUCTURAL_TITLE_RE.match(line)
        bullet = _BULLET_LINE_RE.match(line)
        plain_start = _looks_like_plain_outline_title_start(line)
        dangling_start = _title_has_dangling_end(line)
        if not structural and not bullet and not plain_start and not dangling_start:
            merged.append(line)
            index += 1
            continue

        prefix = ""
        title = line
        if structural:
            prefix_parts = [structural.group("prefix")]
            if structural.group("number"):
                prefix_parts.append(structural.group("number"))
            prefix = " ".join(prefix_parts)
            title = (structural.group("title") or "").strip()
        elif bullet:
            prefix = line[: line.find(bullet.group(1))]
            title = bullet.group(1).strip()
        else:
            title = line.strip()

        parts = [title] if title else []
        next_index = index + 1
        while next_index < len(lines):
            candidate = re.sub(r"\s+", " ", lines[next_index]).strip()
            current_title = f"{prefix}: {' '.join(parts)}".strip() if structural else " ".join(parts).strip()

            if (
                not structural
                and _looks_like_plain_outline_title_start(candidate)
                and not _title_has_dangling_end(current_title)
            ):
                break

            if not _looks_like_wrapped_title_continuation(current_title, candidate):
                break
            parts.append(_normalize_line(candidate).strip())
            next_index += 1

        combined_title = " ".join(part for part in parts if part).strip()
        if structural:
            separator = ": " if combined_title else ":"
            merged.append(f"{prefix}{separator}{combined_title}".strip())
        elif bullet:
            merged.append(f"{prefix}{combined_title}".strip())
        else:
            merged.append(combined_title)
        index = max(next_index, index + 1)
    return merged


def _collect_wrapped_outline_item(
    lines: list[str],
    start_index: int,
    strip_pattern: str,
) -> tuple[str, int]:
    title = re.sub(strip_pattern, "", lines[start_index].strip(), flags=re.IGNORECASE).strip()
    index = start_index + 1
    while index < len(lines):
        candidate = lines[index].strip()
        if not candidate:
            index += 1
            continue
        if _OUTLINE_ITEM_START_RE.search(candidate) or _BULLET_LINE_RE.search(candidate):
            break
        if _is_outline_boundary_line(candidate) or len(candidate) > 120:
            break
        if not _looks_like_wrapped_title_continuation(title, candidate):
            break
        title = f"{title} {candidate}".strip()
        index += 1
    return title, index


def _extract_module_lesson_outline(text: str) -> list[ParsedOutlineNode]:
    raw_lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in raw_lines if line and not _is_table_chrome_line(line)]
    modules: list[ParsedOutlineNode] = []
    module_by_title: dict[str, ParsedOutlineNode] = {}
    lesson_keys_by_module: dict[str, set[str]] = {}
    current_module: ParsedOutlineNode | None = None
    index = 0

    while index < len(lines):
        line = lines[index]
        module_match = re.search(r"^module\s+\d+\s*:\s*(.+)$", line, flags=re.IGNORECASE)
        if module_match:
            collected, next_index = _collect_wrapped_title(lines, index)
            title = _clean_topic_title(re.sub(r"^module\s+\d+\s*:\s*", "", collected, flags=re.IGNORECASE))
            title = _strip_grade_suffix(title)
            if title:
                key = title.casefold()
                current_module = module_by_title.get(key)
                if current_module is None:
                    current_module = ParsedOutlineNode(title=title, depth=0, order=len(modules))
                    modules.append(current_module)
                    module_by_title[key] = current_module
                    lesson_keys_by_module[key] = set()
            index = next_index
            continue

        lesson_match = re.search(r"^[•●\-\*]?\s*lesson\s+\d+\s*:\s*(.+)$", line, flags=re.IGNORECASE)
        if lesson_match and current_module is not None:
            collected, next_index = _collect_wrapped_title(lines, index)
            title = _clean_topic_title(re.sub(r"^[•●\-\*]?\s*lesson\s+\d+\s*:\s*", "", collected, flags=re.IGNORECASE))
            title = _strip_grade_suffix(title)
            if title:
                module_key = current_module.title.casefold()
                lesson_key = title.casefold()
                if lesson_key not in lesson_keys_by_module[module_key]:
                    current_module.children.append(
                        ParsedOutlineNode(
                            title=title,
                            depth=1,
                            order=len(current_module.children),
                        )
                    )
                    lesson_keys_by_module[module_key].add(lesson_key)
            index = next_index
            continue

        index += 1

    modules_with_lessons = [module for module in modules if module.children]
    return _normalize_outline_tree(modules_with_lessons) if modules_with_lessons else []


def _extract_teacher_module_outline(text: str) -> list[ParsedOutlineNode]:
    raw_lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in raw_lines if line and not _is_table_chrome_line(line)]
    modules: list[ParsedOutlineNode] = []
    module_by_key: dict[str, ParsedOutlineNode] = {}
    topic_keys_by_module: dict[str, set[str]] = {}
    current_module: ParsedOutlineNode | None = None
    current_module_key: str | None = None
    next_line_is_week_topic = False
    index = 0

    while index < len(lines):
        line = lines[index]
        if re.fullmatch(r"week\s*\d+", line, flags=re.IGNORECASE):
            next_line_is_week_topic = True
            index += 1
            continue

        module_match = re.search(
            r"^(?:module|unit|chapter|section)\s*(?P<number>\d+(?:\.\d+)*)?\s*[:\-–]?\s*(?P<title>.*)$",
            line,
            flags=re.IGNORECASE,
        )
        if module_match:
            collected, next_index = _collect_wrapped_outline_item(
                lines,
                index,
                r"^(?:module|unit|chapter|section)\s*\d*(?:\.\d+)*\s*[:\-–]?\s*",
            )
            title = _strip_grade_suffix(_clean_topic_title(collected))
            if title:
                module_number = module_match.group("number")
                key = f"module:{module_number}" if module_number else f"title:{title.casefold()}"
                current_module = module_by_key.get(key)
                if current_module is None:
                    current_module = ParsedOutlineNode(title=title, depth=0, order=len(modules))
                    modules.append(current_module)
                    module_by_key[key] = current_module
                    topic_keys_by_module[key] = set()
                elif title.casefold() != current_module.title.casefold():
                    topic_key = title.casefold()
                    if topic_key not in topic_keys_by_module[key]:
                        current_module.children.append(
                            ParsedOutlineNode(
                                title=title,
                                depth=1,
                                order=len(current_module.children),
                            )
                        )
                    topic_keys_by_module[key].add(topic_key)
                current_module_key = key
                next_line_is_week_topic = False
            index = next_index
            continue

        if next_line_is_week_topic and not _BULLET_LINE_RE.search(line):
            title = _strip_grade_suffix(_clean_topic_title(line))
            if _is_valid_outline_topic_title(title):
                key = f"title:{title.casefold()}"
                current_module = module_by_key.get(key)
                if current_module is None:
                    current_module = ParsedOutlineNode(title=title, depth=0, order=len(modules))
                    modules.append(current_module)
                    module_by_key[key] = current_module
                    topic_keys_by_module[key] = set()
                current_module_key = key
            else:
                current_module = None
                current_module_key = None
            next_line_is_week_topic = False
            index += 1
            continue

        bullet_match = _BULLET_LINE_RE.search(line)
        lesson_match = re.search(r"^(?:[•●\-\*]\s*)?lesson\s+\d+\s*[:\-–]\s*", line, flags=re.IGNORECASE)
        if (bullet_match or lesson_match) and current_module is not None and current_module_key is not None:
            collected, next_index = _collect_wrapped_outline_item(
                lines,
                index,
                r"^(?:[•●\-\*]\s*)?(?:lesson\s+\d+\s*[:\-–]\s*)?",
            )
            title = _strip_grade_suffix(_clean_topic_title(collected))
            if title and _is_valid_outline_topic_title(title):
                topic_key = title.casefold()
                if topic_key not in topic_keys_by_module[current_module_key]:
                    current_module.children.append(
                        ParsedOutlineNode(
                            title=title,
                            depth=1,
                            order=len(current_module.children),
                        )
                    )
                    topic_keys_by_module[current_module_key].add(topic_key)
            next_line_is_week_topic = False
            index = next_index
            continue

        next_line_is_week_topic = False
        index += 1

    modules_with_topics = [
        module
        for module in modules
        if module.children or not any(re.search(pattern, module.title.lower()) for pattern in _NOISE_PATTERNS)
    ]
    return _normalize_outline_tree(modules_with_topics) if modules_with_topics else []


def _is_module_lesson_boundary_line(line: str) -> bool:
    lowered = line.lower().strip()
    return bool(
        not line
        or line.startswith("PAGE ")
        or lowered.startswith(("pdf ", "raw pdf text", "table page=", "row ", "cell "))
        or lowered in {"module", "subtopics / lessons", "weekly course outline"}
        or re.search(r"^module\s+\d+\s*:", line, flags=re.IGNORECASE)
        or re.search(r"^lesson\s+\d+\s*:", line, flags=re.IGNORECASE)
        or _BULLET_LINE_RE.search(line)
    )


def _collect_module_lesson_title(lines: list[str], start_index: int, strip_pattern: str) -> tuple[str, int]:
    title = _clean_topic_title(re.sub(strip_pattern, "", lines[start_index], flags=re.IGNORECASE))
    index = start_index + 1
    while index < len(lines):
        candidate = lines[index].strip()
        if _is_module_lesson_boundary_line(candidate):
            break
        if not _looks_like_wrapped_title_continuation(title, candidate):
            break
        title = f"{title} {_clean_topic_title(candidate)}".strip()
        index += 1
    return title, index


def _extract_module_lesson_bullet_outline(text: str) -> list[ParsedOutlineNode]:
    if not re.search(r"\bmodule\s+\d+\s*:", text, flags=re.IGNORECASE):
        return []
    if not re.search(r"\blesson\s+\d+\s*:", text, flags=re.IGNORECASE):
        return []

    raw_context = _build_outline_context(text, limit=12000) if "PDF LAYOUT TABLES" in text else text
    # Preprocess to split combined "Module ... • Lesson ..." lines so module and lesson
    # parts are parsed independently. This helps when PDF extraction places module and
    # lesson text on the same physical line separated by a bullet.
    sep_context = re.sub(r"\s+[•●\-\*]\s+", "\n• ", raw_context)
    lines = _merge_wrapped_outline_candidate_lines(
        [re.sub(r"\s+", " ", line).strip() for line in sep_context.splitlines()]
    )
    modules: list[ParsedOutlineNode] = []
    module_by_key: dict[str, ParsedOutlineNode] = {}
    lesson_keys_by_module: dict[str, set[str]] = {}
    bullet_keys_by_lesson: dict[int, set[str]] = {}
    current_module: ParsedOutlineNode | None = None
    current_module_key: str | None = None
    current_lesson: ParsedOutlineNode | None = None
    index = 0

    while index < len(lines):
        line = lines[index]
        if not line or _is_table_chrome_line(line):
            index += 1
            continue

        module_match = re.search(r"^(?:[•●\-\*]\s*)?module\s+(?P<number>\d+)\s*:\s*(?P<title>.+)$", line, flags=re.IGNORECASE)
        if module_match:
            title, next_index = _collect_module_lesson_title(lines, index, r"^(?:[•●\-\*]\s*)?module\s+\d+\s*:\s*")
            if title and _is_valid_outline_topic_title(title):
                current_module_key = f"{module_match.group('number')}:{title.casefold()}"
                current_module = module_by_key.get(current_module_key)
                if current_module is None:
                    current_module = ParsedOutlineNode(title=title, depth=0, order=len(modules))
                    modules.append(current_module)
                    module_by_key[current_module_key] = current_module
                    lesson_keys_by_module[current_module_key] = set()
                current_lesson = None
            index = next_index
            continue

        lesson_match = re.search(r"^(?:[•●\-\*]\s*)?lesson\s+(?P<number>\d+)\s*:\s*(?P<title>.+)$", line, flags=re.IGNORECASE)
        if lesson_match and current_module is not None and current_module_key is not None:
            title, next_index = _collect_module_lesson_title(lines, index, r"^(?:[•●\-\*]\s*)?lesson\s+\d+\s*:\s*")
            if title and _is_valid_outline_topic_title(title):
                lesson_key = title.casefold()
                if lesson_key in lesson_keys_by_module[current_module_key]:
                    current_lesson = next(
                        (child for child in current_module.children if child.title.casefold() == lesson_key),
                        None,
                    )
                else:
                    current_lesson = ParsedOutlineNode(
                        title=title,
                        depth=1,
                        order=len(current_module.children),
                    )
                    current_module.children.append(current_lesson)
                    lesson_keys_by_module[current_module_key].add(lesson_key)
                    bullet_keys_by_lesson[id(current_lesson)] = set()
            index = next_index
            continue

        numbered_match = re.search(
            r"^(?:[•●\-\*]\s*)?(?P<number>\d+(?:\.\d+)*)[\.)]?\s+(?P<title>.+)$",
            line,
        )
        bullet_match = _BULLET_LINE_RE.search(line)

        if bullet_match and current_lesson is not None:
            title = _clean_topic_title(bullet_match.group(1))
            if title and _is_valid_outline_topic_title(title):
                bullet_key = title.casefold()
                seen_bullets = bullet_keys_by_lesson.setdefault(id(current_lesson), set())
                if bullet_key not in seen_bullets:
                    current_lesson.children.append(
                        ParsedOutlineNode(
                            title=title,
                            depth=2,
                            order=len(current_lesson.children),
                        )
                    )
                    seen_bullets.add(bullet_key)
            index += 1
            continue

        if current_module is not None and current_module_key is not None and (numbered_match or bullet_match):
            title_text = None
            if numbered_match:
                title_text = numbered_match.group("title")
            elif bullet_match:
                title_text = bullet_match.group(1)
            if title_text:
                title = _clean_topic_title(title_text)
                if title and _is_valid_outline_topic_title(title):
                    topic_key = title.casefold()
                    if topic_key not in lesson_keys_by_module.get(current_module_key, set()):
                        current_lesson = None
                        if current_lesson is None and not any(child.title.casefold() == topic_key for child in current_module.children):
                            current_module.children.append(
                                ParsedOutlineNode(
                                    title=title,
                                    depth=1,
                                    order=len(current_module.children),
                                )
                            )
                            lesson_keys_by_module[current_module_key].add(topic_key)
                index += 1
                continue

        index += 1

    modules_with_lessons = [module for module in modules if module.children]
    return _normalize_outline_tree(modules_with_lessons) if modules_with_lessons else []


def _extract_generic_module_topic_outline(text: str) -> list[ParsedOutlineNode]:
    if not re.search(r"\bmodule\s+\d+\s*[:\-–]", text, flags=re.IGNORECASE):
        return []

    raw_context = _build_outline_context(text, limit=12000) if "PDF LAYOUT TABLES" in text else text
    lines = _merge_wrapped_outline_candidate_lines(
        [re.sub(r"\s+", " ", line).strip() for line in raw_context.splitlines()]
    )

    modules: list[ParsedOutlineNode] = []
    module_by_key: dict[str, ParsedOutlineNode] = {}
    topic_keys_by_module: dict[str, set[str]] = {}
    current_module: ParsedOutlineNode | None = None
    current_module_key: str | None = None
    index = 0

    while index < len(lines):
        line = lines[index].strip()
        if not line or _is_table_chrome_line(line):
            index += 1
            continue

        module_match = re.search(
            r"^(?:[•●\-\*]\s*)?module\s+(?P<number>\d+(?:\.\d+)*)\s*[:\-–]?\s*(?P<title>.+)$",
            line,
            flags=re.IGNORECASE,
        )
        if module_match:
            title, next_index = _collect_wrapped_title(lines, index)
            title = _strip_grade_suffix(_clean_topic_title(title))
            if title and _is_valid_outline_topic_title(title):
                number = module_match.group("number")
                current_module_key = f"module:{number or title.casefold()}"
                current_module = module_by_key.get(current_module_key)
                if current_module is None:
                    current_module = ParsedOutlineNode(title=title, depth=0, order=len(modules))
                    modules.append(current_module)
                    module_by_key[current_module_key] = current_module
                    topic_keys_by_module[current_module_key] = set()
                elif title.casefold() != current_module.title.casefold():
                    topic_key = title.casefold()
                    if topic_key not in topic_keys_by_module[current_module_key]:
                        current_module.children.append(
                            ParsedOutlineNode(
                                title=title,
                                depth=1,
                                order=len(current_module.children),
                            )
                        )
                        topic_keys_by_module[current_module_key].add(topic_key)
            index = next_index
            continue

        if current_module is not None and current_module_key is not None:
            numbered_match = re.search(
                r"^(?:[•●\-\*]\s*)?(?P<number>\d+(?:\.\d+)*)[\.)]?\s+(?P<title>.+)$",
                line,
            )
            if numbered_match:
                title, next_index = _collect_wrapped_outline_item(
                    lines,
                    index,
                    r"^(?:[•●\-\*]\s*)?(?:\d+(?:\.\d+)*)[\.)]?\s*",
                )
                title = _strip_grade_suffix(_clean_topic_title(title))
                if title and _is_valid_outline_topic_title(title):
                    topic_key = title.casefold()
                    if topic_key not in topic_keys_by_module[current_module_key]:
                        current_module.children.append(
                            ParsedOutlineNode(
                                title=title,
                                depth=1,
                                order=len(current_module.children),
                            )
                        )
                        topic_keys_by_module[current_module_key].add(topic_key)
                index = next_index
                continue

            bullet_match = _BULLET_LINE_RE.search(line)
            if bullet_match:
                title, next_index = _collect_wrapped_outline_item(
                    lines,
                    index,
                    r"^(?:[•●\-\*]\s*)",
                )
                title = _strip_grade_suffix(_clean_topic_title(title))
                if title and _is_valid_outline_topic_title(title):
                    topic_key = title.casefold()
                    if topic_key not in topic_keys_by_module[current_module_key]:
                        current_module.children.append(
                            ParsedOutlineNode(
                                title=title,
                                depth=1,
                                order=len(current_module.children),
                            )
                        )
                        topic_keys_by_module[current_module_key].add(topic_key)
                index = next_index
                continue

        index += 1

    modules_with_children = [module for module in modules if module.children]
    return _normalize_outline_tree(modules_with_children) if modules_with_children else []


_NUMBERED_CONTENT_RE = re.compile(r"^(?P<number>\d+(?:\.\d+)*)\.?\s+(?P<title>.+)$")
_NUMBER_ONLY_CONTENT_RE = re.compile(r"^(?P<number>\d+(?:\.\d+)*)\.?$")


def _extract_numbered_content_column_outline(text: str) -> list[ParsedOutlineNode]:
    if "PDF CONTENT COLUMN" not in text:
        return []

    roots: list[ParsedOutlineNode] = []
    node_by_number: dict[str, ParsedOutlineNode] = {}
    current_number: str | None = None
    pending_number: str | None = None
    seen_section_titles: set[str] = set()

    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if line in {"PDF LAYOUT TABLES", "RAW PDF TEXT", "VISION PAGE TRANSCRIPTION"}:
            break
        if not line or line.startswith("PDF CONTENT COLUMN"):
            continue
        if line.startswith("PAGE "):
            current_number = None
            pending_number = None
            continue
        if _is_content_column_chrome_line(line) or _is_table_chrome_line(line):
            continue

        number_only_match = _NUMBER_ONLY_CONTENT_RE.match(line)
        if number_only_match:
            pending_number = number_only_match.group("number")
            current_number = None
            continue

        if pending_number:
            title = _clean_topic_title(line)
            if not _is_valid_outline_topic_title(title):
                pending_number = None
                current_number = None
                continue
            number = pending_number
            pending_number = None

            existing_node = node_by_number.get(number)
            if existing_node and _is_duplicate_numbered_title(existing_node.title, title):
                current_number = None
                continue

            node = _add_numbered_content_node(number, title, roots, node_by_number)
            current_number = number if node else None
            continue

        match = _NUMBERED_CONTENT_RE.match(line)
        if match:
            number = match.group("number")
            title = _clean_topic_title(match.group("title"))
            if not _is_valid_outline_topic_title(title):
                current_number = None
                pending_number = None
                continue

            existing_node = node_by_number.get(number)
            if existing_node and _is_duplicate_numbered_title(existing_node.title, title):
                current_number = None
                pending_number = None
                continue

            node = _add_numbered_content_node(number, title, roots, node_by_number)
            current_number = number
            pending_number = None
            continue

        if current_number and current_number in node_by_number:
            continuation = _clean_topic_title(line)
            node = node_by_number[current_number]
            if _looks_like_title_continuation(node.title, continuation):
                node.title = f"{node.title} {continuation}".strip()
            else:
                current_number = None
            continue

        title = _clean_topic_title(line)
        if not title or not _is_valid_outline_topic_title(title):
            current_number = None
            pending_number = None
            continue
        if title.casefold() in seen_section_titles:
            current_number = None
            pending_number = None
            continue
        seen_section_titles.add(title.casefold())
        current_number = None
        pending_number = None

    return _normalize_outline_tree(_dedupe_outline_nodes(roots))


def _is_duplicate_numbered_title(existing_title: str, next_title: str) -> bool:
    existing = _normalized_title_text(existing_title)
    next_value = _normalized_title_text(next_title)
    return existing == next_value or existing.startswith(next_value) or next_value.startswith(existing)


def _add_numbered_content_node(
    number: str,
    title: str,
    roots: list[ParsedOutlineNode],
    node_by_number: dict[str, ParsedOutlineNode],
) -> ParsedOutlineNode | None:
    depth = number.count(".")
    node = ParsedOutlineNode(title=title, depth=depth, order=0)
    node_by_number[number] = node

    parent_number = number.rsplit(".", 1)[0] if "." in number else None
    parent = node_by_number.get(parent_number) if parent_number else None
    if parent and depth > 0 and roots and parent is not roots[-1]:
        parent = roots[-1]
    if parent:
        node.order = len(parent.children)
        parent.children.append(node)
    else:
        node.order = len(roots)
        roots.append(node)
    return node


def _clean_table_cell(value) -> str:
    return re.sub(r"\s+\n", "\n", str(value or "")).strip()


def _markdown_table_cell(value) -> str:
    return _clean_table_cell(value).replace("\n", "<br>").replace("|", "/")


def _table_cell_looks_like_outline(value: str) -> bool:
    lowered = value.lower()
    return bool(
        re.search(r"\b(module|unit|chapter|lesson|section|topic)\s*\d*\b", lowered)
        or re.search(r"^[\s•●\-\*]*(?:\d+(?:\.\d+)*[\.\)]|[ivxlcdm]+[\.\)])\s+\S+", value, flags=re.IGNORECASE | re.MULTILINE)
        or re.search(r"^[\s•●\-\*]+\S+", value, flags=re.MULTILINE)
    )


def _is_topic_table_header(value: str) -> bool:
    lowered = re.sub(r"[^a-z0-9/ ]+", " ", str(value).lower())
    lowered = re.sub(r"\s+", " ", lowered).strip()
    return bool(
        lowered in {"topic", "topics", "content", "contents", "lesson", "lessons", "subtopics", "subtopics / lessons"}
        or "topics / content" in lowered
        or "subtopics / lessons" in lowered
        or "teacher outline" in lowered
    )


def _topic_column_indexes_from_row(cells: list[str]) -> list[int]:
    if any(_is_topic_table_header(cell) for cell in cells):
        return [index for index, cell in enumerate(cells) if _is_topic_table_header(cell)]
    return []


def _extract_pdf_table_outline_text(document: fitz.Document) -> str:
    lines = [
        "PDF LAYOUT TABLES",
        "Detected PDF tables are transcribed below in page, table, row, and cell order.",
        "Column names may vary. Infer the teacher's outline hierarchy from the table layout, headers, row grouping, bullets, numbering, indentation, and module/unit/chapter labels.",
        "Do not assume a fixed table format.",
    ]
    candidate_lines = ["", "POSSIBLE OUTLINE CELL TEXT"]
    found_tables = False
    found_candidates = False

    for page in document:
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                tables = page.find_tables().tables
        except Exception:
            tables = []

        for table in tables:
            found_tables = True
            rows = table.extract()
            topic_column_indexes: list[int] = []
            lines.append("")
            lines.append(f"TABLE page={page.number + 1}")
            for row_index, row in enumerate(rows, start=1):
                cells = [_clean_table_cell(cell) for cell in row]
                if not any(cells):
                    continue

                detected_topic_columns = _topic_column_indexes_from_row(cells)
                if detected_topic_columns:
                    topic_column_indexes = detected_topic_columns

                lines.append(f"ROW {row_index}:")
                for cell_index, cell in enumerate(cells, start=1):
                    if not cell:
                        continue
                    lines.append(f"  CELL {cell_index}: {_markdown_table_cell(cell)}")

                candidate_cells = (
                    [cells[index] for index in topic_column_indexes if index < len(cells)]
                    if topic_column_indexes
                    else cells
                )
                for cell in candidate_cells:
                    if cell and not _is_topic_table_header(cell) and _table_cell_looks_like_outline(cell):
                        found_candidates = True
                        candidate_lines.extend(_merge_wrapped_outline_candidate_lines(cell.splitlines()))

    if not found_tables:
        return ""
    if found_candidates:
        lines[4:4] = candidate_lines
    return "\n".join(lines).strip()


def _extract_pdf_content_column_outline_text(document: fitz.Document) -> str:
    document_text_sample = "\n".join(page.get_text("text") for page in list(document)[:2])
    if not (
        re.search(r"\bCONTENT\s+CONTENT\s+STANDARDS\b", document_text_sample, flags=re.IGNORECASE)
        and re.search(r"\bLEARNING\s+COMPETENCY\b", document_text_sample, flags=re.IGNORECASE)
    ):
        return ""

    lines = [
        "PDF CONTENT COLUMN",
        "Detected left CONTENT column text from the curriculum guide.",
    ]

    for page in document:
        grouped_lines: dict[tuple[int, int], list[tuple[float, float, str]]] = {}
        for word in page.get_text("words"):
            x0, y0, _x1, _y1, text, block_number, line_number, _word_number = word
            if not (30 <= x0 <= 180) or y0 < 120:
                continue
            grouped_lines.setdefault((block_number, line_number), []).append((y0, x0, str(text)))

        page_lines = [
            " ".join(token for _y, _x, token in sorted(words))
            for _key, words in sorted(
                grouped_lines.items(),
                key=lambda item: (min(token[0] for token in item[1]), min(token[1] for token in item[1])),
            )
        ]

        useful_lines = [
            line
            for line in page_lines
            if not _is_content_column_chrome_line(line)
        ]
        if useful_lines:
            lines.append(f"PAGE {page.number + 1}")
            lines.extend(useful_lines)

    return "\n".join(lines).strip() if len(lines) > 2 else ""


def _is_content_column_chrome_line(line: str) -> bool:
    lowered = line.lower().strip()
    return bool(
        lowered in {"content", "content standards", "standards"}
        or lowered.startswith("grade 4")
        or "quarter" in lowered
        or "grading period" in lowered
        or lowered.startswith("page ")
        or lowered.startswith("k to 12")
    )


def _has_enough_embedded_pdf_text(text: str) -> bool:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    words = re.findall(r"[A-Za-z0-9]+", cleaned)
    return len(cleaned) >= 120 and len(words) >= 25


def _document_structure_evidence(text: str) -> dict:
    """Measure document structure without using subject-specific vocabulary."""
    lines = [re.sub(r"\s+", " ", line).strip() for line in (text or "").splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return {
            "line_count": 0,
            "explicit_items": 0,
            "hierarchical_items": 0,
            "numbered_items": 0,
            "bullet_items": 0,
            "prose_ratio": 0.0,
            "outline_heading": False,
            "curriculum_schema_markers": 0,
            "structured_items": 0,
        }

    explicit_items = sum(
        bool(
            re.match(
                r"^(?:module|unit|chapter|week|lesson|topic|subtopic)\s*"
                r"(?:no\.?|number|#)?\s*\d+(?:\.\d+)*\b",
                line,
                flags=re.IGNORECASE,
            )
        )
        for line in lines
    )
    hierarchical_items = sum(
        bool(re.match(r"^\d+(?:\.\d+)+[.)]?\s+\S+", line))
        for line in lines
    )
    numbered_items = sum(
        bool(re.match(r"^\d+[.)]\s+\S+", line)) and len(line.split()) <= 14
        for line in lines
    )
    bullet_items = sum(
        bool(re.match(r"^[^\w\s]{1,3}\s+\S+", line)) and len(line.split()) <= 14
        for line in lines
    )
    prose_lines = sum(
        len(line.split()) >= 14 and bool(re.search(r"[.!?;:]$", line))
        for line in lines
    )
    outline_heading = any(
        re.search(
            r"\b(?:course|weekly|curriculum)\s+outline\b|\bsyllabus\b|\bscope\s+and\s+sequence\b",
            line,
            flags=re.IGNORECASE,
        )
        for line in lines
    )
    normalized_text = "\n".join(lines).casefold()
    curriculum_schema_markers = sum(
        marker in normalized_text
        for marker in (
            "content standard",
            "learning competenc",
            "learning outcome",
            "course code",
            "credit unit",
            "grading period",
        )
    )
    structured_items = explicit_items + hierarchical_items + numbered_items + bullet_items
    prose_ratio = prose_lines / max(len(lines), 1)

    return {
        "line_count": len(lines),
        "explicit_items": explicit_items,
        "hierarchical_items": hierarchical_items,
        "numbered_items": numbered_items,
        "bullet_items": bullet_items,
        "prose_ratio": prose_ratio,
        "outline_heading": outline_heading,
        "curriculum_schema_markers": curriculum_schema_markers,
        "structured_items": structured_items,
    }


def is_course_outline_document(text: str) -> bool:
    """Classify curriculum structure without using subject-specific vocabulary."""
    evidence = _document_structure_evidence(text)
    if not evidence["line_count"]:
        return False

    return bool(
        (evidence["outline_heading"] and evidence["structured_items"] >= 2)
        or (evidence["explicit_items"] >= 3 and evidence["structured_items"] >= 4)
        or (
            evidence["curriculum_schema_markers"] >= 2
            and evidence["hierarchical_items"] >= 2
            and evidence["prose_ratio"] < 0.60
        )
    )


def is_course_outline_pdf(file_path: str) -> bool:
    """Return whether a PDF's visible structure represents a course outline."""
    document = fitz.open(file_path)
    try:
        visible_text = "\n".join(page.get_text("text") for page in document)
    finally:
        document.close()
    return is_course_outline_document(visible_text)


def validate_course_outline_pdf(file_path: str) -> None:
    """Reject lesson PDFs before they can replace the saved course outline."""
    if not is_course_outline_pdf(file_path):
        raise ValueError(
            "This PDF appears to be lesson material, not a course outline. "
            "Upload a course outline containing modules, lessons, topics, or a curriculum schedule."
        )

    extracted_text = extract_outline_text(file_path, ".pdf")
    marker = re.search(r"weekly course outline", extracted_text, flags=re.IGNORECASE)
    parse_text = extracted_text[marker.start():] if marker else extracted_text
    parsed = parse_outline_text(parse_text)

    def count_nodes(nodes: list[ParsedOutlineNode]) -> int:
        return sum(1 + count_nodes(node.children) for node in nodes)

    if count_nodes(parsed) < 2:
        raise ValueError(
            "This PDF does not contain enough course-outline structure. "
            "At least two identifiable module, lesson, or topic nodes are required."
        )


def _render_pdf_pages_as_images(document: fitz.Document, max_pages: int | None = None) -> list[dict]:
    pages = []
    limit = max_pages or int(os.getenv("MAX_PDF_PAGE_IMAGES_FOR_VISION", "8"))
    matrix = fitz.Matrix(2, 2)
    for page_index, page in enumerate(document, start=1):
        if page_index > limit:
            break
        pixmap = page.get_pixmap(matrix=matrix, alpha=False)
        pages.append(
            {
                "page_number": page_index,
                "image_bytes": pixmap.tobytes("png"),
            }
        )
    return pages


def _transcribe_image_only_outline_pdf(document: fitz.Document) -> str:
    """Deterministic fallback: use embedded PDF text only. There is no LLM OCR step."""
    pages = []
    for page_index, page in enumerate(document, start=1):
        text = page.get_text("text").strip()
        if text:
            pages.append(f"Page {page_index}\n{text.strip()}")
    return "\n\n".join(pages).strip()


def extract_outline_text(file_path: str, extension: str) -> str:
    extension = extension.lower().lstrip(".")
    if extension != "pdf":
        raise ValueError("Only PDF course outlines are supported.")

    document = fitz.open(file_path)
    table_text = _extract_pdf_table_outline_text(document)
    content_column_text = _extract_pdf_content_column_outline_text(document)
    text = "\n".join(page.get_text("text") for page in document)
    try:
        if _has_enough_embedded_pdf_text(text):
            layout_parts = [part for part in [content_column_text, table_text] if part]
            if layout_parts:
                layout_text = "\n\n".join(layout_parts)
                return f"{layout_text}\n\nRAW PDF TEXT\n{text}"
            return text

        transcribed_text = _transcribe_image_only_outline_pdf(document)
        if transcribed_text:
            return f"VISION PAGE TRANSCRIPTION\n{transcribed_text}"
        return text
    finally:
        document.close()


def _build_outline_context(text: str, limit: int = 8000) -> str:
    table_match = re.search(
        r"(?:TEACHER OUTLINE TOPICS COLUMN|PDF LAYOUT TABLES)\s*(.*?)(?:\n\s*RAW PDF TEXT\s*\n|\Z)",
        text,
        flags=re.DOTALL,
    )
    if table_match:
        table_context = table_match.group(0).strip()
        if table_context:
            return table_context[: min(limit, 5500)]

    heading_lines = []
    fallback_lines = []
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            continue
        lowered = line.lower()
        if len(line) > 180:
            continue
        if re.fullmatch(r"\d+", lowered) or lowered.startswith(("page ", "p.")):
            continue
        is_heading_like = bool(
            re.search(r"^(module|unit|chapter|lesson|section|topic)\b", lowered)
            or re.search(r"^\d+(?:\.\d+)*[\.\)]?\s+\S+", line)
            or re.search(r"^[ivxlcdm]+[\.\)]\s+\S+", lowered)
        )
        if is_heading_like:
            heading_lines.append(line)
        elif len(fallback_lines) < 80:
            fallback_lines.append(line)

    context = "\n".join(heading_lines or fallback_lines)
    if len(context) < 800:
        context = "\n".join([context, *fallback_lines]).strip()
    return context[:limit]


def _looks_like_outline_candidate_line(line: str) -> bool:
    lowered = line.lower().strip()
    if not line or len(line) > 160:
        return False
    if lowered in {"module", "subtopics / lessons", "possible outline cell text", "pdf layout tables"}:
        return False
    if lowered.startswith(("table page=", "row ", "cell ", "raw pdf text", "pdf content column")):
        return False
    if re.fullmatch(r"\d+", lowered) or lowered.startswith(("page ", "p.")):
        return False
    if any(re.search(pattern, lowered) for pattern in _NOISE_PATTERNS):
        return False
    return bool(
        re.search(r"^(module|unit|chapter|lesson|section|topic)\b", lowered)
        or re.search(r"^\d+(?:\.\d+)*[\.\)]?\s+\S+", line)
        or re.search(r"^[ivxlcdm]+[\.\)]\s+\S+", lowered)
        or re.search(r"^[•●\-\*]\s+\S+", line)
        or (
            1 <= len(line.split()) <= 9
            and line[:1].isupper()
            and not line.endswith((".", ";"))
            and not _looks_like_fragment_or_non_topic(line)
        )
    )


def _build_outline_candidates(text: str, limit: int = 120) -> list[dict]:
    context = _build_outline_context(text, limit=12000)
    candidates = []
    seen = set()
    for raw_line in _merge_wrapped_outline_candidate_lines(context.splitlines()):
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not _looks_like_outline_candidate_line(line):
            continue
        title = _clean_topic_title(_normalize_line(line))
        if not _is_valid_outline_topic_title(title):
            continue
        key = title.casefold()
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            {
                "id": len(candidates) + 1,
                "raw": line,
                "title": title,
            }
        )
        if len(candidates) >= limit:
            break
    return candidates


def parse_outline_text(text: str) -> list[ParsedOutlineNode]:
    module_lesson_nodes = _extract_module_lesson_bullet_outline(text)
    if module_lesson_nodes:
        return module_lesson_nodes

    generic_module_nodes = _extract_generic_module_topic_outline(text)
    if generic_module_nodes:
        return generic_module_nodes

    numbered_content_nodes = _extract_numbered_content_column_outline(text)
    if numbered_content_nodes:
        return numbered_content_nodes

    structured_nodes = _extract_teacher_module_outline(text) or _extract_module_lesson_outline(text)
    if structured_nodes:
        return _normalize_outline_tree(_dedupe_outline_nodes(structured_nodes))

    lines = text.splitlines()
    nodes = _parse_outline_lines(lines)

    if not nodes:
        fallback = []
        for index, line in enumerate(lines):
            title = _clean_topic_title(_normalize_line(line))
            if not title:
                continue
            if _looks_like_real_outline_item(title, line):
                fallback.append(ParsedOutlineNode(title=title, depth=0, order=index))
        return _flatten_accidental_chain(_normalize_outline_tree(_dedupe_outline_nodes(fallback)))[:50]

    return _flatten_accidental_chain(_normalize_outline_tree(_dedupe_outline_nodes(nodes)))


def _persist_nodes(
    course: CourseGroup,
    nodes: list[ParsedOutlineNode],
    parent: OutlineNode | None = None,
) -> list[OutlineNode]:
    created: list[OutlineNode] = []
    for parsed in nodes:
        outline_node = OutlineNode.objects.create(
            course=course,
            parent=parent,
            title=parsed.title,
            related_info=parsed.related_info,
            order=parsed.order,
            depth=parsed.depth,
        )
        created.append(outline_node)
        if parsed.children:
            created.extend(_persist_nodes(course, parsed.children, outline_node))
    return created


def _merge_persisted_nodes(
    course: CourseGroup,
    nodes: list[ParsedOutlineNode],
    parent: OutlineNode | None = None,
) -> list[OutlineNode]:
    """Merge one parsed outline tree into the course without deleting prior nodes.

    Titles are compared only among siblings. An existing module/topic keeps its
    database identity so lesson materials already mapped to it remain valid;
    newly encountered siblings are appended after the current last sibling.
    """
    siblings = list(course.nodes.filter(parent=parent).order_by("order", "id"))
    by_title = {_normalized_title_text(node.title): node for node in siblings}
    next_order = max((node.order for node in siblings), default=-1) + 1
    merged: list[OutlineNode] = []

    for parsed in nodes:
        title_key = _normalized_title_text(parsed.title)
        outline_node = by_title.get(title_key)
        if outline_node is None:
            outline_node = OutlineNode.objects.create(
                course=course,
                parent=parent,
                title=parsed.title,
                related_info=parsed.related_info,
                order=next_order,
                depth=parent.depth + 1 if parent else 0,
            )
            by_title[title_key] = outline_node
            next_order += 1
        elif parsed.related_info:
            # Preserve teacher-edited/existing values and fill only information
            # that was absent from the earlier outline source.
            combined_info = {**parsed.related_info, **(outline_node.related_info or {})}
            if combined_info != outline_node.related_info:
                outline_node.related_info = combined_info
                outline_node.save(update_fields=["related_info"])

        merged.append(outline_node)
        if parsed.children:
            merged.extend(_merge_persisted_nodes(course, parsed.children, outline_node))

    return merged


def build_dag_from_outline(
    course: CourseGroup,
    file_path: str,
    extension: str,
    *,
    replace: bool = False,
) -> list[OutlineNode]:
    text = extract_outline_text(file_path, extension)
    # Prefer parsing the weekly course outline section when present to avoid
    # capturing course-info sections such as "Intended Learning Outcomes",
    # which sometimes contain bulleted verbs that look like lesson titles.
    marker = re.search(r"weekly course outline", text, flags=re.IGNORECASE)
    if marker:
        # slice from the marker onward to focus parsing on the actual weekly outline
        text = text[marker.start():]
    parsed = parse_outline_text(text)
    logger.debug("Parsed course-outline roots: %s", [node.title for node in parsed])
    if not parsed:
        raise ValueError("No course-outline topics could be extracted from this PDF.")
    if replace:
        course.nodes.all().delete()
        return _persist_nodes(course, parsed)
    return _merge_persisted_nodes(course, parsed)
