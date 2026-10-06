"""Course-level links: storing what the criteria derive, the page's roll-up, teacher edits.

Mirrors ``publishing.refresh_prerequisites`` and ``teacher_links`` for
``CourseConceptLink``: derived rows are updated in place so ids stay stable,
and a teacher's ``approved`` or ``rejected`` is never overwritten.
"""

from collections import defaultdict

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from lessons.models import LearningObjectGroup

from ..models import CourseConceptLink, LearningPathStep
from .concept_units import concepts_for_topic
from .course_criteria import course_topics, decide_course_pairs
from .reasons import link_reason

# 2026-10-03: two final checks found no rule whose automatic course links can be
# trusted (docs/course-path-closest-evaluation-2026-10-03.md), so every derived
# course link is stored as a suggestion; only a teacher's approval lets it reach
# learners. The rule's own verdict stays in each link's evidence.
DERIVED_STATUS = CourseConceptLink.Status.PENDING


def refresh_course_links(course, embed=None, rule=None):
    """Re-derive the course's cross-topic links as suggestions, keeping every teacher decision."""
    topics = course_topics(course)
    decisions = decide_course_pairs(
        [list(concepts_for_topic(topic)) for topic in topics], embed=embed, rule=rule,
        topic_titles=[topic.title for topic in topics],
    )
    fresh = {(row["prerequisite"].id, row["dependent"].id): row for row in decisions}

    with transaction.atomic():
        existing = {
            (row.prerequisite_id, row.dependent_id): row
            for row in CourseConceptLink.objects.select_for_update().filter(course=course)
        }
        decided = {pair for pair, row in existing.items() if row.status in CourseConceptLink.TEACHER_DECIDED}
        for pair, decision in fresh.items():
            row = existing.get(pair)
            if row is None:
                row, created = CourseConceptLink.objects.get_or_create(
                    prerequisite_id=pair[0],
                    dependent_id=pair[1],
                    defaults={
                        "course": course,
                        "status": DERIVED_STATUS,
                        "source": CourseConceptLink.Source.DERIVED,
                        "evidence": decision["evidence"],
                    },
                )
                if created:
                    continue
            if row.status in CourseConceptLink.TEACHER_DECIDED:
                row.evidence = decision["evidence"]
                row.save(update_fields=["evidence", "updated_at"])
                continue
            CourseConceptLink.objects.filter(pk=row.pk).exclude(
                status__in=CourseConceptLink.TEACHER_DECIDED,
            ).update(
                status=DERIVED_STATUS,
                source=CourseConceptLink.Source.DERIVED,
                evidence=decision["evidence"],
                updated_at=timezone.now(),
            )
        stale = [
            row.pk for pair, row in existing.items()
            if pair not in fresh and row.status not in CourseConceptLink.TEACHER_DECIDED
        ]
        CourseConceptLink.objects.filter(pk__in=stale).exclude(
            status__in=CourseConceptLink.TEACHER_DECIDED,
        ).delete()

    counts = {"accepted": 0, "pending": 0, "teacher_decided": len(decided)}
    for pair in fresh:
        if pair not in decided:
            counts[DERIVED_STATUS] += 1
    return counts


def _concept_titles(topics):
    return {concept.id: concept.title for topic in topics for concept in concepts_for_topic(topic)}


def _topic_steps(topic, titles):
    """``(published, steps)``: the saved path's order, or the topic's current concept order.

    Saved steps outlive an unpublish (regrouping, a failed publish), so they
    count only while the topic is published -- as for students' course links.
    """
    saved = [] if not topic.published else list(
        LearningPathStep.objects.filter(outline_node=topic).order_by("position").values_list("concept_id", "position")
    )
    if saved:
        return True, [
            {"concept_id": concept_id, "title": titles.get(concept_id, "Untitled concept"), "position": position}
            for concept_id, position in saved
        ]
    return False, [
        {"concept_id": concept.id, "title": concept.title or "Untitled concept", "position": index}
        for index, concept in enumerate(concepts_for_topic(topic), start=1)
    ]


