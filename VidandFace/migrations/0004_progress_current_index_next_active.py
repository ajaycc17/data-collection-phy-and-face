from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('VidandFace', '0003_alter_capturerating_arousal_and_more'),
    ]

    operations = [
        migrations.RenameField(
            model_name='uservideoprogress',
            old_name='next_video_index',
            new_name='current_video_index',
        ),
        migrations.AddField(
            model_name='uservideoprogress',
            name='next_active',
            field=models.BooleanField(default=False),
        ),
    ]
