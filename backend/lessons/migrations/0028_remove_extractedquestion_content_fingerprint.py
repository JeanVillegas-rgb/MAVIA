from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("lessons", "0027_extractedquestion"),
    ]

    operations = [
        migrations.RemoveField(model_name="extractedquestion", name="content_fingerprint"),
    ]