def course_path(course):
    """The Course path page: outline topics, and one arrow per pair of topics with links."""
    topics = course_topics(course, with_content=False)
    with_content = {topic.id for topic in course_topics(course)}
    position = {topic.id: index for index, topic in enumerate(topics)}
    titles = _concept_titles([topic for topic in topics if topic.id in with_content])

    arrows = defaultdict(list)
    rows = (
        CourseConceptLink.objects.filter(course=course)
        .exclude(status=CourseConceptLink.Status.REJECTED)
        .select_related("prerequisite", "dependent")
    )
    for row in rows:
        from_topic, to_topic = row.prerequisite.outline_node_id, row.dependent.outline_node_id
        if from_topic not in position or to_topic not in position:
            continue
        before = titles.get(row.prerequisite_id) or row.prerequisite.label or "Untitled concept"
        after = titles.get(row.dependent_id) or row.dependent.label or "Untitled concept"
        arrows[(from_topic, to_topic)].append({
            "id": row.id,
            "status": row.status,
            "prerequisite": {"concept_id": row.prerequisite_id, "title": before, "topic_id": from_topic},
            "dependent": {"concept_id": row.dependent_id, "title": after, "topic_id": to_topic},
            "reason": link_reason(row.evidence, before, after),
            "rank": (row.evidence or {}).get("rank"),
            "contradicts_outline": position[from_topic] > position[to_topic],
        })

    topic_rows = []
    for topic in topics:
        published, steps = _topic_steps(topic, titles) if topic.id in with_content else (False, [])
        topic_rows.append({
            "id": topic.id, "title": topic.title, "position": position[topic.id],
            "has_content": topic.id in with_content, "published": published, "steps": steps,
        })

    return {
        "course": {"id": course.id, "title": course.title},
        "topics": topic_rows,
        "arrows": [
            {
                "from_topic": from_topic,
                "to_topic": to_topic,
                "contradicts_outline": position[from_topic] > position[to_topic],
                "shaping": sum(1 for link in links if link["status"] in CourseConceptLink.SHAPES_PATH),
                "pending": sum(1 for link in links if link["status"] == CourseConceptLink.Status.PENDING),
                "links": sorted(links, key=lambda link: link["id"]),
            }
            for (from_topic, to_topic), links in sorted(arrows.items(), key=lambda item: (position[item[0][0]], position[item[0][1]]))
        ],
    }


class CourseLinkError(ValueError):
    """A teacher's change that cannot be applied; the message is shown as is."""


_INVALID_UNDO = "That undo is not valid. Refresh the page."


def _snapshot(course, pairs):
    rows = {
        (row.prerequisite_id, row.dependent_id): row
        for row in CourseConceptLink.objects.filter(course=course)
    }
    records = []
    for prerequisite_id, dependent_id in pairs:
        row = rows.get((prerequisite_id, dependent_id))
        records.append({
            "prerequisite_id": prerequisite_id,
            "dependent_id": dependent_id,
            "prior": None if row is None else {
                "status": row.status,
                "source": row.source,
                "decided_at": row.decided_at.isoformat() if row.decided_at else None,
            },
        })
    return records


def decide_course_link(course, link_id, status):
    """Approve a suggestion, or reject (remove) any link. Returns the undo record."""
    if status not in CourseConceptLink.TEACHER_DECIDED:
        raise CourseLinkError("Choose approve or reject.")
    row = CourseConceptLink.objects.filter(pk=link_id, course=course).first()
    if row is None:
        raise CourseLinkError("That link no longer exists. Refresh the page.")
    undo = _snapshot(course, [(row.prerequisite_id, row.dependent_id)])
    row.status = status
    row.source = CourseConceptLink.Source.TEACHER
    row.decided_at = timezone.now()
    row.save(update_fields=["status", "source", "decided_at", "updated_at"])
    return undo


def _course_group(course, group_id):
    group = LearningObjectGroup.objects.filter(pk=group_id, outline_node__course=course).first()
    if group is None:
        raise CourseLinkError(_INVALID_UNDO)
    return group


@transaction.atomic
def restore_course_links(course, records):
    """Put each pair back exactly as an undo record says it was."""
    if not isinstance(records, list) or not records:
        raise CourseLinkError("Nothing to undo.")
    for record in records:
        try:
            prerequisite = _course_group(course, int(record["prerequisite_id"]))
            dependent = _course_group(course, int(record["dependent_id"]))
            prior = record["prior"]
        except (KeyError, TypeError, ValueError):
            raise CourseLinkError(_INVALID_UNDO)
        if prior is None:
            CourseConceptLink.objects.filter(prerequisite=prerequisite, dependent=dependent).delete()
            continue
        if (
            not isinstance(prior, dict)
            or prior.get("status") not in CourseConceptLink.Status.values
            or prior.get("source") not in CourseConceptLink.Source.values
            or not isinstance(prior.get("decided_at"), (str, type(None)))
        ):
            raise CourseLinkError(_INVALID_UNDO)
        CourseConceptLink.objects.update_or_create(
            prerequisite=prerequisite,
            dependent=dependent,
            defaults={
                "course": course,
                "status": prior["status"],
                "source": prior["source"],
                "decided_at": parse_datetime(prior["decided_at"]) if prior.get("decided_at") else None,
            },
        )
