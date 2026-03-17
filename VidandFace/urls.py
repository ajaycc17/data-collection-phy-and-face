from django.urls import path
from . import views

urlpatterns = [
    path('', views.video_gallery, name='video-gallery'),
    path('completion-details/', views.completion_details, name='completion-details'),
    path('completion-thank-you/', views.completion_thank_you, name='completion-thank-you'),
    path('signup/', views.signup_view, name='signup'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('capture-photo/', views.capture_photo, name='capture-photo'),
    path('submit-rating/', views.submit_rating, name='submit-rating'),
    path('submit-clip-questionnaire/', views.submit_clip_questionnaire, name='submit-clip-questionnaire'),
    path('mark-watched/', views.mark_watched, name='mark-watched'),
    path('submit-mcq-questionnaire/', views.submit_mcq_questionnaire, name='submit-mcq-questionnaire'),
    path('logout-on-close/', views.logout_on_close, name='logout-on-close'),
]
