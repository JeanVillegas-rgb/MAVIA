from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("course", "0008_remove_extra_version_role")]

    operations = [
        migrations.AddConstraint(
            model_name="lessonvariant",
            constraint=models.CheckConstraint(
                condition=models.Q(variant__in=("SIMPLIFIED", "ELABORATED")),
                name="lessonvariant_active_variant",
            ),
        ),
    ]
