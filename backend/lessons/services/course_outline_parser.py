"""Adapt Jean's outline extractor/hierarchy to MAVIA's persistence tree."""

from dataclasses import dataclass, field

from lessons.models import CourseGroup, OutlineNode

from .outline_pdf_extractor import extract_pdf_blocks
from .topic_hierarchy import build_topic_hierarchy, identify_outline_item


@dataclass
class ParsedOutlineNode:
    title: str
    depth: int
    order: int
    children: list = field(default_factory=list)
    related_info: dict = field(default_factory=dict)


def parse_outline_blocks(blocks):
    hierarchy = build_topic_hierarchy(blocks)
    roots = []
    converted = {}
    for item in hierarchy:
        node = ParsedOutlineNode(
            title=item["title"],
            depth=item["depth"],
            order=item["order"],
            children=[],
        )
        if item["type"] == "parent":
            converted[id(item)] = node
            roots.append(node)
        else:
            parent = converted.get(id(item["parent"]))
            if parent is not None:
                parent.children.append(node)
    if not roots:
        raise ValueError("No numbered modules and topics were found in the outline.")
    return roots


def parse_outline_lines(lines):
    blocks = [
        {"text": line, "block_id": index, "page": 1, "block_index": index}
        for index, line in enumerate(lines)
    ]
    return parse_outline_blocks(blocks)


def parse_outline_file(file_path, extension=".pdf"):
    if extension.lower().lstrip(".") != "pdf":
        raise ValueError("Only PDF course outlines are supported.")
    return parse_outline_blocks(extract_pdf_blocks(file_path))


def is_course_outline_pdf(file_path):
    """Use Jean's hierarchy parser as the outline/lesson routing decision."""
    try:
        roots = parse_outline_file(file_path, ".pdf")
    except (OSError, ValueError):
        return False
    return bool(roots and any(root.children for root in roots))


def _title_key(title):
    return " ".join((title or "").casefold().split())


def _persist_nodes(course, nodes, parent=None):
    saved = []
    siblings = list(course.nodes.filter(parent=parent).order_by("order", "id"))
    existing = {_title_key(node.title): node for node in siblings}
    next_order = max((node.order for node in siblings), default=-1) + 1
    for parsed in nodes:
        node = existing.get(_title_key(parsed.title))
        if node is None:
            node = OutlineNode.objects.create(
                course=course,
                parent=parent,
                title=parsed.title,
                related_info=parsed.related_info,
                order=next_order,
                depth=parent.depth + 1 if parent else 0,
            )
            existing[_title_key(parsed.title)] = node
            next_order += 1
        saved.append(node)
        saved.extend(_persist_nodes(course, parsed.children, node))
    return saved


def save_outline_hierarchy(course, parsed_nodes, *, replace=False):
    """Persist the hierarchy produced by Jean's deterministic parser."""
    if not isinstance(course, CourseGroup):
        raise TypeError("course must be a CourseGroup")
    if not parsed_nodes:
        raise ValueError("No numbered modules and topics were found in the outline.")
    if replace:
        course.nodes.all().delete()
    return _persist_nodes(course, parsed_nodes)


__all__ = [
    "identify_outline_item",
    "parse_outline_blocks",
    "parse_outline_file",
    "parse_outline_lines",
    "is_course_outline_pdf",
    "save_outline_hierarchy",
]
