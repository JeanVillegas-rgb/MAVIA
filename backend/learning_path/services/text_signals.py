"""Recognising the chunker's "(Part 1 of 3)" pieces, so split passages join up again."""

import re

_PART_SUFFIX = re.compile(r"\s*\(\s*part\s+\d+\s+of\s+\d+\s*\)\s*$", re.IGNORECASE)


def strip_part_suffix(title):
    """Drop the chunker's "(Part 1 of 3)" marker so split pieces share a name."""
    return _PART_SUFFIX.sub("", title or "").strip()


def part_marker(title):
    """Return ``(base_title, part_number, part_total)`` for a split chunk, else ``None``."""
    match = _PART_SUFFIX.search(title or "")
    if not match:
        return None
    numbers = re.findall(r"\d+", match.group(0))
    if len(numbers) != 2:
        return None
    return strip_part_suffix(title), int(numbers[0]), int(numbers[1])
