# Generated migration for making youtube_link optional to support MP3 uploads

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("note_generator", "0002_rename_blogpost_notepost"),
    ]

    operations = [
        migrations.AlterField(
            model_name="notepost",
            name="youtube_link",
            field=models.URLField(blank=True, null=True),
        ),
    ]
