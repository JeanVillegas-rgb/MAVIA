from __future__ import annotations

import re
import os
from dataclasses import dataclass, field

import fitz

from lessons.models import CourseGroup, OutlineNode


@dataclass
class ParsedOutlineNode:
    title: str
    depth: int
    order: int
    children: list[ParsedOutlineNode] = field(default_factory=list)


def _normalize_line(line: str) -> str:
    line = line.strip()
    line = re.sub(r"^[\-\*\+]\s+", "", line)
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
]


def _clean_topic_title(title: str) -> str:
    title = re.sub(r"\s+", " ", title).strip(" \t\r\n:;,-–")
    title = _OUTLINE_TITLE_PREFIX_RE.sub("", title).strip(" \t\r\n:;,-–")
    return title


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
    if any(re.search(pattern, title.lower()) for pattern in _NOISE_PATTERNS):
        return False
    return not _looks_like_schedule_or_admin_label(title)


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


def extract_outline_text(file_path: str, extension: str) -> str:
    extension = extension.lower().lstrip(".")
    if extension == "pdf":
        document = fitz.open(file_path)
        text = "\n".join(page.get_text("text") for page in document)
        document.close()
        return text
    with open(file_path, encoding="utf-8", errors="ignore") as handle:
        return handle.read()


def _build_outline_llm_context(text: str, limit: int = 8000) -> str:
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


def _extract_outline_nodes_with_llm(text: str) -> list[ParsedOutlineNode] | None:
    try:
        from .llm_client import extract_json_from_text, get_llm_client
        import textwrap
    except Exception:
        return None

    excerpt = _build_outline_llm_context(text, limit=8000)

    prompt = textwrap.dedent(
        """
        You are extracting a concept/topic hierarchy from a PDF course outline or syllabus.

        IMPORTANT RULES:
        - Ignore repeated headers, footers, page numbers, author names, school names, and titles that are not lesson items.
        - Return only actual subject-matter topics, subtopics, modules, or lesson section headings.
        - Do not return schedule labels such as Week 1, Week 2, Day 1, meeting numbers, or date ranges.
        - Do not return classroom/admin activities such as self-introduction, course syllabus, expectations, class discussion, group discussion, quizzes, assignments, assessments, or performance tasks.
        - If a line says "Module 1: Properties of Matter", return "Properties of Matter" as the title.
        - If the same topic appears across multiple weeks, return it only once.
        - Use the whole document context to decide what is a real lesson item.
        - Preserve hierarchy using the level field. Level 0 is top-level, level 1 is a child of the previous top-level item, level 2 is a child of the most recent level 1 item, and so on.
        - If the outline is flat, use level 0 for all real lesson items.

        OUTPUT SCHEMA (JSON ONLY):
        {
          "nodes": [
            {"title": "Lesson title", "level": 0, "order": 1},
            {"title": "Child lesson title", "level": 1, "order": 2}
          ]
        }

        PDF_TEXT:
        """
    ) + excerpt

    resp = get_llm_client().generate_text(prompt, max_tokens=900, timeout=240)
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
        raw_title = (item.get("title") or "").strip()
        title = _clean_topic_title(raw_title)
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
        nodes.append(ParsedOutlineNode(title=title, depth=max(level, 0), order=order))

    return nodes or None


def parse_outline_text(text: str) -> list[ParsedOutlineNode]:
    validate_with_llm = os.getenv("OLLAMA_VALIDATE_OUTLINE", "False").lower() in ("1", "true", "yes")
    structured_nodes = _extract_module_lesson_outline(text)
    if structured_nodes:
        return _normalize_outline_tree(_dedupe_outline_nodes(structured_nodes))

    llm_nodes = _extract_outline_nodes_with_llm(text)
    if llm_nodes:
        roots: list[ParsedOutlineNode] = []
        stack: list[ParsedOutlineNode] = []

        for node in llm_nodes:
            while stack and stack[-1].depth >= node.depth:
                stack.pop()
            if stack:
                stack[-1].children.append(node)
            else:
                roots.append(node)
            stack.append(node)

        filtered = _filter_outline_nodes_with_llm(roots, text) if validate_with_llm else roots
        return _flatten_accidental_chain(_normalize_outline_tree(_dedupe_outline_nodes(filtered)))

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
    course.nodes.all().delete()
    return _persist_nodes(course, parsed)
