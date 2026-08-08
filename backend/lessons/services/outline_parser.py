from __future__ import annotations

import re
import os
import contextlib
import io
from dataclasses import dataclass, field

import fitz

from lessons.models import CourseGroup, OutlineNode


@dataclass
class ParsedOutlineNode:
    title: str
    depth: int
    order: int
    related_info: dict = field(default_factory=dict)
    children: list[ParsedOutlineNode] = field(default_factory=list)


def _normalize_line(line: str) -> str:
    line = line.strip()
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


def _is_valid_llm_topic_title(title: str) -> bool:
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

    if len(title.strip()) < 4:
        return True
    if len(letters) < 3:
        return True
    if len(words) == 1 and len(words[0]) <= 3:
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
        parent_key = stack[-1].title if stack else None
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
        title = f"{title} {candidate}".strip()
        index += 1
        if re.search(r"\)\s*$", title):
            break
    return title, index


_OUTLINE_ITEM_START_RE = re.compile(
    r"^(?:[•●\-\*]\s*)?(?:module|unit|chapter|lesson|section|topic)\s*\d*(?:\.\d+)*\s*[:\-–]?",
    re.IGNORECASE,
)

_BULLET_LINE_RE = re.compile(r"^[•●\-\*]\s*(.+)$")

# Words that only ever continue a wrapped title, never start a new one.
_TITLE_CONTINUATION_STARTERS = {
    "among",
    "and",
    "at",
    "based",
    "by",
    "due",
    "for",
    "from",
    "in",
    "into",
    "of",
    "on",
    "or",
    "their",
    "through",
    "to",
    "with",
}

# A title ending on one of these words is grammatically unfinished, so the next
# line is a wrap of the same title rather than a new item.
_DANGLING_TITLE_END_RE = re.compile(
    r"\b(?:among|and|at|based|by|chemical|due|for|from|in|into|of|on|or|that|their|through|to|with)$",
    re.IGNORECASE,
)


def _looks_like_title_continuation(current_title: str, candidate: str) -> bool:
    words = candidate.split()
    if not words or len(words) > 6:
        return False
    first_word = words[0].strip(",:;()").casefold()
    if first_word in _NON_TITLE_VERBS:
        return False
    if candidate[:1].islower():
        return True
    if len(words) == 1 and not current_title.rstrip().endswith((".", ";", ":")):
        return True
    if first_word in _TITLE_CONTINUATION_STARTERS:
        return True
    if len(words) <= 4 and _DANGLING_TITLE_END_RE.search(current_title.rstrip()):
        return True
    # Table cells wrap long lesson titles across lines, so a title that is still
    # only a few words is almost certainly cut off mid-phrase.
    return len(current_title.split()) <= 3 and not candidate.rstrip().endswith((".", ";"))


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
        if not _looks_like_title_continuation(title, candidate):
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
            if _is_valid_llm_topic_title(title):
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
            if title and _is_valid_llm_topic_title(title):
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
        if not _looks_like_title_continuation(title, candidate):
            break
        title = f"{title} {_clean_topic_title(candidate)}".strip()
        index += 1
    return title, index


