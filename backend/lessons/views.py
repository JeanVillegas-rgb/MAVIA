import logging
from collections import defaultdict
from datetime import timedelta
import threading
from time import perf_counter

from django.db import transaction
from django.db.models import Max, Prefetch
from django.utils import timezone
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from user.permissions import IsTeacherOrAdmin

from course.models import LessonVariant
from course.bulk_version_generation import classify_all_source_versions
from course.services import sync_course_outline
from course.variant_generator import (
    _fingerprint as version_fingerprint,
    fill_missing_slots,
    generate_standalone_variants,
)
from question_generation.models import GenerationRun
from .services.topic_publish import confirmed_materials_for, run_topic_publish
from course.version_assignment import (
    assign_group_versions,
    assign_source_as_representative,
    assign_source_to_slot,
    bundle_role_provenance,
    prune_bundle_role,
    release_from_group,
)
from .services.concept_bundles import (
    bundle_label,
    bundle_text,
    bundles_for_group,
    material_order,
)

from .models import (
    CourseGroup,
    LearningMaterial,
    LearningObject,
    LearningObjectGroup,
    LearningObjectMatchSuggestion,
    OutlineNode,
    Question,
    QuestionLearningObjectLink,
)
from .features.pdf_processing.serializers import (
    CourseOutlineUploadInputSerializer,
    CoursePdfUploadInputSerializer,
    LearningMaterialUploadInputSerializer,
)
from .features.pdf_processing.use_cases import (
    PdfProcessingUseCaseError,
    confirm_course_outline,
    regenerate_learning_material,
    upload_course_outline,
    upload_course_pdf,
    upload_learning_material,
)
from .serializers import (
    CourseCreateSerializer,
    CourseDetailSerializer,
    CourseListSerializer,
    LearningMaterialSerializer,
    LearningObjectMutationSerializer,
    LearningObjectReorderSerializer,
    LearningObjectMatchSuggestionSerializer,
    LearningObjectSerializer,
    OutlineNodeMutationSerializer,
    OutlineNodeSerializer,
    QuestionSerializer,
)
from .services.audio_generator import AudioGenerationError, generate_material_audio_playlist
from .services.content_generator import (
    apply_classification_override,
    build_lesson_playlist,
    build_narration_script_from_learning_objects,
    is_structural_metadata_label,
)
from .services.instructional_content_classifier import CLASSIFICATION_CATEGORIES
from .services.image_describer import populate_missing_image_descriptions
from .services.learning_resource_linker import (
    CONFIRMED_QUESTION_PAIRING_STATUSES,
    learning_objects_are_confirmed,
    learning_object_match_debug_configuration,
    question_pairing_debug_configuration,
    question_snapshots,
    record_teacher_match_decision,
    refresh_material_learning_relationships,
    refresh_question_learning_object_links,
)
from .services.unit_matching import place_unit, unpublish_topic
from .services.regrouping import (
    RegroupingUnavailable,
    apply_regrouping,
    changed_learning_objects,
    propose_regrouping,
)
from .services.question_workflow import (
    delete_generated_questions_for,
    duplicate_for_topic,
    duplicate_in_topic,
    enriched_question_values,
    question_fingerprint,
    sync_question_to_adaptive,
)


logger = logging.getLogger(__name__)


def _active_topic_run(outline_node, *, stale_after=timedelta(minutes=30)):
    """Return a live topic run and close runs that stopped reporting progress."""
    cutoff = timezone.now() - stale_after
    for run in GenerationRun.objects.filter(
        outline_node=outline_node,
        status="running",
    ).order_by("-id"):
        latest_event = run.events.order_by("-created_at").first()
        last_activity = latest_event.created_at if latest_event else run.started_at
        if last_activity >= cutoff:
            return run

        from question_generation.models import GenerationEvent

        next_seq = (run.events.aggregate(max_seq=Max("seq"))["max_seq"] or 0) + 1
        GenerationEvent.objects.create(
            run=run,
            seq=next_seq,
            event_type="topic_task_abandoned",
            message=(
                "This task stopped reporting progress and was closed automatically "
                "so it can be retried."
            ),
        )
        run.status = "failed"
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "finished_at"])
    return None


def _run_topic_publish_in_background(run_id, course_id, node_id, set_confirmed):
    """Thread entry point. Owns the run's lifecycle and nothing else."""
    from question_generation.models import GenerationEvent

    run = GenerationRun.objects.get(id=run_id)
    seq = {"n": 0}

    def record(event_type, message, **data):
        seq["n"] += 1
        GenerationEvent.objects.create(
            run=run, seq=seq["n"], event_type=event_type, message=message, data=data or None
        )

    try:
        course = CourseGroup.objects.get(id=course_id)
        node = OutlineNode.objects.get(id=node_id)
        summary = run_topic_publish(
            course, node,
            set_confirmed=lambda material: set_confirmed(material, True),
            on_event=record,
        )
        run.status = "finished"
    except Exception as exc:  # a failed publish must not leave the run "running" forever
        logger.exception("Publish run %s failed", run_id)
        record("publish_failed", str(exc))
        run.status = "failed"
    finally:
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "finished_at"])


def _run_all_versions_in_background(run_id, node_id):
    """Classify all existing PDF variants while recording pollable progress."""
    from question_generation.models import GenerationEvent

    run = GenerationRun.objects.get(id=run_id)
    seq = {"n": 0}

    def record(event_type, message, **data):
        seq["n"] += 1
        GenerationEvent.objects.create(
            run=run, seq=seq["n"], event_type=event_type, message=message, data=data or None
        )

    try:
        node = OutlineNode.objects.get(id=node_id)
        classify_all_source_versions(node, on_event=record)
        run.status = "finished"
    except Exception as exc:
        logger.exception("Bulk version run %s failed", run_id)
        record("versions_bulk_failed", str(exc))
        run.status = "failed"
    finally:
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "finished_at"])


class SuggestionAcceptError(ValueError):
    """A match suggestion accept that cannot be carried out as asked."""


