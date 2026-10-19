"""Deterministic two-level outline hierarchy rules."""

import re
from pprint import pprint


PARENT_PATTERN = re.compile(
    r"^\s*(?:(?P<label>module|unit|chapter)\s*(?P<label_number>\d+)"
    r"\s*[:.\-\u2013\u2014]?\s*|(?P<number>\d+)[.)]\s*)(?P<title>.+)$",
    re.IGNORECASE,
)
CHILD_PATTERN = re.compile(
    r"^\s*(?:\u2022|-)?\s*(?:(?P<label>lesson|topic)\s*"
    r"(?P<label_number>\d+)\s*[:.\-\u2013\u2014]?\s+|"
    r"(?P<parent_number>\d+)\.(?P<child_number>\d+)[.)]?\s+)"
    r"(?P<title>.+)$",
    re.IGNORECASE,
)


def identify_outline_item(text):

    child_match = CHILD_PATTERN.match(text)
    if child_match:
        number = child_match.group("label_number") or (
            f"{child_match.group('parent_number')}."
            f"{child_match.group('child_number')}"
        )
        return {
            "type": "child",
            "number": number,
            "title": child_match.group("title").strip(),
        }
    parent_match = PARENT_PATTERN.match(text)
    if parent_match:
        return {
            "type": "parent",
            "number": parent_match.group("label_number") or parent_match.group("number"),
            "title": parent_match.group("title").strip(),
        }
    return None


def build_topic_hierarchy(extracted_blocks):
    hierarchy = []
    current_parent = None
    recognized_parents = {}
    parent_order = 0
    child_orders = {}
    for block in extracted_blocks:
        item = identify_outline_item(block["text"])
        if item is None:
            continue
        if item["type"] == "parent":
            parent_key = (item["number"], item["title"].casefold())
            if parent_key in recognized_parents:
                current_parent = recognized_parents[parent_key]
                continue
            item["parent"] = None
            item["depth"] = 0
            item["order"] = parent_order
            parent_order += 1
            recognized_parents[parent_key] = item
            current_parent = item
        else:
            if current_parent is None:
                continue
            item["parent"] = current_parent
            parent_key = (
                current_parent["number"],
                current_parent["title"].casefold(),
            )
            child_order = child_orders.get(parent_key, 0)
            item["depth"] = 1
            item["order"] = child_order
            child_orders[parent_key] = child_order + 1
        hierarchy.append(item)

    return hierarchy