def _extract_module_lesson_bullet_outline(text: str) -> list[ParsedOutlineNode]:
    if not re.search(r"\bmodule\s+\d+\s*:", text, flags=re.IGNORECASE):
        return []
    if not re.search(r"\blesson\s+\d+\s*:", text, flags=re.IGNORECASE):
        return []

    raw_context = _build_outline_llm_context(text, limit=12000) if "PDF LAYOUT TABLES" in text else text
    lines = [re.sub(r"\s+", " ", line).strip() for line in raw_context.splitlines()]
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

        module_match = re.search(r"^module\s+(?P<number>\d+)\s*:\s*(?P<title>.+)$", line, flags=re.IGNORECASE)
        if module_match:
            title, next_index = _collect_module_lesson_title(lines, index, r"^module\s+\d+\s*:\s*")
            if title and _is_valid_llm_topic_title(title):
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

        lesson_match = re.search(
            r"^(?:[•●\-\*]\s*)?lesson\s+(?P<number>\d+)\s*:\s*(?P<title>.+)$",
            line,
            flags=re.IGNORECASE,
        )
        if lesson_match and current_module is not None and current_module_key is not None:
            title, next_index = _collect_module_lesson_title(
                lines, index, r"^(?:[•●\-\*]\s*)?lesson\s+\d+\s*:\s*"
            )
            if title and _is_valid_llm_topic_title(title):
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

        bullet_match = _BULLET_LINE_RE.search(line)
        if bullet_match and current_lesson is not None:
            title = _clean_topic_title(bullet_match.group(1))
            if title and _is_valid_llm_topic_title(title):
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

        index += 1

    modules_with_lessons = [module for module in modules if module.children]
    return _normalize_outline_tree(modules_with_lessons) if modules_with_lessons else []


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
            if not _is_valid_llm_topic_title(title):
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
            if not _is_valid_llm_topic_title(title):
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
        if not title or not _is_valid_llm_topic_title(title):
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


def _filter_outline_nodes_with_llm(
    nodes: list[ParsedOutlineNode],
    source_text: str,
) -> list[ParsedOutlineNode]:
    if not nodes:
        return nodes

    try:
        from .llm_client import extract_json_from_text, get_llm_client
        import textwrap
    except Exception:
        return nodes

    candidates = _flatten_outline_nodes(nodes)
    if not candidates:
        return nodes

    candidate_lines = "\n".join(
        f"{index}. {node.title}"
        for index, node in enumerate(candidates, start=1)
    )
    excerpt = _build_outline_llm_context(source_text, limit=6000)

    prompt = textwrap.dedent(
        f"""
        You are validating extracted course outline nodes.

        Decide whether each candidate is a real subject-matter lesson topic/subtopic or not.

        A REAL TOPIC teaches academic content, concepts, skills, or lesson sections.
        NOT A TOPIC includes schedules, weeks, dates, classroom routines, introductions,
        announcements, assessments, quizzes, assignments, grading notes, teacher/admin notes,
        page labels, or other syllabus metadata.

        Return JSON only:
        {{
          "items": [
            {{"title": "Candidate title", "is_topic": true, "reason": "short reason"}}
          ]
        }}

        CANDIDATES:
        {candidate_lines}

        OUTLINE_CONTEXT:
        {excerpt}
        """
    )

    response = get_llm_client().generate_text(prompt, max_tokens=1200, timeout=240)
    if not response:
        return nodes

    output = response.get("text") if isinstance(response, dict) else None
    data = extract_json_from_text(output) if output else response
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return nodes

    accepted_titles = set()
    classified_any = False
    for item in items:
        if not isinstance(item, dict):
            continue
        title = _clean_topic_title(str(item.get("title") or ""))
        if not title:
            continue
        classified_any = True
        if item.get("is_topic") is True:
            accepted_titles.add(title.casefold())

    if not classified_any:
        return nodes

    def prune(items_to_prune: list[ParsedOutlineNode]) -> list[ParsedOutlineNode]:
        kept = []
        for node in items_to_prune:
            node.children = prune(node.children)
            if node.title.casefold() in accepted_titles:
                kept.append(node)
            else:
                kept.extend(node.children)
        return kept

    return _normalize_outline_tree(prune(nodes))


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
            lines.append("")
            lines.append(f"TABLE page={page.number + 1}")
            for row_index, row in enumerate(rows, start=1):
                cells = [_clean_table_cell(cell) for cell in row]
                if not any(cells):
                    continue

                lines.append(f"ROW {row_index}:")
                for cell_index, cell in enumerate(cells, start=1):
                    if not cell:
                        continue
                    lines.append(f"  CELL {cell_index}: {_markdown_table_cell(cell)}")
                    if _table_cell_looks_like_outline(cell):
                        found_candidates = True
                        candidate_lines.extend(
                            candidate_line.strip()
                            for candidate_line in cell.splitlines()
                            if candidate_line.strip()
                        )

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
    from .llm_client import extract_json_from_text, get_llm_client

    client = get_llm_client()
    pages = []
    for page in _render_pdf_pages_as_images(document):
        prompt = f"""
        Transcribe this teacher-provided course outline PDF page.

        PAGE:
        {page["page_number"]}

        OUTPUT JSON ONLY:
        {{
          "page": {page["page_number"]},
          "text": "All visible outline text from the page"
        }}

        RULES:
        - Copy all visible teacher text as accurately as possible.
        - Preserve headings, bullets, numbering, indentation, table cells, row labels, and reading order.
        - Do not summarize, simplify, classify, or add new topics.
        - If the page has no readable text, return an empty text string.
        """.strip()
        response = client.describe_image(page["image_bytes"], prompt, max_tokens=1800, timeout=300)
        output = response.get("text") if isinstance(response, dict) else None
        data = extract_json_from_text(output) if output else None
        text = data.get("text") if isinstance(data, dict) else output
        if text and str(text).strip():
            pages.append(f"Page {page['page_number']}\n{str(text).strip()}")
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


