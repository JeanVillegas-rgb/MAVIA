from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("lessons", "0012_learningobjectprerequisiteedge"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="learningobjectprerequisiteedge",
            name="unique_learning_object_prerequisite_edge",
        ),
        migrations.AddField(
            model_name="learningobjectprerequisiteedge",
            name="relation_type",
            field=models.CharField(
                choices=[
                    ("prerequisite", "Prerequisite"),
                    ("topic_parent", "Topic Parent"),
                ],
                default="prerequisite",
                max_length=30,
            ),
        ),
        migrations.AddConstraint(
            model_name="learningobjectprerequisiteedge",
            constraint=models.UniqueConstraint(
                fields=("course", "module_node", "source", "target", "relation_type"),
                name="unique_learning_object_prerequisite_edge",
            ),
        ),
    ]
