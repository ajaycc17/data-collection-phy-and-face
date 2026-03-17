from django.db import models
from django.contrib.auth.models import User


class CaptureRating(models.Model):
	user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
	video_name = models.CharField(max_length=255)
	capture_rel_path = models.CharField(max_length=500)
	valence = models.DecimalField(max_digits=3, decimal_places=2)
	arousal = models.DecimalField(max_digits=3, decimal_places=2)
	created_at = models.DateTimeField(auto_now_add=True)

	def __str__(self) -> str:
		return f"{self.video_name} {self.capture_rel_path} (V={self.valence}, A={self.arousal})"


class UserVideoProgress(models.Model):
	user = models.OneToOneField(User, on_delete=models.CASCADE)
	current_video_name = models.CharField(max_length=255, blank=True, default='')
	next_active = models.BooleanField(default=False)
	videos_watched = models.PositiveIntegerField(default=0)
	updated_at = models.DateTimeField(auto_now=True)


class WatchedVideo(models.Model):
	user = models.ForeignKey(User, on_delete=models.CASCADE)
	video_name = models.CharField(max_length=255)
	watched_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		unique_together = (('user', 'video_name'),)


class PayoutDetailsSubmission(models.Model):
	user = models.OneToOneField(User, on_delete=models.CASCADE)
	submitted_at = models.DateTimeField(auto_now_add=True)
