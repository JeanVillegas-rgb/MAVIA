from __future__ import annotations

import re
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

        title = _normalize_line(raw_line)
        if not title or title.lower() in {"table of contents", "course outline", "outline"}:
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


def extract_outline_text(file_path: str, extension: str) -> str:
    extension = extension.lower().lstrip(".")
    if extension == "pdf":
        document = fitz.open(file_path)
        text = "\n".join(page.get_text("text") for page in document)
        document.close()
        return text
    with open(file_path, encoding="utf-8", errors="ignore") as handle:
        return handle.read()


def parse_outline_text(text: str) -> list[ParsedOutlineNode]:
    lines = text.splitlines()
    nodes = _parse_outline_lines(lines)

    if not nodes:
        fallback = [
            ParsedOutlineNode(title=line.strip(), depth=0, order=index)
            for index, line in enumerate(lines)
            if line.strip()
        ]
        return fallback[:50]

    return nodes


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
            status=OutlineNode.NodeStatus.EMPTY,
        )
        created.append(outline_node)
        if parsed.children:
            created.extend(_persist_nodes(course, parsed.children, outline_node))
    return created


def build_dag_from_outline(course: CourseGroup, file_path: str, extension: str) -> list[OutlineNode]:
    course.nodes.all().delete()
    text = extract_outline_text(file_path, extension)
    parsed = parse_outline_text(text)
    return _persist_nodes(course, parsed)