def _build_outline_llm_context(text: str, limit: int = 8000) -> str:
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
    context = _build_outline_llm_context(text, limit=12000)
    candidates = []
    seen = set()
    for raw_line in context.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not _looks_like_outline_candidate_line(line):
            continue
        title = _clean_topic_title(_normalize_line(line))
        if not _is_valid_llm_topic_title(title):
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


def _extract_outline_nodes_with_llm(text: str) -> list[ParsedOutlineNode] | None:
    try:
        from .llm_client import extract_json_from_text, get_llm_client
        import textwrap
    except Exception:
        return None

    candidates = _build_outline_candidates(text)
    if not candidates:
        return None

    candidate_lines = "\n".join(
        f'{item["id"]}. {item["raw"]}'
        for item in candidates
    )
    candidate_by_id = {item["id"]: item for item in candidates}

    prompt = textwrap.dedent(
        f"""
        You are identifying the explicit topic/subtopic hierarchy from a PDF course outline or syllabus.

        IMPORTANT RULES:
        - You are given CANDIDATE_LINES copied from the PDF.
        - You may choose only from those candidate line IDs.
        - Do not write, invent, summarize, translate, or paraphrase topic titles.
        - The backend will use the exact candidate title for each selected id.
        - Extract ONLY topics, subtopics, modules, units, chapters, lesson titles, or lesson section headings.
        - Do not extract learning objectives, competencies, learning focus, activities, outputs, assessments, dates, schedules, classroom tasks, directions, descriptions, examples, references, page labels, row labels, notes, or admin text as nodes.
        - Do not add related_info. Leave related_info as an empty object.
        - The teacher's written outline structure is authoritative.
        - Preserve parent-child relationships only when the PDF explicitly shows them through module/lesson labels, numbering, indentation, bullets, or table grouping.
        - Do not create a parent-child relationship just because two topics are semantically related.
        - If two lesson topics appear under the same module/section, return them as siblings with the same level.
        - If hierarchy is uncertain, keep the item at the same level as nearby lesson topics instead of nesting it under another topic.
        - Ignore repeated headers, footers, page numbers, author names, school names, and titles that are not lesson items.
        - Return only actual subject-matter topics, subtopics, modules, or lesson section headings.
        - Do not return schedule labels such as Week 1, Week 2, Day 1, meeting numbers, or date ranges.
        - Do not return classroom/admin activities such as self-introduction, course syllabus, expectations, class discussion, group discussion, quizzes, assignments, assessments, or performance tasks.
        - If a line says "Module 1: Properties of Matter", return "Properties of Matter" as the title.
        - If the same topic appears across multiple weeks, return it only once.
        - Reject random OCR fragments, isolated letters, single-character bullets, table artifacts, and broken words.
        - Use the whole document context to decide what is a real topic or subtopic.
        - Preserve hierarchy using the level field. Level 0 is top-level module/section, level 1 is a direct child lesson/topic of the previous level 0 item, level 2 is used only when the PDF explicitly shows a subtopic under that level 1 item.
        - If the outline is flat, use level 0 for all real lesson items.

        OUTPUT SCHEMA (JSON ONLY):
        {{
          "nodes": [
            {{
              "source_id": 1,
              "level": 0,
              "order": 1,
              "related_info": {{}}
            }}
          ]
        }}

        CANDIDATE_LINES:
        {candidate_lines}
        """
    )

    outline_timeout = int(os.getenv("OLLAMA_OUTLINE_TIMEOUT", "180"))
    resp = get_llm_client().generate_text(prompt, max_tokens=900, timeout=outline_timeout)
    if not resp:
        return None

    text_out = None
    if isinstance(resp, dict):
        text_out = resp.get("text") or resp.get("output")

    data = extract_json_from_text(text_out) if isinstance(text_out, str) else None
    if data is None and isinstance(resp, dict):
        data = resp if isinstance(resp, dict) else None

    if not data:
        return None

    nodes = []
    seen_titles = set()
    for index, item in enumerate(data.get("nodes", []), start=1):
        source_id = item.get("source_id")
        try:
            source_id = int(source_id)
        except (TypeError, ValueError):
            source_id = None
        candidate = candidate_by_id.get(source_id)
        if not candidate:
            continue

        title = candidate["title"]
        if not _is_valid_llm_topic_title(title):
            continue
        if title.lower() in {"table of contents", "contents", "outline"}:
            continue
        dedupe_key = title.casefold()
        if dedupe_key in seen_titles:
            continue
        seen_titles.add(dedupe_key)
        level = int(item.get("level") or 0)
        order = int(item.get("order") or index)
        nodes.append(
            ParsedOutlineNode(
                title=title,
                depth=max(level, 0),
                order=order,
                related_info={},
            )
        )

    return nodes or None


