from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("lessons", "0012_metadata_ids")]

    operations = [
        migrations.AddField(
            model_name="courseoutline",
            name="file_sha256",
            field=models.CharField(blank=True, db_index=True, max_length=64),
        ),
        migrations.AddConstraint(
            model_name="courseoutline",
            constraint=models.UniqueConstraint(
                condition=~models.Q(file_sha256=""),
                fields=("course", "file_sha256"),
                name="unique_outline_file_per_course",
            ),
        ),
    ]
