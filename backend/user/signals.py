from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import AdminProfile, StudentProfile, TeacherProfile, User

PROFILE_MODEL_BY_ROLE = {
    User.Role.STUDENT: StudentProfile,
    User.Role.TEACHER: TeacherProfile,
    User.Role.ADMIN: AdminProfile,
}


@receiver(post_save, sender=User)
def create_role_profile(sender, instance, created, **kwargs):
    if not created:
        return
    profile_model = PROFILE_MODEL_BY_ROLE.get(instance.role)
    if profile_model is not None:
        profile_model.objects.get_or_create(user=instance)
