"""Remove stored files when their rows are deleted, including by cascade.

Deleting a course or topic cascades to its materials without calling their
views, so file removal has to hang off the row itself. It runs after commit:
a rolled-back delete must keep its file.
"""

from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import CourseOutline, LearningMaterial


def _delete_after_commit(field_file):
    if not field_file or not field_file.name:
        return
    storage, name = field_file.storage, field_file.name
    transaction.on_commit(lambda: storage.exists(name) and storage.delete(name))


@receiver(post_delete, sender=LearningMaterial)
def delete_material_file(sender, instance, **kwargs):
    _delete_after_commit(instance.pdf_file)


@receiver(post_delete, sender=CourseOutline)
def delete_outline_file(sender, instance, **kwargs):
    _delete_after_commit(instance.outline_file)