class CourseGroupViewSet(viewsets.ModelViewSet):
    permission_classes = [IsTeacherOrAdmin]
    queryset = CourseGroup.objects.prefetch_related(
        "nodes",
        "outlines",
        "materials__learning_objects",
        "materials__questions__learning_object_links__learning_object",
    ).all()
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_serializer_class(self):
        if self.action in {"list"}:
            return CourseListSerializer
        if self.action == "create":
            return CourseCreateSerializer
        return CourseDetailSerializer

    @action(detail=True, methods=["get"], url_path="review/modules", permission_classes=[permissions.IsAuthenticated])
    def review_modules(self, request, pk=None):
        if request.user.role not in {"TEACHER", "ADMIN"}:
            return Response({"detail": "Teacher or admin access required."}, status=403)
        from .services.lesson_package import build_course_module_summaries
        return Response(build_course_module_summaries(self.get_object()))

    @action(detail=True, methods=["get"], url_path=r"review/modules/(?P<module_id>[^/.]+)/package", permission_classes=[permissions.IsAuthenticated])
    def review_module_package(self, request, pk=None, module_id=None):
        if request.user.role not in {"TEACHER", "ADMIN"}:
            return Response({"detail": "Teacher or admin access required."}, status=403)
        from .services.lesson_package import build_module_package
        module = self._get_module_node(self.get_object(), module_id)
        if module is None:
            return Response({"detail": "Module not found in this course."}, status=404)
        return Response(build_module_package(module))

    def _get_module_node(self, course, module_id):
        try:
            module = course.nodes.get(pk=module_id, parent__isnull=True)
        except OutlineNode.DoesNotExist:
            return None
        return module

    @action(detail=True, methods=["get"], url_path="modules")
    def modules(self, request, pk=None):
        course = self.get_object()
        modules = course.nodes.filter(parent__isnull=True).order_by("order", "id")
        return Response(OutlineNodeSerializer(modules, many=True, context={"request": request}).data)

    def _learning_resources_payload(self, node, request):
        started_at = perf_counter()
        learning_object_queryset = (
            LearningObject.objects.filter(
                material__generated_json__learning_objects_confirmed=True,
            )
            .select_related("material")
            .order_by("material_id", "order", "id")
        )
        group_queryset = node.learning_object_groups.prefetch_related(
            Prefetch(
                "learning_objects",
                queryset=learning_object_queryset,
                to_attr="resource_learning_objects",
            )
        ).order_by("id")
        has_confirmed_learning_objects = learning_object_queryset.filter(
            material__outline_node=node,
        ).exists()
        confirmed_link_queryset = (
            QuestionLearningObjectLink.objects.filter(
                learning_object__material__generated_json__learning_objects_confirmed=True,
            )
            .select_related("learning_object__group")
            .order_by("-is_primary", "-relevance_score", "id")
        )
        all_questions = list(
            Question.objects.filter(material__outline_node=node)
            .select_related("material")
            .prefetch_related(
                Prefetch("learning_object_links", queryset=confirmed_link_queryset)
            )
            .order_by("material_id", "order", "id")
        ) if has_confirmed_learning_objects else []
        confirmed_questions_by_group = defaultdict(list)
        for question in all_questions:
            group_ids = {
                link.learning_object.group_id
                for link in question.learning_object_links.all()
                if link.review_status in CONFIRMED_QUESTION_PAIRING_STATUSES
                and link.learning_object.group_id is not None
            }
            for group_id in group_ids:
                confirmed_questions_by_group[group_id].append(question)

        order_index = {
            material_id: index for index, material_id in enumerate(material_order(node))
        }

        groups = []
        for group in group_queryset:
            learning_objects = [
                item
                for item in group.resource_learning_objects
                if not is_structural_metadata_label(item.title)
            ]
            if not learning_objects:
                continue
            version_state = assign_group_versions(group)
            group_bundles = bundles_for_group(group)
            # Shown per PDF. Built from the same rows as the flat list -- only
            # confirmed files, no extraction leftovers -- so the two views of a
            # concept cannot disagree, and ordered as `bundles_for_group` does.
            display_bundles = defaultdict(list)
            for item in learning_objects:
                display_bundles[item.material_id].append(item)
            for objects in display_bundles.values():
                objects.sort(key=lambda item: (item.order, item.id))
            slot_rows = {}
            extra_rows = []
            # A version a PDF supplies *is* its bundle's objects -- nothing is
            # copied into a LessonVariant row -- so the roles are read first and
            # the variant table only fills what was generated. Reading the table
            # alone left such a slot empty and the concept forever "incomplete".
            role_provenance = bundle_role_provenance(group)
            normal_material = version_state.get("normal_material_id")
            normal_objects = [
                item
                for item in group_bundles.get(normal_material, [])
                if (item.content or "").strip()
            ]

            def object_rows(objects):
                """Which objects a version is made of, in the order it reads.

                The review screen shows every object exactly once, under the
                role it actually plays. Without this it had no way to tell a
                member of the Normal bundle from a bundle supplying another
                version, and labelled both "Other variation".
                """
                return [
                    {
                        "id": item.id,
                        "title": item.title,
                        "material": item.material_id,
                        "kind": item.kind,
                        "text": item.content or "",
                    }
                    for item in objects
                ]

            # Normal is a role like the others: the bundle the concept is
            # taught as. It used to be absent from the payload entirely, so the
            # screen fell back to the bundle's lead object and dropped the rest.
            if normal_objects:
                slot_rows["normal"] = {
                    "id": None,
                    "material": normal_material,
                    "text": bundle_text(normal_objects),
                    "origin": LessonVariant.Origin.SOURCE_PDF,
                    "source": "pdf",
                    "assigned_by": role_provenance.get(normal_material, ""),
                    "source_learning_object_id": normal_objects[0].id,
                    "objects": object_rows(normal_objects),
                    "stale": False,
                }

            for material_id, role in (version_state.get("bundle_roles") or {}).items():
                supplied = [
                    item
                    for item in group_bundles.get(material_id, [])
                    if (item.content or "").strip()
                ]
                if not supplied:
                    continue
                entry = {
                    "id": None,
                    "material": material_id,
                    "text": bundle_text(supplied),
                    "origin": LessonVariant.Origin.SOURCE_PDF,
                    "source": "pdf",
                    "assigned_by": role_provenance.get(material_id, ""),
                    "source_learning_object_id": supplied[0].id,
                    "objects": object_rows(supplied),
                    # Teacher text, never written from the Normal wording, so
                    # it cannot go stale the way a generated version does.
                    "stale": False,
                }
                if role == "EXTRA":
                    extra_rows.append(entry)
                else:
                    slot_rows[role.lower()] = entry

            if normal_objects:
                # A generated version is written one object of the Normal
                # bundle at a time, so it is read back the same way. Reading
                # only the lead's row showed one segment of four.
                by_id = {item.id: item for item in normal_objects}
                position = {item.id: index for index, item in enumerate(normal_objects)}
                generated = defaultdict(list)
                for row in LessonVariant.objects.filter(learning_object__in=normal_objects):
                    generated[row.variant].append(row)
                for variant, rows in generated.items():
                    rows.sort(key=lambda row: position.get(row.learning_object_id, len(position)))
                    if variant == "EXTRA":
                        # Extras are kept for the learning-path component to
                        # rule on; they are not one of the three slots a
                        # student is offered, so they travel as their own list.
                        extra_rows.extend({
                            "id": row.id,
                            "text": row.narration,
                            "origin": row.origin,
                            "source": "generated",
                            "assigned_by": row.assigned_by,
                            "source_learning_object_id": row.source_learning_object_id,
                            "objects": object_rows([by_id[row.learning_object_id]]),
                            "stale": False,
                        } for row in rows)
                        continue
                    # A version short of its bundle is reported missing rather
                    # than served: three quarters of a version reads as a whole
                    # one to a learner who cannot see the page.
                    if len(rows) < len(normal_objects):
                        continue
                    entry = {
                        # The first segment's row, so the existing edit and
                        # regenerate controls keep working unchanged.
                        "id": rows[0].id,
                        "material": None,
                        "text": "\n".join(
                            row.narration.strip() for row in rows if row.narration.strip()
                        ),
                        "origin": rows[0].origin,
                        "source": "generated",
                        "assigned_by": rows[0].assigned_by,
                        "source_learning_object_id": rows[0].source_learning_object_id,
                        # Each segment carries the wording that was *written*,
                        # not the object it was written from -- showing the
                        # source text under an Elaborated heading would be
                        # printing the Normal version a second time.
                        "objects": [
                            {
                                **object_rows([by_id[row.learning_object_id]])[0],
                                "text": row.narration,
                            }
                            for row in rows
                        ],
                        # Written from different text than the object has now.
                        # Publishing refuses these until a teacher checks them.
                        "stale": any(
                            row.origin == LessonVariant.Origin.GENERATED
                            and row.source_fingerprint
                            and row.source_fingerprint
                            != version_fingerprint(by_id[row.learning_object_id])
                            for row in rows
                        ),
                    }
                    # A bundle that already supplies this role keeps it: the
                    # PDF's own wording outranks a leftover generated row.
                    slot_rows.setdefault(variant.lower(), entry)
            groups.append(
                {
                    "id": group.id,
                    "label": group.label,
                    "outline_node_id": node.id,
                    "versions": {
                        "representative_id": version_state["representative_id"],
                        "original_selected": version_state.get("original_selected", False),
                        "classification_complete": version_state.get("classification_complete", True),
                        "slots": slot_rows,
                        "extras": extra_rows,
                        "needs_confirmation": version_state["needs_confirmation"],
                        # A primary role counts as done whether a PDF supplies
                        # it or a generator wrote it.
                        "complete": {"simplified", "elaborated"}.issubset(slot_rows.keys()),
                    },
                    # One block per PDF, in upload order, so the teacher sees
                    # what each file contributed to this concept as a unit.
                    "bundles": [
                        {
                            "material": material_id,
                            "role": version_state["bundle_roles"].get(
                                material_id,
                                "NORMAL" if material_id == version_state["normal_material_id"] else None,
                            ),
                            "learning_objects": LearningObjectSerializer(
                                objects, many=True, context={"request": request},
                            ).data,
                        }
                        for material_id, objects in sorted(
                            display_bundles.items(),
                            key=lambda pair: order_index.get(pair[0], len(order_index)),
                        )
                    ],
                    "learning_objects": LearningObjectSerializer(
                        learning_objects,
                        many=True,
                        context={"request": request},
                    ).data,
                    "questions": QuestionSerializer(
                        confirmed_questions_by_group[group.id],
                        many=True,
                        context={"request": request},
                    ).data,
                }
            )
        payload = {
            "grouping_warnings": [
                material.generated_json["grouping_warning"]
                for material in node.materials.all()
                if (material.generated_json or {}).get("grouping_warning")
            ],
            "outline_node": OutlineNodeSerializer(node, context={"request": request}).data,
            # A database comparison only -- no model runs here -- so the page
            # knows whether "Review grouping changes" is worth enabling.
            "regrouping": {"changed_count": len(changed_learning_objects(node))},
            "learning_object_groups": groups,
            "matching_debug": learning_object_match_debug_configuration(),
            "question_pairing_debug": question_pairing_debug_configuration(),
            "question_pairings": QuestionSerializer(
                all_questions,
                many=True,
                context={"request": request},
            ).data,
            "match_suggestions": LearningObjectMatchSuggestionSerializer(
                node.learning_object_match_suggestions.filter(
                    status=LearningObjectMatchSuggestion.Status.PENDING,
                    source_learning_object__material__generated_json__learning_objects_confirmed=True,
                    candidate_learning_object__material__generated_json__learning_objects_confirmed=True,
                ).select_related(
                    "source_learning_object__material",
                    "candidate_learning_object__material",
                ),
                many=True,
                context={"request": request},
            ).data,
        }

        elapsed_ms = round((perf_counter() - started_at) * 1000, 1)
        payload["debug"] = {
            "payload_build_ms": elapsed_ms,
            "group_count": len(groups),
            "question_count": len(all_questions),
            "suggestion_count": len(payload["match_suggestions"]),
        }
        logger.info(
            "Learning-resource payload: node=%s duration_ms=%.1f groups=%s questions=%s suggestions=%s",
            node.id,
            elapsed_ms,
            len(groups),
            len(all_questions),
            len(payload["match_suggestions"]),
        )
        return payload

    def _refresh_relationship_snapshots(self, materials, *, recompute=True):
        """Keep the API handoff snapshot aligned with teacher grouping changes."""
        for material in materials:
            if recompute:
                refresh_material_learning_relationships(material)
            generated_json = material.generated_json or {}
            generated_json["questions"] = question_snapshots(material)
            generated_json["learning_objects"] = self._learning_objects_snapshot(material)
            material.generated_json = generated_json
            material.save(update_fields=["generated_json"])

    @action(
        detail=True,
        methods=["get"],
        url_path=r"outline-nodes/(?P<node_id>[^/.]+)/learning-resources",
    )
    def node_learning_resources(self, request, pk=None, node_id=None):
        course = self.get_object()
        try:
            node = course.nodes.get(pk=node_id)
        except OutlineNode.DoesNotExist:
            return Response(
                {"detail": "Outline node not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(self._learning_resources_payload(node, request))

    @action(
        detail=True,
        methods=["post"],
        url_path=r"outline-nodes/(?P<node_id>[^/.]+)/connect-learning-objects",
    )
    def connect_learning_objects(self, request, pk=None, node_id=None):
        """Connect equivalent objects without assigning a difficulty label."""
        course = self.get_object()
        try:
            node = course.nodes.get(pk=node_id)
        except OutlineNode.DoesNotExist:
            return Response(
                {"detail": "Outline node not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        raw_ids = request.data.get("learning_object_ids")
        if not isinstance(raw_ids, list) or len(set(raw_ids)) < 2:
            return Response(
                {"detail": "Provide at least two learning_object_ids."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            object_ids = {int(value) for value in raw_ids}
        except (TypeError, ValueError):
            return Response(
                {"detail": "Every learning_object_id must be an integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        learning_objects = list(
            LearningObject.objects.filter(
                id__in=object_ids,
                material__course=course,
            ).select_related("material", "group")
        )
        if len(learning_objects) != len(object_ids):
            return Response(
                {"detail": "One or more learning objects were not found in this course."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if any(item.material.outline_node_id != node.id for item in learning_objects):
            return Response(
                {"detail": "Connected learning objects must belong to this same outline node."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if any(not learning_objects_are_confirmed(item.material) for item in learning_objects):
            return Response(
                {"detail": "Confirm every selected learning object before reviewing connections."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        target_group = next(
            (
                item.group
                for item in learning_objects
                if item.group_id and item.group.outline_node_id == node.id
            ),
            None,
        )
        if target_group is None:
            requested_label = str(request.data.get("label") or "").strip()
            target_group = LearningObjectGroup.objects.create(
                outline_node=node,
                label=(requested_label or learning_objects[0].title)[:255],
                version_selection={"label_locked": True} if requested_label else {},
            )
        elif request.data.get("label"):
            target_group.label = str(request.data["label"]).strip()[:255]
            target_group.version_selection = {
                **(target_group.version_selection or {}),
                "label_locked": True,
            }
            target_group.save(update_fields=["label", "version_selection"])

        old_group_ids = {
            item.group_id
            for item in learning_objects
            if item.group_id and item.group_id != target_group.id
        }
        # An object pulled out of another group leaves its companions there;
        # undo its version links with them before it moves.
        for item in learning_objects:
            if item.group_id and item.group_id != target_group.id:
                staying = [
                    member for member in item.group.learning_objects.all()
                    if member.id not in object_ids
                ]
                release_from_group(item, staying)
        LearningObject.objects.filter(id__in=object_ids).update(group=target_group)
        # The teacher grouped these as they read now, so later edits are
        # measured from this point rather than from their original text.
        for item in learning_objects:
            item.mark_grouping_current()
        LearningObject.objects.bulk_update(learning_objects, ["grouping_content_hash"])
        if old_group_ids:
            LearningObjectGroup.objects.filter(
                id__in=old_group_ids,
                learning_objects__isnull=True,
            ).delete()
        self._refresh_relationship_snapshots(
            {item.material for item in learning_objects}
        )
        for index, first in enumerate(learning_objects):
            for second in learning_objects[index + 1:]:
                record_teacher_match_decision(first, second, accepted=True)

        return Response(self._learning_resources_payload(node, request))

    @action(
        detail=True,
        methods=["post"],
        url_path=r"outline-nodes/(?P<node_id>[^/.]+)/separate-learning-object",
    )
    def separate_learning_object(self, request, pk=None, node_id=None):
        """Move one object into its own neutral group without classifying it."""
        course = self.get_object()
        try:
            node = course.nodes.get(pk=node_id)
        except OutlineNode.DoesNotExist:
            return Response(
                {"detail": "Outline node not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        raw_id = request.data.get("learning_object_id")
        try:
            object_id = int(raw_id)
        except (TypeError, ValueError):
            return Response(
                {"detail": "learning_object_id must be an integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            learning_object = LearningObject.objects.select_related(
                "material", "group"
            ).get(
                pk=object_id,
                material__course=course,
                material__outline_node=node,
            )
        except LearningObject.DoesNotExist:
            return Response(
                {"detail": "Learning object was not found in this outline node."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not learning_objects_are_confirmed(learning_object.material):
            return Response(
                {"detail": "Confirm this learning object before reviewing connections."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        old_group = learning_object.group
        old_group_members = list(
            old_group.learning_objects.exclude(pk=learning_object.id).select_related("material")
        ) if old_group else []
        if old_group and old_group.learning_objects.count() == 1:
            return Response(self._learning_resources_payload(node, request))

        # Undo the old group's version links first, or the object stays "taught
        # through" an original it no longer shares a concept with.
        release_from_group(learning_object, old_group_members)
        new_group = LearningObjectGroup.objects.create(
            outline_node=node,
            label=learning_object.title[:255],
        )
        learning_object.group = new_group
        learning_object.mark_grouping_current()
        learning_object.save(update_fields=["group", "grouping_content_hash"])
        if old_group and not old_group.learning_objects.exists():
            old_group.delete()

        for old_member in old_group_members:
            record_teacher_match_decision(
                learning_object,
                old_member,
                accepted=False,
            )
        self._refresh_relationship_snapshots(
            {learning_object.material, *(member.material for member in old_group_members)},
            recompute=False,
        )
        return Response(self._learning_resources_payload(node, request))

    def _node_and_object(self, course, node_id, object_id):
        """Resolve one of this course's objects, or say which half was missing."""
        try:
            node = course.nodes.get(pk=node_id)
            learning_object = LearningObject.objects.select_related("material", "group").get(
                pk=object_id, material__outline_node=node,
            )
        except (OutlineNode.DoesNotExist, LearningObject.DoesNotExist):
            return None, None
        return node, learning_object

    def _leave_old_group(self, learning_object):
        """Who stays behind in the concept this object is leaving.

        Call this **before** the object's group changes. Undoing the old
        concept's version links is ``place_unit``'s job -- it does the same for
        the automatic and accept-a-card paths, which do not come through here
        -- so this only reports the companions the caller still has to record a
        teacher decision against.
        """
        old_group = learning_object.group
        return list(
            old_group.learning_objects.exclude(pk=learning_object.id).select_related("material")
        ) if old_group else []

    def _bundle_correction_response(self, node, learning_object, request, companions=()):
        self._refresh_relationship_snapshots(
            {learning_object.material, *(member.material for member in companions)},
            recompute=False,
        )
        return Response(self._learning_resources_payload(node, request))

    @action(
        detail=True,
        methods=["post"],
        url_path=r"outline-nodes/(?P<node_id>[^/.]+)/learning-objects/(?P<object_id>[^/.]+)/move-out",
    )
    def move_learning_object_out(self, request, pk=None, node_id=None, object_id=None):
        """Take one object out of its bundle and let it teach a concept alone."""
        course = self.get_object()
        node, learning_object = self._node_and_object(course, node_id, object_id)
        if learning_object is None:
            return Response({"detail": "Learning object not found."}, status=status.HTTP_404_NOT_FOUND)
        if not learning_objects_are_confirmed(learning_object.material):
            return Response(
                {"detail": "Confirm this learning object before reviewing connections."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if learning_object.group_id and learning_object.group.learning_objects.count() == 1:
            return Response(self._learning_resources_payload(node, request))
        companions = self._leave_old_group(learning_object)
        target = LearningObjectGroup.objects.create(
            outline_node=node,
            # One object on its own keeps its own title (design 3.5):
            # naming it after the heading it sat under gave every object of a
            # shared section the same concept name.
            label=bundle_label([learning_object])[:255],
        )
        place_unit([learning_object], target)
        # A teacher breaking a bundle up is a decision, not a gap in the
        # evidence: without recording it the next automatic pass would place
        # the object straight back. Only cross-PDF pairs are decided -- two
        # objects of one file say nothing about whether the files agree.
        for companion in companions:
            if companion.material_id != learning_object.material_id:
                record_teacher_match_decision(learning_object, companion, accepted=False)
        unpublish_topic(node)
        return self._bundle_correction_response(node, learning_object, request, companions)

    @action(
        detail=True,
        methods=["post"],
        url_path=r"outline-nodes/(?P<node_id>[^/.]+)/learning-objects/(?P<object_id>[^/.]+)/move-to",
    )
    def move_learning_object_to(self, request, pk=None, node_id=None, object_id=None):
        """Move one object into another concept of the same topic."""
        course = self.get_object()
        node, learning_object = self._node_and_object(course, node_id, object_id)
        if learning_object is None:
            return Response({"detail": "Learning object not found."}, status=status.HTTP_404_NOT_FOUND)
        if not learning_objects_are_confirmed(learning_object.material):
            return Response(
                {"detail": "Confirm this learning object before reviewing connections."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            group_id = int(request.data.get("group_id"))
        except (TypeError, ValueError):
            return Response(
                {"detail": "group_id must be an integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Scoped to the node: a concept from another topic would carry the
        # object out of the lesson it belongs to.
        target = node.learning_object_groups.filter(pk=group_id).first()
        if target is None:
            return Response(
                {"detail": "That concept is not part of this topic."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        companions = []
        if learning_object.group_id != target.id:
            companions = self._leave_old_group(learning_object)
            place_unit([learning_object], target)
            unpublish_topic(node)
        return self._bundle_correction_response(node, learning_object, request, companions)

    @action(
        detail=True,
        methods=["post"],
        url_path=r"outline-nodes/(?P<node_id>[^/.]+)/learning-objects/(?P<object_id>[^/.]+)/reorder",
    )
    def reorder_learning_object_in_bundle(self, request, pk=None, node_id=None, object_id=None):
        """Change one object's place in its bundle, swapping with its neighbour.

        Only its own bundle moves: order is what the PDF that wrote these
        objects says, so an object never steps over another file's text.
        """
        course = self.get_object()
        node, learning_object = self._node_and_object(course, node_id, object_id)
        if learning_object is None:
            return Response({"detail": "Learning object not found."}, status=status.HTTP_404_NOT_FOUND)
        if not learning_objects_are_confirmed(learning_object.material):
            return Response(
                {"detail": "Confirm this learning object before reviewing connections."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        direction = request.data.get("direction")
        if direction not in ("up", "down"):
            return Response(
                {"detail": "direction must be \"up\" or \"down\"."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if learning_object.group_id is None:
            return Response(self._learning_resources_payload(node, request))
        siblings = [
            item
            for item in bundles_for_group(learning_object.group).get(learning_object.material_id, [])
        ]
        position = next(
            (index for index, item in enumerate(siblings) if item.id == learning_object.id),
            None,
        )
        if position is None:
            return Response(self._learning_resources_payload(node, request))
        neighbour_index = position - 1 if direction == "up" else position + 1
        if neighbour_index < 0 or neighbour_index >= len(siblings):
            # Already at the edge of its bundle; the teacher still gets the
            # current state back rather than an error they cannot act on.
            return Response(self._learning_resources_payload(node, request))
        moved = list(siblings)
        moved[position], moved[neighbour_index] = moved[neighbour_index], moved[position]
        # Renumber rather than swap the two values. Swapping is a no-op when
        # two objects share an `order` (the tie is broken by id), and nudging
        # one value clear of the other can walk `order` below zero, which the
        # PositiveIntegerField does not allow.
        #
        # The bundle is renumbered over the places it already occupies, not
        # 0..n-1: `order` is the whole PDF's document order, and a file's other
        # concepts hold the places in between. Taking 0..n-1 here would move
        # this bundle in front of them and rewrite the lesson's reading and
        # audio order material-wide.
        places = []
        for value in sorted(item.order for item in siblings):
            places.append(max(value, places[-1] + 1) if places else value)
        for place, item in zip(places, moved):
            if item.order != place:
                LearningObject.objects.filter(pk=item.id).update(order=place)
        return self._bundle_correction_response(node, learning_object, request)

    @action(
        detail=True,
        methods=["get"],
        url_path=r"outline-nodes/(?P<node_id>[^/.]+)/regrouping",
    )
    def regrouping_preview(self, request, pk=None, node_id=None):
        """Where each edited object now belongs. Proposes only; changes nothing."""
        course = self.get_object()
        try:
            node = course.nodes.get(pk=node_id)
        except OutlineNode.DoesNotExist:
            return Response({"detail": "Outline node not found."}, status=status.HTTP_404_NOT_FOUND)
        try:
            proposals = propose_regrouping(node)
        except RegroupingUnavailable as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response({"proposals": proposals, "published": node.published})

    @action(
        detail=True,
        methods=["post"],
        url_path=r"outline-nodes/(?P<node_id>[^/.]+)/regrouping/apply",
    )
    def regrouping_apply(self, request, pk=None, node_id=None):
        """Carry out the proposals the teacher ticked."""
        course = self.get_object()
        try:
            node = course.nodes.get(pk=node_id)
        except OutlineNode.DoesNotExist:
            return Response({"detail": "Outline node not found."}, status=status.HTTP_404_NOT_FOUND)

        raw_ids = request.data.get("learning_object_ids", [])
        if not isinstance(raw_ids, list):
            return Response(
                {"detail": "learning_object_ids must be a list."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            chosen = [int(value) for value in raw_ids]
        except (TypeError, ValueError):
            return Response(
                {"detail": "Every learning_object_id must be an integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            summary = apply_regrouping(node, chosen)
        except RegroupingUnavailable as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        if summary["affected_material_ids"]:
            # Recompute, unlike Connect: a separated object may now have a
            # pairing worth suggesting, and question links follow the groups.
            self._refresh_relationship_snapshots(
                set(LearningMaterial.objects.filter(pk__in=summary["affected_material_ids"]))
            )
        node.refresh_from_db()
        return Response({
            "summary": summary,
            "resources": self._learning_resources_payload(node, request),
        })

    def _get_node_match_suggestion(self, course, node, suggestion_id):
        try:
            return LearningObjectMatchSuggestion.objects.select_related(
                "source_learning_object__material",
                "source_learning_object__group",
                "candidate_learning_object__material",
                "candidate_learning_object__group",
            ).get(
                pk=suggestion_id,
                outline_node=node,
                source_learning_object__material__course=course,
                candidate_learning_object__material__course=course,
            )
        except LearningObjectMatchSuggestion.DoesNotExist:
            return None

    @staticmethod
    def _record_suggestion_decision(suggestion, source, candidate, *, accepted):
        """Record a teacher decision on a suggestion, including unit suggestions.

        ``record_teacher_match_decision`` ignores pairs of different kinds, and
        a unit suggestion may pair an image with text; its row is written
        directly with the same evidence keys, keeping its extras.
        """
        if not (suggestion.source_extra_ids or suggestion.candidate_extra_ids):
            if record_teacher_match_decision(source, candidate, accepted=accepted) is not None:
                return
        suggestion.status = (
            LearningObjectMatchSuggestion.Status.ACCEPTED
            if accepted
            else LearningObjectMatchSuggestion.Status.REJECTED
        )
        suggestion.evidence = {
            **(suggestion.evidence or {}),
            "teacher_reviewed": True,
            "teacher_decision": "accepted" if accepted else "rejected",
        }
        suggestion.save(update_fields=["status", "evidence", "updated_at"])

    @action(
        detail=True,
        methods=["post"],
        url_path=r"outline-nodes/(?P<node_id>[^/.]+)/match-suggestions/(?P<suggestion_id>[^/.]+)/accept",
    )
    def accept_match_suggestion(self, request, pk=None, node_id=None, suggestion_id=None):
        course = self.get_object()
        try:
            node = course.nodes.get(pk=node_id)
        except OutlineNode.DoesNotExist:
            return Response(
                {"detail": "Outline node not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        suggestion = self._get_node_match_suggestion(course, node, suggestion_id)
        if suggestion is None:
            return Response(
                {"detail": "Match suggestion not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        source = suggestion.source_learning_object
        candidate = suggestion.candidate_learning_object
        if not all(
            learning_objects_are_confirmed(item.material)
            for item in (source, candidate)
        ):
            return Response(
                {"detail": "Confirm both learning objects before reviewing this connection."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            unpublished = self._accept_suggestion(node, suggestion, source, candidate)
        except SuggestionAcceptError as exc:
            # Raised inside the transaction, so nothing was moved or saved.
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        # Placement unpublishes with an UPDATE, so the cached node is stale and
        # the payload would still report the topic as published.
        node.refresh_from_db()
        payload = self._learning_resources_payload(node, request)
        payload["unpublished"] = unpublished
        return Response(payload)

    @transaction.atomic
    def _accept_suggestion(self, node, suggestion, source, candidate):
        """Place (for units), connect and record the decision in one transaction."""
        unpublished = False
        if suggestion.source_extra_ids or suggestion.candidate_extra_ids:
            from .services.unit_matching import (
                place_unit,
                rejected_across_sides,
                unpublish_topic,
            )

            # Each side is its own PDF's run, so an extra id belongs to the
            # material of the side that lists it. Built as sets, so an id
            # repeated within or across the two lists cannot inflate the count.
            source_ids = {suggestion.source_learning_object_id, *suggestion.source_extra_ids}
            candidate_ids = {suggestion.candidate_learning_object_id, *suggestion.candidate_extra_ids}
            sides = []
            for ids, primary in ((source_ids, source), (candidate_ids, candidate)):
                members = list(
                    LearningObject.objects.filter(
                        pk__in=ids,
                        material_id=primary.material_id,
                        material__outline_node=node,
                    )
                )
                if len(members) != len(ids):
                    raise SuggestionAcceptError(
                        "This suggestion is out of date. Refresh the page and review it again."
                    )
                sides.append(members)
            source_objects, candidate_objects = sides
            objects = [*source_objects, *candidate_objects]
            # Checked before anything moves: a teacher may have declined a pair
            # this unit would put into one concept.
            if rejected_across_sides(source_objects, candidate_objects, exclude_pk=suggestion.pk):
                raise SuggestionAcceptError("A teacher declined connecting these objects; review it first.")
            place_unit(objects, source.group or candidate.group or LearningObjectGroup.objects.create(
                outline_node=node,
                label=(
                    bundle_label(source_objects) or bundle_label(candidate_objects)
                )[:255],
            ))
            unpublished = unpublish_topic(node)
            source.refresh_from_db()
            candidate.refresh_from_db()
        suggestion.status = LearningObjectMatchSuggestion.Status.ACCEPTED
        suggestion.save(update_fields=["status", "updated_at"])
        target_group = source.group or LearningObjectGroup.objects.create(
            outline_node=node,
            # Named the way every other concept is named. A bundle of two
            # or more takes its heading; this one object takes its own title,
            # so objects sharing a section do not all become one name.
            label=(bundle_label([source]) or source.title)[:255],
        )
        old_group = candidate.group
        if old_group and old_group.id != target_group.id:
            release_from_group(
                candidate,
                list(old_group.learning_objects.exclude(pk=candidate.pk)),
            )
        if not target_group.learning_objects.filter(
            material_id=candidate.material_id,
        ).exclude(pk=candidate.pk).exists():
            # This PDF contributes nothing to the concept yet, so any role
            # stored against it is a ruling about text that has since left --
            # see `place_unit`, which guards the same way.
            prune_bundle_role(target_group, candidate.material_id)
        candidate.group = target_group
        candidate.mark_grouping_current()
        candidate.save(update_fields=["group", "grouping_content_hash"])
        source.group = target_group
        source.mark_grouping_current()
        source.save(update_fields=["group", "grouping_content_hash"])
        if old_group and old_group.id != target_group.id and not old_group.learning_objects.exists():
            old_group.delete()

        # The explicit teacher decision is already authoritative. Only update
        # handoff snapshots; rescoring every object and question made each click slow.
        self._refresh_relationship_snapshots(
            {source.material, candidate.material},
            recompute=False,
        )
        self._record_suggestion_decision(suggestion, source, candidate, accepted=True)
        return unpublished

    @action(
        detail=True,
        methods=["post"],
        url_path=r"outline-nodes/(?P<node_id>[^/.]+)/match-suggestions/(?P<suggestion_id>[^/.]+)/reject",
    )
    def reject_match_suggestion(self, request, pk=None, node_id=None, suggestion_id=None):
        course = self.get_object()
        try:
            node = course.nodes.get(pk=node_id)
        except OutlineNode.DoesNotExist:
            return Response(
                {"detail": "Outline node not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        suggestion = self._get_node_match_suggestion(course, node, suggestion_id)
        if suggestion is None:
            return Response(
                {"detail": "Match suggestion not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not all(
            learning_objects_are_confirmed(item.material)
            for item in (
                suggestion.source_learning_object,
                suggestion.candidate_learning_object,
            )
        ):
            return Response(
                {"detail": "Confirm both learning objects before reviewing this connection."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        self._record_suggestion_decision(
            suggestion,
            suggestion.source_learning_object,
            suggestion.candidate_learning_object,
            accepted=False,
        )
        return Response(self._learning_resources_payload(node, request))

    @action(
        detail=True,
        methods=["post"],
        url_path=r"outline-nodes/(?P<node_id>[^/.]+)/questions",
    )
    def create_topic_question(self, request, pk=None, node_id=None):
        """Create a teacher-authored question and immediately suggest a concept."""
        course = self.get_object()
        try:
            node = course.nodes.get(pk=node_id)
        except OutlineNode.DoesNotExist:
            return Response(
                {"detail": "Outline node not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        prompt = str(request.data.get("prompt") or "").strip()
        question_type = str(request.data.get("question_type") or "").strip()
        if not prompt:
            return Response(
                {"detail": "Enter the question text."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if question_type not in {
            Question.Type.TRUE_FALSE,
            Question.Type.MULTIPLE_CHOICE,
        }:
            return Response(
                {"detail": "Question type must be true_false or multiple_choice."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        raw_choices = request.data.get("choices") or []
        correct_answer = str(request.data.get("correct_answer") or "").strip()
        if question_type == Question.Type.TRUE_FALSE:
            choices = ["True", "False"]
            if correct_answer.casefold() not in {"true", "false"}:
                return Response(
                    {"detail": "Choose True or False as the correct answer."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            correct_answer = correct_answer.title()
        else:
            if not isinstance(raw_choices, list):
                return Response(
                    {"detail": "Multiple-choice options must be a list."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            choices = [str(choice).strip() for choice in raw_choices if str(choice).strip()]
            if len(choices) < 2 or len(choices) > 4:
                return Response(
                    {"detail": "Provide between two and four answer choices."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if correct_answer not in choices:
                return Response(
                    {"detail": "The correct answer must match one of the choices."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        values = enriched_question_values(
            prompt,
            question_type,
            choices,
            correct_answer,
        )
        duplicate = duplicate_for_topic(
            course.id,
            node.id,
            values["content_fingerprint"],
        )
        if duplicate is not None:
            return Response(
                {
                    "detail": "This question already exists in this topic.",
                    "duplicate_question_id": duplicate.id,
                },
                status=status.HTTP_409_CONFLICT,
            )

        raw_group_id = request.data.get("learning_object_group_id")
        target_group = None
        if raw_group_id not in (None, ""):
            try:
                target_group = LearningObjectGroup.objects.get(pk=int(raw_group_id), outline_node=node)
            except (TypeError, ValueError, LearningObjectGroup.DoesNotExist):
                return Response(
                    {"detail": "Selected concept was not found in this outline node."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        manual_material = LearningMaterial.objects.filter(
            course=course,
            outline_node=node,
            generated_json__document_source="manual_questions",
        ).first()
        if manual_material is None:
            module_node = node
            while module_node.parent_id:
                module_node = module_node.parent
            manual_material = LearningMaterial.objects.create(
                course=course,
                outline_node=node,
                module_node=module_node,
                title="Manually added questions",
                pdf_file="",
                status=LearningMaterial.Status.COMPLETED,
                generated_json={
                    "document_role": "assessment",
                    "document_source": "manual_questions",
                    "learning_objects": [],
                },
            )

        next_order = (manual_material.questions.aggregate(Max("order"))["order__max"] or -1) + 1
        question = Question.objects.create(
            material=manual_material,
            source_type=Question.SourceType.MANUAL,
            order=next_order,
            source_excerpt="Manually added by the teacher.",
            **values,
        )
        if target_group is not None:
            representative = (
                target_group.learning_objects.filter(
                    material__generated_json__learning_objects_confirmed=True,
                )
                .order_by("material_id", "order", "id")
                .first()
            )
            if representative is None:
                return Response(
                    {"detail": "The selected concept has no confirmed learning objects."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            QuestionLearningObjectLink.objects.create(
                question=question,
                learning_object=representative,
                relevance_score=0.0,
                method="teacher_selected",
                is_primary=True,
                review_status=QuestionLearningObjectLink.ReviewStatus.TEACHER_CONFIRMED,
                reviewed_at=timezone.now(),
            )
        refresh_question_learning_object_links(manual_material)
        question.refresh_from_db()
        sync_question_to_adaptive(question)
        generated_json = manual_material.generated_json or {}
        generated_json["questions"] = question_snapshots(manual_material)
        manual_material.generated_json = generated_json
        manual_material.save(update_fields=["generated_json"])

        return Response(
            {
                "question": QuestionSerializer(question, context={"request": request}).data,
                "resources": self._learning_resources_payload(node, request),
            },
            status=status.HTTP_201_CREATED,
        )

    @action(
        detail=True,
        methods=["patch", "delete"],
        url_path=r"outline-nodes/(?P<node_id>[^/.]+)/questions/(?P<question_id>[^/.]+)",
    )
    def delete_topic_question(self, request, pk=None, node_id=None, question_id=None):
        """Permanently drop a question and every concept pairing it had."""
        course = self.get_object()
        try:
            node = course.nodes.get(pk=node_id)
        except OutlineNode.DoesNotExist:
            return Response(
                {"detail": "Outline node not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            question = Question.objects.select_related("material").get(
                pk=question_id,
                material__course=course,
                material__outline_node=node,
            )
        except (Question.DoesNotExist, ValueError):
            return Response(
                {"detail": "Question was not found in this outline node."},
                status=status.HTTP_404_NOT_FOUND,
            )

        material = question.material
        if request.method == "PATCH":
            prompt = str(request.data.get("prompt", question.prompt) or "").strip()
            question_type = str(request.data.get("question_type", question.question_type) or "").strip()
            choices = request.data.get("choices", question.choices)
            correct_answer = str(request.data.get("correct_answer", question.correct_answer) or "").strip()
            if not prompt:
                return Response({"detail": "Enter the question text."}, status=status.HTTP_400_BAD_REQUEST)
            if question_type not in {Question.Type.TRUE_FALSE, Question.Type.MULTIPLE_CHOICE}:
                return Response(
                    {"detail": "Question type must be true_false or multiple_choice."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if question_type == Question.Type.TRUE_FALSE:
                choices = ["True", "False"]
                correct_answer = correct_answer.title()
            elif not isinstance(choices, list):
                return Response({"detail": "Multiple-choice options must be a list."}, status=status.HTTP_400_BAD_REQUEST)
            else:
                choices = [str(choice).strip() for choice in choices if str(choice).strip()]

            values = enriched_question_values(prompt, question_type, choices, correct_answer)
            if values["validation_issues"]:
                return Response(
                    {"detail": " ".join(values["validation_issues"]), "validation_issues": values["validation_issues"]},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            duplicate = duplicate_in_topic(material, values["content_fingerprint"], exclude_id=question.id)
            if duplicate is not None:
                return Response(
                    {"detail": "This question already exists in this topic.", "duplicate_question_id": duplicate.id},
                    status=status.HTTP_409_CONFLICT,
                )
            update_pairing = "learning_object_group_id" in request.data
            target_group = None
            representative = None
            if update_pairing:
                raw_group_id = request.data.get("learning_object_group_id")
                if raw_group_id not in (None, ""):
                    try:
                        target_group = LearningObjectGroup.objects.get(pk=int(raw_group_id), outline_node=node)
                    except (TypeError, ValueError, LearningObjectGroup.DoesNotExist):
                        return Response(
                            {"detail": "Selected concept was not found in this outline node."},
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                    representative = target_group.learning_objects.filter(
                        material__generated_json__learning_objects_confirmed=True,
                    ).order_by("material_id", "order", "id").first()
                    if representative is None:
                        return Response(
                            {"detail": "The selected concept has no confirmed learning objects."},
                            status=status.HTTP_400_BAD_REQUEST,
                        )
            for field, value in values.items():
                setattr(question, field, value)
            question.save(update_fields=list(values))

            if update_pairing:
                question.learning_object_links.all().delete()
                if target_group is not None:
                    QuestionLearningObjectLink.objects.create(
                        question=question,
                        learning_object=representative,
                        relevance_score=0.0,
                        method="teacher_selected",
                        is_primary=True,
                        review_status=QuestionLearningObjectLink.ReviewStatus.TEACHER_CONFIRMED,
                        reviewed_at=timezone.now(),
                    )
            sync_question_to_adaptive(question)
            generated_json = material.generated_json or {}
            generated_json["questions"] = question_snapshots(material)
            material.generated_json = generated_json
            material.save(update_fields=["generated_json"])
            return Response({
                "question": QuestionSerializer(question, context={"request": request}).data,
                "resources": self._learning_resources_payload(node, request),
            })

        adaptive_id = question.adaptive_question_id
        question.delete()
        if adaptive_id:
            from question_generation.models import GeneratedQuestion
            GeneratedQuestion.objects.filter(pk=adaptive_id).delete()

        generated_json = material.generated_json or {}
        generated_json["questions"] = question_snapshots(material)
        material.generated_json = generated_json
        material.save(update_fields=["generated_json"])
        self._refresh_relationship_snapshots({material}, recompute=False)

        return Response(self._learning_resources_payload(node, request))

    @action(
        detail=True,
        methods=["delete"],
        url_path=r"outline-nodes/(?P<node_id>[^/.]+)/learning-objects/(?P<object_id>[^/.]+)",
    )
    def delete_topic_learning_object(self, request, pk=None, node_id=None, object_id=None):
        """Drop one learning object during final review, keeping its PDF confirmed.

        The step-1 endpoint unconfirms the whole material on delete, which would
        empty the final-review payload. Final review keeps the confirmation so the
        remaining concepts stay publishable.
        """
        course = self.get_object()
        try:
            node = course.nodes.get(pk=node_id)
        except OutlineNode.DoesNotExist:
            return Response(
                {"detail": "Outline node not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            learning_object = LearningObject.objects.select_related("material", "group").get(
                pk=object_id,
                material__course=course,
                material__outline_node=node,
            )
        except (LearningObject.DoesNotExist, ValueError):
            return Response(
                {"detail": "Learning object was not found in this outline node."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not learning_objects_are_confirmed(learning_object.material):
            return Response(
                {"detail": "Confirm this learning object before reviewing connections."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        material = learning_object.material
        group = learning_object.group
        # Only this concept's generated questions go with it. Every other
        # concept's generated questions are left exactly as they are.
        delete_generated_questions_for(learning_object)
        learning_object.delete()
        if group is not None and not group.learning_objects.exists():
            group.delete()

        self._refresh_relationship_snapshots({material})

        return Response(self._learning_resources_payload(node, request))

    @action(
        detail=True,
        methods=["post"],
        url_path=r"outline-nodes/(?P<node_id>[^/.]+)/publish",
    )
    def publish_topic(self, request, pk=None, node_id=None):
        """Start a publish run for one topic and return its id.

        Publishing runs image narration, version settling and audio synthesis
        across every confirmed material, each of which calls a local model.
        Doing that inside the request would hold the connection open for
        minutes with nothing to show, so the work moves to a background thread
        and the teacher follows it through the run's event stream -- the same
        mechanism question generation already uses.
        """
        course = self.get_object()
        try:
            node = course.nodes.get(pk=node_id)
        except OutlineNode.DoesNotExist:
            return Response(
                {"detail": "Outline node not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not confirmed_materials_for(course, node).exists():
            return Response(
                {"detail": "Confirm at least one lesson file's learning objects before publishing."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        active = GenerationRun.objects.filter(
            outline_node=node, status="running"
        ).order_by("-id").first()
        if active is not None:
            return Response(
                {"detail": "This topic is already publishing.", "run_id": active.id},
                status=status.HTTP_409_CONFLICT,
            )

        run = GenerationRun.objects.create(
            outline_node=node, kind=GenerationRun.Kind.PUBLISH,
        )
        threading.Thread(
            target=_run_topic_publish_in_background,
            # The confirmation helper lives on the viewset and does not touch
            # the request, so the bound method travels to the thread rather
            # than being extracted into a module function for one caller.
            args=(run.id, course.id, node.id, self._set_learning_objects_confirmed),
            daemon=True,
        ).start()
        return Response(
            {"run_id": run.id, "node_id": node.id},
            status=status.HTTP_202_ACCEPTED,
        )

    @action(
        detail=True,
        methods=["post"],
        url_path=r"outline-nodes/(?P<node_id>[^/.]+)/learning-objects/(?P<object_id>[^/.]+)/generate-versions",
    )
    def generate_object_versions(self, request, pk=None, node_id=None, object_id=None):
        """Fill one teaching step's missing version slots so a teacher can read them.

        Deliberately one object at a time: a single Gemma call takes minutes, so
        a bulk request would block for hours with nothing to show. Failures are
        reported in the payload rather than raised -- an unreachable model must
        not look like a broken endpoint.
        """
        course = self.get_object()
        try:
            node = course.nodes.get(pk=node_id)
        except OutlineNode.DoesNotExist:
            return Response({"detail": "Outline node not found."}, status=status.HTTP_404_NOT_FOUND)

        try:
            learning_object = LearningObject.objects.select_related("material").get(
                pk=object_id,
                material__outline_node=node,
            )
        except (LearningObject.DoesNotExist, TypeError, ValueError):
            return Response(
                {"detail": "Learning object not found in this topic."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if learning_object.represented_by_id is not None:
            return Response(
                {"detail": "This object is taught through another one; generate versions there."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        requested_slot = str(request.data.get("slot") or "").upper()
        if requested_slot and requested_slot not in ("SIMPLIFIED", "ELABORATED"):
            return Response(
                {"detail": "Choose Simplified or Elaborated."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if learning_object.group_id:
            version_state = assign_group_versions(learning_object.group)
            if not version_state.get("classification_complete"):
                return Response(
                    {"detail": "Classify the existing PDF variants before generating a missing version."},
                    status=status.HTTP_409_CONFLICT,
                )
            learning_object = LearningObject.objects.get(
                pk=version_state["representative_id"],
            )
        result = fill_missing_slots(
            learning_object,
            target_slots=[requested_slot] if requested_slot else None,
            # The teacher's "Regenerate" on an out-of-date version.
            replace_stale=bool(request.data.get("replace_stale")),
        )
        payload = self._learning_resources_payload(node, request)
        payload["version_generation"] = {
            "learning_object_id": learning_object.id,
            "generated": result["generated"],
            "skipped": result.get("skipped", []),
            "errors": result["errors"],
        }
        return Response(payload)

    @action(
        detail=True,
        methods=["post"],
        url_path=r"outline-nodes/(?P<node_id>[^/.]+)/generate-all-versions",
    )
    def generate_all_versions(self, request, pk=None, node_id=None):
        """Start background classification of all existing PDF variants."""
        course = self.get_object()
        try:
            node = course.nodes.get(pk=node_id)
        except OutlineNode.DoesNotExist:
            return Response(
                {"detail": "Outline node not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        active = _active_topic_run(node)
        if active is not None:
            return Response(
                {"detail": "Another topic task is already running.", "run_id": active.id},
                status=status.HTTP_409_CONFLICT,
            )

        run = GenerationRun.objects.create(
            outline_node=node, kind=GenerationRun.Kind.VERSIONS,
        )
        threading.Thread(
            target=_run_all_versions_in_background,
            args=(run.id, node.id),
            daemon=True,
        ).start()
        return Response(
            {"run_id": run.id, "node_id": node.id},
            status=status.HTTP_202_ACCEPTED,
        )

    @action(
        detail=True,
        methods=["post"],
        url_path=r"outline-nodes/(?P<node_id>[^/.]+)/versions/(?P<variant_id>[^/.]+)",
    )
    def edit_version_text(self, request, pk=None, node_id=None, variant_id=None):
        """Reword one version. Where the text came from is preserved."""
        course = self.get_object()
        try:
            node = course.nodes.get(pk=node_id)
        except OutlineNode.DoesNotExist:
            return Response({"detail": "Outline node not found."}, status=status.HTTP_404_NOT_FOUND)

        try:
            variant = LessonVariant.objects.select_related("learning_object__material").get(
                pk=variant_id,
                learning_object__material__outline_node=node,
            )
        except (LessonVariant.DoesNotExist, TypeError, ValueError):
            return Response(
                {"detail": "Version not found in this topic."},
                status=status.HTTP_404_NOT_FOUND,
            )

        narration = str(request.data.get("narration") or "").strip()
        if not narration:
            return Response(
                {"detail": "Version text cannot be empty."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        variant.narration = narration
        variant.audio_url = ""
        from course.variant_generator import _fingerprint
        variant.source_fingerprint = _fingerprint(variant.learning_object)
        # origin records where the wording came from and stays put; assigned_by
        # is what records that a teacher has since had a hand in it.
        variant.assigned_by = LessonVariant.AssignedBy.TEACHER
        variant.save(update_fields=["narration", "assigned_by", "audio_url", "source_fingerprint"])

        return Response(self._learning_resources_payload(node, request))

    @action(
        detail=True,
        methods=["post"],
        url_path=r"outline-nodes/(?P<node_id>[^/.]+)/versions/(?P<variant_id>[^/.]+)/keep",
    )
    def keep_version_text(self, request, pk=None, node_id=None, variant_id=None):
        """Confirm an out-of-date version still matches the current Normal text.

        The wording is left exactly as it is; only the record of which text it
        was checked against moves forward, which is what clears the publish
        block.
        """
        course = self.get_object()
        try:
            node = course.nodes.get(pk=node_id)
        except OutlineNode.DoesNotExist:
            return Response({"detail": "Outline node not found."}, status=status.HTTP_404_NOT_FOUND)

        try:
            variant = LessonVariant.objects.select_related("learning_object").get(
                pk=variant_id,
                learning_object__material__outline_node=node,
            )
        except (LessonVariant.DoesNotExist, TypeError, ValueError):
            return Response({"detail": "Version not found in this topic."}, status=status.HTTP_404_NOT_FOUND)

        variant.source_fingerprint = version_fingerprint(variant.learning_object)
        variant.assigned_by = LessonVariant.AssignedBy.TEACHER
        variant.save(update_fields=["source_fingerprint", "assigned_by"])
        return Response(self._learning_resources_payload(node, request))

    @action(
        detail=True,
        methods=["post"],
        url_path=r"outline-nodes/(?P<node_id>[^/.]+)/version-assignment",
    )
    def confirm_version_assignment(self, request, pk=None, node_id=None):
        """Record a teacher's ruling on which version slot a grouped text fills."""
        course = self.get_object()
        try:
            node = course.nodes.get(pk=node_id)
        except OutlineNode.DoesNotExist:
            return Response({"detail": "Outline node not found."}, status=status.HTTP_404_NOT_FOUND)

        slot = str(request.data.get("slot", "")).upper()
        if slot not in ("NORMAL", "SIMPLIFIED", "ELABORATED", "EXTRA"):
            return Response(
                {"detail": "slot must be NORMAL, SIMPLIFIED, ELABORATED or EXTRA."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            learning_object = LearningObject.objects.select_related("group", "material").get(
                pk=request.data.get("learning_object_id"),
                material__outline_node=node,
            )
        except (LearningObject.DoesNotExist, TypeError, ValueError):
            return Response(
                {"detail": "Learning object not found in this topic."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if learning_object.group_id is None:
            return Response(
                {"detail": "Only grouped learning objects have version slots."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        state = assign_group_versions(learning_object.group)
        representative_id = state["representative_id"]
        if representative_id is None:
            return Response(
                {"detail": "This concept has no Normal version."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if slot == "NORMAL":
            try:
                assign_source_as_representative(learning_object.group, learning_object)
            except ValueError as exc:
                return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
            payload = self._learning_resources_payload(node, request)
            payload["version_assignment"] = {
                "slot": slot,
                "source_learning_object_id": learning_object.id,
                "moved_to_extra": None,
            }
            return Response(payload)

        if representative_id == learning_object.id:
            return Response(
                {"detail": "Choose another PDF source as Normal before moving this one."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        representative = LearningObject.objects.get(pk=representative_id)
        try:
            moved = assign_source_to_slot(representative, learning_object, slot)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        displaced_id = moved["displaced_learning_object_id"]
        payload = self._learning_resources_payload(node, request)
        payload["version_assignment"] = {
            "slot": slot,
            "source_learning_object_id": learning_object.id,
            "moved_to_extra": displaced_id if displaced_id != learning_object.id else None,
        }
        return Response(payload)

    @action(
        detail=True,
        methods=["post"],
        url_path=r"outline-nodes/(?P<node_id>[^/.]+)/questions/(?P<question_id>[^/.]+)/pairing",
    )
    def review_question_pairing(self, request, pk=None, node_id=None, question_id=None):
        """Confirm, redirect, or deliberately leave a suggested question pair unpaired."""
        course = self.get_object()
        try:
            node = course.nodes.get(pk=node_id)
        except OutlineNode.DoesNotExist:
            return Response(
                {"detail": "Outline node not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            question = Question.objects.select_related("material").get(
                pk=question_id,
                material__course=course,
                material__outline_node=node,
            )
        except Question.DoesNotExist:
            return Response(
                {"detail": "Question not found in this outline node."},
                status=status.HTTP_404_NOT_FOUND,
            )

        decision = str(request.data.get("decision") or "").strip().casefold()
        if decision not in {"confirm", "change", "unpair"}:
            return Response(
                {"detail": "decision must be confirm, change, or unpair."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        existing = question.learning_object_links.filter(
            learning_object__material__generated_json__learning_objects_confirmed=True,
        ).select_related(
            "learning_object__group"
        ).order_by("-is_primary", "-relevance_score", "id").first()
        now = timezone.now()

        if decision == "change":
            try:
                group_id = int(request.data.get("learning_object_group_id"))
            except (TypeError, ValueError):
                return Response(
                    {"detail": "Select a valid learning-object group."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            try:
                group = LearningObjectGroup.objects.get(pk=group_id, outline_node=node)
            except LearningObjectGroup.DoesNotExist:
                return Response(
                    {"detail": "Learning-object group not found in this outline node."},
                    status=status.HTTP_404_NOT_FOUND,
                )
            confirmed_group_objects = group.learning_objects.filter(
                material__generated_json__learning_objects_confirmed=True,
            )
            representative = (
                confirmed_group_objects.filter(material=question.material).order_by("order", "id").first()
                or confirmed_group_objects.order_by("material_id", "order", "id").first()
            )
            if representative is None:
                return Response(
                    {"detail": "The selected concept group has no learning objects."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            question.learning_object_links.all().delete()
            QuestionLearningObjectLink.objects.create(
                question=question,
                learning_object=representative,
                relevance_score=0.0,
                method="teacher_selected",
                is_primary=True,
                review_status=QuestionLearningObjectLink.ReviewStatus.TEACHER_CONFIRMED,
                reviewed_at=now,
            )
        elif existing is None:
            return Response(
                {"detail": "No suggested question pairing is available to review."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        elif decision == "confirm":
            question.learning_object_links.exclude(pk=existing.pk).delete()
            existing.is_primary = True
            existing.review_status = QuestionLearningObjectLink.ReviewStatus.TEACHER_CONFIRMED
            existing.reviewed_at = now
            existing.save(update_fields=["is_primary", "review_status", "reviewed_at"])
        else:
            question.learning_object_links.exclude(pk=existing.pk).delete()
            existing.is_primary = False
            existing.review_status = QuestionLearningObjectLink.ReviewStatus.TEACHER_UNPAIRED
            existing.reviewed_at = now
            existing.save(update_fields=["is_primary", "review_status", "reviewed_at"])

        question.refresh_from_db()
        sync_question_to_adaptive(question)
        # Preserve the explicit teacher decision without rescoring every question.
        self._refresh_relationship_snapshots({question.material}, recompute=False)
        return Response(self._learning_resources_payload(node, request))

    @action(
        detail=True,
        methods=["get"],
        url_path=r"modules/(?P<module_id>[^/.]+)/materials",
    )
    def module_materials(self, request, pk=None, module_id=None):
        course = self.get_object()
        module = self._get_module_node(course, module_id)
        if module is None:
            return Response(
                {"detail": "Top-level module was not found for this course."},
                status=status.HTTP_404_NOT_FOUND,
            )

        materials = (
            course.materials.filter(module_node=module)
            .prefetch_related(
                "learning_objects",
                "questions__learning_object_links__learning_object",
            )
            .order_by("-created_at")
        )
        return Response(LearningMaterialSerializer(materials, many=True, context={"request": request}).data)

    def _serialize_course_detail(self, course, request):
        course = self.get_queryset().get(pk=course.pk)
        serializer = CourseDetailSerializer(course, context={"request": request})
        return Response(serializer.data)

    @action(detail=True, methods=["post"], url_path="outline-nodes")
    def create_outline_node(self, request, pk=None):
        course = self.get_object()
        serializer = OutlineNodeMutationSerializer(
            data=request.data,
            context={"course": course},
        )
        serializer.is_valid(raise_exception=True)

        parent = serializer.validated_data.get("parent")
        max_order = OutlineNode.objects.filter(course=course, parent=parent).aggregate(Max("order"))["order__max"]
        title = serializer.validated_data.get("title")
        if not title:
            return Response(
                {"detail": "Title is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        OutlineNode.objects.create(
            course=course,
            parent=parent,
            title=title,
            order=serializer.validated_data.get("order", (max_order or 0) + 1 if max_order is not None else 0),
            depth=parent.depth + 1 if parent else 0,
        )

        return self._serialize_course_detail(course, request)

    @action(
        detail=True,
        methods=["patch", "delete"],
        url_path=r"outline-nodes/(?P<node_id>[^/.]+)",
    )
    def outline_node_detail(self, request, pk=None, node_id=None):
        course = self.get_object()
        try:
            node = course.nodes.get(pk=node_id)
        except OutlineNode.DoesNotExist:
            return Response(
                {"detail": "Outline node not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if request.method == "DELETE":
            node.delete()
            return self._serialize_course_detail(course, request)

        serializer = OutlineNodeMutationSerializer(
            data=request.data,
            partial=True,
            context={"course": course},
        )
        serializer.is_valid(raise_exception=True)

        if "title" in serializer.validated_data:
            node.title = serializer.validated_data["title"]
        if "parent" in serializer.validated_data:
            parent = serializer.validated_data["parent"]
            if parent and parent.id == node.id:
                return Response(
                    {"detail": "A node cannot be its own parent."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            node.parent = parent
            node.depth = parent.depth + 1 if parent else 0
        if "order" in serializer.validated_data:
            node.order = serializer.validated_data["order"]

        node.save()
        return self._serialize_course_detail(course, request)

    @action(detail=True, methods=["post"], url_path="upload-outline")
    def upload_outline(self, request, pk=None):
        course = self.get_object()
        input_serializer = CourseOutlineUploadInputSerializer(data=request.data)
        if not input_serializer.is_valid():
            return Response(
                {"detail": str(input_serializer.errors["detail"][0])},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            _course, reused = upload_course_outline(
                course=course,
                **input_serializer.validated_data,
            )
        except PdfProcessingUseCaseError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        response = self._serialize_course_detail(course, request)
        response.data["upload_type"] = "outline"
        response.data["upload_reused"] = reused
        response.status_code = status.HTTP_200_OK if reused else status.HTTP_201_CREATED
        return response

    @action(detail=True, methods=["post"], url_path="upload-pdf")
    def upload_pdf(self, request, pk=None):
        """Accept one PDF and automatically route outlines and lesson materials."""
        course = self.get_object()
        input_serializer = CoursePdfUploadInputSerializer(data=request.data)
        if not input_serializer.is_valid():
            detail = input_serializer.errors.get("detail")
            if detail:
                return Response({"detail": str(detail[0])}, status=status.HTTP_400_BAD_REQUEST)
            return Response(input_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            upload_type, material, reused = upload_course_pdf(
                course=course,
                **input_serializer.validated_data,
            )
        except PdfProcessingUseCaseError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        response = self._serialize_course_detail(course, request)
        response.data["upload_type"] = upload_type
        response.data["upload_reused"] = reused
        if material is not None:
            response.data["uploaded_material_id"] = material.id
        response.status_code = status.HTTP_200_OK if reused else status.HTTP_201_CREATED
        return response

    @action(detail=True, methods=["delete"], url_path="delete-outline")
    def delete_outline(self, request, pk=None):
        course = self.get_object()
        for outline in course.outlines.all():
            if outline.outline_file:
                outline.outline_file.delete(save=False)
            outline.delete()

        course.nodes.all().delete()

        course = self.get_queryset().get(pk=course.pk)
        serializer = CourseDetailSerializer(course, context={"request": request})
        return Response(serializer.data)

    @action(detail=True, methods=["post"], url_path="confirm-outline")
    def confirm_outline(self, request, pk=None):
        course = self.get_object()
        try:
            confirm_course_outline(course=course)
        except PdfProcessingUseCaseError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return self._serialize_course_detail(course, request)

    @action(detail=True, methods=["post"], url_path="upload-material")
    def upload_material(self, request, pk=None):
        course = self.get_object()
        input_serializer = LearningMaterialUploadInputSerializer(data=request.data)
        if not input_serializer.is_valid():
            detail = input_serializer.errors.get("detail")
            if detail:
                return Response({"detail": str(detail[0])}, status=status.HTTP_400_BAD_REQUEST)
            return Response(input_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        try:
            material, reused = upload_learning_material(
                course=course, **input_serializer.validated_data
            )
        except PdfProcessingUseCaseError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        response = self._serialize_course_detail(course, request)
        response.data["uploaded_material_id"] = material.id
        response.data["upload_reused"] = reused
        response.status_code = status.HTTP_200_OK if reused else status.HTTP_201_CREATED
        return response

    def _get_course_material(self, course, material_id):
        try:
            return course.materials.get(pk=material_id)
        except LearningMaterial.DoesNotExist:
            return None

    @action(
        detail=True,
        methods=["delete"],
        url_path=r"materials/(?P<material_id>[^/.]+)",
    )
    def material_detail(self, request, pk=None, material_id=None):
        course = self.get_object()
        material = self._get_course_material(course, material_id)
        if material is None:
            return Response(
                {"detail": "Learning material not found for this course."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if material.pdf_file:
            material.pdf_file.delete(save=False)
        material.delete()

        course = self.get_queryset().get(pk=course.pk)
        serializer = CourseDetailSerializer(course, context={"request": request})
        return Response(serializer.data)

    def _learning_objects_snapshot(self, material):
        return [
            {
                "order": index,
                "type": "image_description" if learning_object.kind == LearningObject.Kind.IMAGE else "lesson_content",
                "kind": learning_object.kind,
                "learning_object_group_id": learning_object.group_id,
                "section_title": learning_object.section_title,
                "title": learning_object.title,
                "content": learning_object.content,
                "image_url": learning_object.image_url,
                "source_page": learning_object.source_page,
                "source_block_id": learning_object.source_block_id,
                "source_excerpt": learning_object.source_excerpt,
                "source": "teacher_reviewed",
                "learning_object_id": learning_object.id,
            }
            for index, learning_object in enumerate(
                item
                for item in material.learning_objects.all().order_by("order", "id")
                if not is_structural_metadata_label(item.title)
            )
        ]

    def _set_learning_objects_confirmed(self, material, confirmed):
        stale_ids = [
            item.id
            for item in material.learning_objects.all()
            if is_structural_metadata_label(item.title)
        ]
        if stale_ids:
            material.learning_objects.filter(id__in=stale_ids).delete()

        # Publish the confirmation state before relationship refresh so the
        # linker can never use draft learning objects as candidates.
        generated_json = material.generated_json or {}
        generated_json["learning_objects_confirmed"] = confirmed
        generated_json["learning_objects_confirmed_at"] = timezone.now().isoformat() if confirmed else None
        material.generated_json = generated_json
        material.save(update_fields=["generated_json"])

        refresh_material_learning_relationships(material)
        if material.outline_node_id:
            related_question_materials = (
                LearningMaterial.objects.filter(
                    outline_node_id=material.outline_node_id,
                    questions__isnull=False,
                )
                .exclude(pk=material.pk)
                .distinct()
            )
            for question_material in related_question_materials:
                refresh_material_learning_relationships(question_material)
                question_json = question_material.generated_json or {}
                question_json["questions"] = question_snapshots(question_material)
                question_material.generated_json = question_json
                question_material.save(update_fields=["generated_json"])

        generated_json = material.generated_json or {}
        learning_objects = self._learning_objects_snapshot(material)
        narration_script = build_narration_script_from_learning_objects(learning_objects)
        generated_json["learning_objects"] = learning_objects
        generated_json["narration_script"] = narration_script
        generated_json["lesson_playlist"] = build_lesson_playlist(narration_script)
        generated_json["questions"] = question_snapshots(material)
        generated_json["audio_playlist_generated"] = False
        generated_json["lesson_audio_generated"] = False
        material.generated_json = generated_json
        material.save(update_fields=["generated_json"])

    @action(
        detail=True,
        methods=["post"],
        url_path=r"materials/(?P<material_id>[^/.]+)/regenerate-outputs",
    )
    def regenerate_material_outputs(self, request, pk=None, material_id=None):
        course = self.get_object()
        material = self._get_course_material(course, material_id)
        if material is None:
            return Response(
                {"detail": "Learning material not found for this course."},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            regenerate_learning_material(material=material)
        except PdfProcessingUseCaseError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return self._serialize_course_detail(course, request)

    @action(
        detail=True,
        methods=["post"],
        url_path=r"materials/(?P<material_id>[^/.]+)/learning-objects",
    )
    def create_learning_object(self, request, pk=None, material_id=None):
        course = self.get_object()
        material = self._get_course_material(course, material_id)
        if material is None:
            return Response(
                {"detail": "Learning material not found for this course."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = LearningObjectMutationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not serializer.validated_data.get("title") or not serializer.validated_data.get("content"):
            return Response(
                {"detail": "Title and content are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        max_order = material.learning_objects.aggregate(Max("order"))["order__max"]
        LearningObject.objects.create(
            material=material,
            kind=LearningObject.Kind.TEXT,
            section_title=serializer.validated_data.get("section_title", ""),
            title=serializer.validated_data["title"],
            content=serializer.validated_data["content"],
            order=serializer.validated_data.get("order", (max_order or 0) + 1 if max_order is not None else 0),
        )
        self._set_learning_objects_confirmed(material, False)

        return self._serialize_course_detail(course, request)

    @action(
        detail=True,
        methods=["post"],
        url_path=r"materials/(?P<material_id>[^/.]+)/reorder-learning-objects",
    )
    def reorder_learning_objects(self, request, pk=None, material_id=None):
        course = self.get_object()
        material = self._get_course_material(course, material_id)
        if material is None:
            return Response(
                {"detail": "Learning material not found for this course."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = LearningObjectReorderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        requested_ids = serializer.validated_data["object_ids"]

        with transaction.atomic():
            learning_objects = list(
                material.learning_objects.select_for_update().order_by("order", "id")
            )
            existing_ids = {item.id for item in learning_objects}
            if len(requested_ids) != len(learning_objects) or set(requested_ids) != existing_ids:
                return Response(
                    {"detail": "Provide every learning object from this material exactly once."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            by_id = {item.id: item for item in learning_objects}
            reordered = [by_id[object_id] for object_id in requested_ids]
            for index, learning_object in enumerate(reordered):
                learning_object.order = index
            LearningObject.objects.bulk_update(reordered, ["order"])
            self._set_learning_objects_confirmed(material, False)

        return self._serialize_course_detail(course, request)

    @action(
        detail=True,
        methods=["post"],
        url_path=r"materials/(?P<material_id>[^/.]+)/confirm-learning-objects",
    )
    def confirm_learning_objects(self, request, pk=None, material_id=None):
        course = self.get_object()
        material = self._get_course_material(course, material_id)
        if material is None:
            return Response(
                {"detail": "Learning material not found for this course."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not material.learning_objects.exists():
            return Response(
                {"detail": "Add at least one learning object before confirming."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        image_result = populate_missing_image_descriptions(material)
        self._set_learning_objects_confirmed(material, True)
        response = self._serialize_course_detail(course, request)
        response.data["image_description_generation"] = image_result
        return response

    @action(
        detail=True,
        methods=["post"],
        url_path=r"materials/(?P<material_id>[^/.]+)/regenerate-image-narrations",
    )
    def regenerate_image_narrations(self, request, pk=None, material_id=None):
        """Repair blank narration on saved image objects without re-uploading the PDF."""
        course = self.get_object()
        material = self._get_course_material(course, material_id)
        if material is None:
            return Response(
                {"detail": "Learning material not found for this course."},
                status=status.HTTP_404_NOT_FOUND,
            )

        image_result = populate_missing_image_descriptions(material)
        if image_result["generated_count"]:
            # Refresh narration and playlist snapshots while preserving whether
            # this material was already confirmed.
            confirmed = bool(
                (material.generated_json or {}).get("learning_objects_confirmed")
            )
            self._set_learning_objects_confirmed(material, confirmed)

        response = self._serialize_course_detail(course, request)
        response.data["image_description_generation"] = image_result
        return response

    @action(
        detail=True,
        methods=["post"],
        url_path=r"materials/(?P<material_id>[^/.]+)/generate-audio-playlist",
    )
    def generate_audio_playlist(self, request, pk=None, material_id=None):
        course = self.get_object()
        material = self._get_course_material(course, material_id)
        if material is None:
            return Response(
                {"detail": "Learning material not found for this course."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not material.learning_objects.exists():
            return Response(
                {"detail": "Add at least one learning object before generating audio."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        scope = request.data.get("scope", "all")
        try:
            result = generate_material_audio_playlist(material, scope=scope)
        except AudioGenerationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        course = self.get_queryset().get(pk=course.pk)
        serializer = CourseDetailSerializer(course, context={"request": request})
        return Response(
            {
                "message": "Audio playlist generated successfully.",
                "generated_count": result["generated_count"],
                "scope": result.get("scope", scope),
                "course": serializer.data,
            }
        )

    @action(
        detail=True,
        methods=["patch"],
        url_path=r"materials/(?P<material_id>[^/.]+)/classified-blocks/(?P<block_id>[^/.]+)",
    )
    def classified_block_detail(self, request, pk=None, material_id=None, block_id=None):
        course = self.get_object()
        material = self._get_course_material(course, material_id)
        if material is None:
            return Response(
                {"detail": "Learning material not found for this course."},
                status=status.HTTP_404_NOT_FOUND,
            )

        category = request.data.get("category")
        if category and category not in CLASSIFICATION_CATEGORIES:
            return Response({"detail": "Unsupported classification category."}, status=status.HTTP_400_BAD_REQUEST)

        include_in_narration = request.data.get("include_in_narration", None)
        if include_in_narration is not None and not isinstance(include_in_narration, bool):
            return Response({"detail": "include_in_narration must be true or false."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            apply_classification_override(
                material,
                block_id=int(block_id),
                category=category,
                include_in_narration=include_in_narration,
            )
        except (TypeError, ValueError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return self._serialize_course_detail(course, request)

    @action(
        detail=True,
        methods=["patch", "delete"],
        url_path=r"materials/(?P<material_id>[^/.]+)/learning-objects/(?P<object_id>[^/.]+)",
    )
    def learning_object_detail(self, request, pk=None, material_id=None, object_id=None):
        course = self.get_object()
        material = self._get_course_material(course, material_id)
        if material is None:
            return Response(
                {"detail": "Learning material not found for this course."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            learning_object = material.learning_objects.get(pk=object_id)
        except LearningObject.DoesNotExist:
            return Response(
                {"detail": "Learning object not found for this material."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if request.method == "DELETE":
            group = learning_object.group
            # Only this object's generated questions go with it.
            delete_generated_questions_for(learning_object)
            learning_object.delete()
            # A group whose only member was deleted is not a concept any more.
            if group is not None and not group.learning_objects.exists():
                group.delete()
            self._set_learning_objects_confirmed(material, False)
            return self._serialize_course_detail(course, request)

        serializer = LearningObjectMutationSerializer(
            learning_object,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        self._set_learning_objects_confirmed(material, False)
        return self._serialize_course_detail(course, request)

