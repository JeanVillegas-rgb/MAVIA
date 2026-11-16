from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("lessons", "0026_question_adaptive_model_rename"),
        ("question_generation", "0015_move_teacher_questions_to_bank"),
    ]

    operations = [
        migrations.RemoveField(model_name="question", name="learning_objects"),
        migrations.DeleteModel(name="QuestionLearningObjectLink"),
        migrations.RemoveField(model_name="question", name="adaptive_question"),
        migrations.RemoveField(model_name="question", name="source_type"),
        migrations.RemoveField(model_name="question", name="teacher_edited"),
        migrations.RemoveField(model_name="question", name="bloom_level"),
        migrations.RemoveField(model_name="question", name="thinking_order"),
        migrations.RemoveField(model_name="question", name="difficulty"),
        migrations.RemoveField(model_name="question", name="category"),
        migrations.RemoveField(model_name="question", name="validation_status"),
        migrations.RemoveField(model_name="question", name="validation_issues"),
        migrations.RenameModel(old_name="Question", new_name="ExtractedQuestion"),
    ]
