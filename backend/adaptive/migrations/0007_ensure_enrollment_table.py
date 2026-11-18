from django.db import migrations


def ensure_enrollment_table(apps, schema_editor):
    enrollment = apps.get_model("adaptive", "Enrollment")
    connection = schema_editor.connection
    existing_tables = set(connection.introspection.table_names())
    enrollment_table = enrollment._meta.db_table

    if enrollment_table not in existing_tables:
        schema_editor.create_model(enrollment)

    legacy_table = "adaptive_portal_enrollment"
    if legacy_table not in existing_tables:
        return

    quote = schema_editor.quote_name
    columns = "id, created_at, course_id, created_by_id, student_id"
    schema_editor.execute(
        f"INSERT OR IGNORE INTO {quote(enrollment_table)} ({columns}) "
        f"SELECT {columns} FROM {quote(legacy_table)}"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("adaptive", "0006_decision_log_and_concept_mastery"),
    ]

    operations = [
        migrations.RunPython(ensure_enrollment_table, migrations.RunPython.noop),
    ]
