"""A concept's name and the text its questions are written from and checked against."""


def concept_name(node):
    """The concept's name, for trace lines."""
    group = getattr(node, "group", None)
    return (group.label if group is not None and group.label else node.title) or "Untitled concept"


def _object_text(item):
    """An object's text, with a definition the extractor split from its term joined back to the term."""
    content = (item.content or "").strip()
    title = (item.title or "").strip()
    if title and content[:1].islower():
        return f"{title} {content}"
    return content


def concept_source_text(node):
    """Every telling of the concept, Standard first: the text its questions are written from."""
    from course.version_assignment import bundle_roles, standard_material_id
    from lessons.services.concept_bundles import bundles_for_group, material_order

    if node.group_id is None:
        return _object_text(node)

    group = node.group
    bundles = {
        material_id: kept
        for material_id, objects in bundles_for_group(group).items()
        if (kept := [item for item in objects if (item.content or "").strip()])
    }
    standard_id = standard_material_id(group)
    if not any(item.id == node.id for item in bundles.get(standard_id) or []):
        return _object_text(node)

    roles = bundle_roles(group)
    rank = {
        material_id: index
        for index, material_id in enumerate(material_order(group.outline_node))
    }
    others = sorted(
        (material_id for material_id in bundles if material_id != standard_id),
        key=lambda material_id: (rank.get(material_id, len(rank)), material_id),
    )

    objects = []
    for material_id in [standard_id, *others]:
        if material_id != standard_id and material_id not in roles:
            continue
        objects.extend(bundles.get(material_id) or [])
    return "\n".join(text for item in objects if (text := _object_text(item))) or _object_text(node)


def is_concept_source(node):
    """Whether node is the one object of its concept that generates questions."""
    if node.group_id is None:
        return True
    from course.version_assignment import version_bundles
    from lessons.services.concept_bundles import bundle_lead

    standard = version_bundles(node.group).get("STANDARD") or []
    if not standard:
        return True
    if not any(item.id == node.id for item in standard):
        return False
    lead = bundle_lead(standard)
    return lead is not None and lead.id == node.id
