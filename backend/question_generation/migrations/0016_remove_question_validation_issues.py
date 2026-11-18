from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("question_generation", "0015_move_teacher_questions_to_bank"),
    ]

    operations = [
        migrations.RemoveField(model_name="question", name="validation_issues"),
    ]
