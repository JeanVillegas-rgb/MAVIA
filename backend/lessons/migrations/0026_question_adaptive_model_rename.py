import django.db.models
import django.db.models.deletion
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("lessons", "0025_remove_learningobject_kept_apart_from_section"),
        ("question_generation", "0010_question_bank_schema"),
    ]

    operations = [
        migrations.AlterField(
            model_name="question",
            name="adaptive_question",
            field=django.db.models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="teacher_question",
                to="question_generation.question",
            ),
        ),
    ]
