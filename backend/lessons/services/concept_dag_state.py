from __future__ import annotations

from django.utils import timezone

from lessons.models import CourseGroup, ModuleConceptDAGState, OutlineNode


def get_or_create_module_dag_state(course: CourseGroup, module: OutlineNode) -> ModuleConceptDAGState:
    state, _ = ModuleConceptDAGState.objects.get_or_create(
        course=course,
        module_node=module,
    )
    return state


def invalidate_module_dag(course: CourseGroup, module: OutlineNode, reason: str) -> ModuleConceptDAGState:
    state = get_or_create_module_dag_state(course, module)
    state.is_confirmed = False
    state.confirmed_at = None
    state.invalidated_at = timezone.now()
    state.invalidation_reason = reason
    state.full_clean()
    state.save(
        update_fields=[
            "is_confirmed",
            "confirmed_at",
            "invalidated_at",
            "invalidation_reason",
            "updated_at",
        ]
    )
    return state


def confirm_module_dag(course: CourseGroup, module: OutlineNode) -> ModuleConceptDAGState:
    state = get_or_create_module_dag_state(course, module)
    state.is_confirmed = True
    state.confirmed_at = timezone.now()
    state.invalidated_at = None
    state.invalidation_reason = ""
    state.full_clean()
    state.save(
        update_fields=[
            "is_confirmed",
            "confirmed_at",
            "invalidated_at",
            "invalidation_reason",
            "updated_at",
        ]
    )
    return state
