from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("lessons", "0004_delete_question")]

    operations = [
        migrations.AddField(
            model_name="learningmaterial",
            name="file_sha256",
            field=models.CharField(blank=True, db_index=True, max_length=64),
        ),
        migrations.AddConstraint(
            model_name="learningmaterial",
            constraint=models.UniqueConstraint(
                condition=~models.Q(file_sha256=""),
                fields=("course", "file_sha256"),
                name="unique_material_file_per_course",
            ),
        ),
    ]
