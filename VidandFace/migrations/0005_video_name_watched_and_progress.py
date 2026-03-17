from django.db import migrations, models


def dedupe_watched_by_video_name(apps, schema_editor):
    WatchedVideo = apps.get_model('VidandFace', 'WatchedVideo')
    seen = set()

    # Keep the earliest row per (user_id, video_name)
    for row in WatchedVideo.objects.using(schema_editor.connection.alias).order_by('user_id', 'video_name', 'watched_at', 'id'):
        key = (row.user_id, (row.video_name or '').strip())
        if not key[1]:
            continue
        if key in seen:
            WatchedVideo.objects.using(schema_editor.connection.alias).filter(id=row.id).delete()
        else:
            seen.add(key)


class Migration(migrations.Migration):

    dependencies = [
        ('VidandFace', '0004_progress_current_index_next_active'),
    ]

    operations = [
        migrations.RunPython(dedupe_watched_by_video_name, reverse_code=migrations.RunPython.noop),
        migrations.AlterUniqueTogether(
            name='watchedvideo',
            unique_together={('user', 'video_name')},
        ),
        migrations.RemoveField(
            model_name='watchedvideo',
            name='video_index',
        ),
        migrations.AddField(
            model_name='uservideoprogress',
            name='current_video_name',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.RemoveField(
            model_name='uservideoprogress',
            name='current_video_index',
        ),
    ]
