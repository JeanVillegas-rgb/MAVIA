"""Permanent source-set finalization for one outline topic."""

from django.utils import timezone


FINALIZED_AT_KEY = "source_review_finalized_at"
FINALIZED_BY_KEY = "source_review_finalized_by"


def source_review_is_finalized(node):
    return bool((node.related_info or {}).get(FINALIZED_AT_KEY))


def finalize_source_review(node, *, user=None):
    """Permanently lock the PDFs and concept connections for ``node``."""
    if source_review_is_finalized(node):
        return node

    related_info = dict(node.related_info or {})
    related_info[FINALIZED_AT_KEY] = timezone.now().isoformat()
    if user is not None and getattr(user, "pk", None):
        related_info[FINALIZED_BY_KEY] = user.pk
    node.related_info = related_info
    node.save(update_fields=["related_info"])
    return node


def source_review_state(node):
    related_info = node.related_info or {}
    return {
        "finalized": source_review_is_finalized(node),
        "finalized_at": related_info.get(FINALIZED_AT_KEY),
    }