def _hierarchy_from_flat_nodes(nodes: list[ParsedOutlineNode]) -> list[ParsedOutlineNode]:
    roots: list[ParsedOutlineNode] = []
    stack: list[ParsedOutlineNode] = []

    for node in nodes:
        while stack and stack[-1].depth >= node.depth:
            stack.pop()
        if stack:
            stack[-1].children.append(node)
        else:
            roots.append(node)
        stack.append(node)

    return roots


def parse_outline_text(text: str) -> list[ParsedOutlineNode]:
    validate_with_llm = os.getenv("OLLAMA_VALIDATE_OUTLINE", "False").lower() in ("1", "true", "yes")
    module_lesson_nodes = _extract_module_lesson_bullet_outline(text)
    if module_lesson_nodes:
        return module_lesson_nodes

    numbered_content_nodes = _extract_numbered_content_column_outline(text)
    if numbered_content_nodes:
        return numbered_content_nodes

    try:
        llm_nodes = _extract_outline_nodes_with_llm(text)
    except Exception:
        llm_nodes = None
    if llm_nodes:
        roots = _hierarchy_from_flat_nodes(llm_nodes)
        return _normalize_outline_tree(_dedupe_outline_nodes(roots))

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
        filtered = _filter_outline_nodes_with_llm(fallback, text) if validate_with_llm else fallback
        return _flatten_accidental_chain(_normalize_outline_tree(_dedupe_outline_nodes(filtered)))[:50]

    filtered = _filter_outline_nodes_with_llm(nodes, text) if validate_with_llm else nodes
    return _flatten_accidental_chain(_normalize_outline_tree(_dedupe_outline_nodes(filtered)))


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


def build_dag_from_outline(course: CourseGroup, file_path: str, extension: str) -> list[OutlineNode]:
    text = extract_outline_text(file_path, extension)
    parsed = parse_outline_text(text)
    print(
        "[TRACE outline] parsed roots:",
        [node.title for node in parsed],
        flush=True,
    )
    course.nodes.all().delete()
    return _persist_nodes(course, parsed)
