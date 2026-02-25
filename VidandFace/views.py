from pathlib import Path
import base64
import json
import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.shortcuts import redirect
from django.http import HttpResponse
from django.urls import reverse

from .models import CaptureRating, UserVideoProgress, WatchedVideo


def _unix_ms(dt: datetime | None = None) -> int:
    if dt is None:
        dt = timezone.now()
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return int(dt.timestamp() * 1000)


@login_required
def video_gallery(request):
    videos_dir = Path(settings.MEDIA_ROOT) / 'videos'
    allowed_extensions = {'.mp4', '.webm', '.ogg', '.mov', '.m4v'}

    video_files = []
    if videos_dir.exists():
        for file_path in sorted(videos_dir.iterdir()):
            if file_path.is_file() and file_path.suffix.lower() in allowed_extensions:
                video_files.append(
                    {
                        'name': file_path.name,
                        'url': f"{settings.MEDIA_URL}videos/{file_path.name}",
                    }
                )

    total = len(video_files)

    progress, _ = UserVideoProgress.objects.get_or_create(user=request.user)

    # Which video to show (0-based). Use ?i=0, ?i=1, ...
    default_index = progress.next_video_index
    try:
        current_index = int(request.GET.get('i', str(default_index)))
    except ValueError:
        current_index = default_index

    if total == 0:
        current_video = None
        current_index = 0
    else:
        current_index = max(0, min(current_index, total - 1))
        current_video = video_files[current_index]

    progress_pct = (100 * progress.videos_watched / total) if total else 0

    context = {
        'video_files': video_files,
        'current_video': current_video,
        'current_index': current_index,
        'total': total,
        'has_prev': total > 0 and current_index > 0,
        'has_next': total > 0 and current_index < total - 1,
        'prev_index': max(0, current_index - 1),
        'next_index': min(total - 1, current_index + 1) if total > 0 else 0,
        'user_email': request.user.email or request.user.username,
        'videos_watched': progress.videos_watched,
        'progress_pct': round(progress_pct, 1),
        'capture_interval_ms': getattr(settings, 'CAPTURE_INTERVAL_MS', 10000),
    }

    return render(request, 'VidandFace/video_gallery.html', context)


@login_required
@require_POST
def capture_photo(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)

    video_name = str(payload.get('video_name') or '').strip() or 'unknown'
    image_data_url = str(payload.get('image_data') or '').strip()
    if not image_data_url.startswith('data:image/'):
        return JsonResponse({'ok': False, 'error': 'Missing image data'}, status=400)

    # Expected: data:image/jpeg;base64,<...>
    try:
        header, b64_data = image_data_url.split(',', 1)
    except ValueError:
        return JsonResponse({'ok': False, 'error': 'Invalid data URL'}, status=400)

    content_type = header.split(';', 1)[0]  # data:image/jpeg
    ext = 'jpg'
    if content_type.endswith('/png'):
        ext = 'png'
    elif content_type.endswith('/jpeg') or content_type.endswith('/jpg'):
        ext = 'jpg'
    else:
        return JsonResponse({'ok': False, 'error': 'Unsupported image type'}, status=415)

    try:
        image_bytes = base64.b64decode(b64_data)
    except (base64.binascii.Error, ValueError):
        return JsonResponse({'ok': False, 'error': 'Invalid base64 data'}, status=400)

    safe_video = slugify(Path(video_name).stem) or 'unknown'
    user_root = Path(settings.MEDIA_ROOT) / 'users' / str(request.user.id)
    captures_dir = user_root / 'captures' / safe_video
    captures_dir.mkdir(parents=True, exist_ok=True)

    timestamp_ms = _unix_ms()
    filename_stem = f'user_{request.user.id}_{timestamp_ms}'
    filename = f'{filename_stem}.{ext}'
    file_path = captures_dir / filename
    collision_idx = 1
    while file_path.exists():
        filename = f'{filename_stem}_{collision_idx}.{ext}'
        file_path = captures_dir / filename
        collision_idx += 1
    file_path.write_bytes(image_bytes)

    rel_path = f"users/{request.user.id}/captures/{safe_video}/{filename}"
    rel_url = f"{settings.MEDIA_URL}{rel_path}"
    return JsonResponse({'ok': True, 'url': rel_url, 'capture_id': rel_path})


@login_required
@require_POST
def submit_rating(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)

    video_name = str(payload.get('video_name') or '').strip() or 'unknown'
    capture_id = str(payload.get('capture_id') or '').strip()

    try:
        valence = Decimal(str(payload.get('valence'))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        arousal = Decimal(str(payload.get('arousal'))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Invalid valence/arousal'}, status=400)

    expected_prefix = f"users/{request.user.id}/captures/"
    if not capture_id.startswith(expected_prefix):
        return JsonResponse({'ok': False, 'error': 'Invalid capture_id'}, status=400)

    if not (Decimal('1.00') <= valence <= Decimal('5.00') and Decimal('1.00') <= arousal <= Decimal('5.00')):
        return JsonResponse({'ok': False, 'error': 'valence/arousal must be 1.00..5.00'}, status=400)

    CaptureRating.objects.create(
        user=request.user,
        video_name=video_name,
        capture_rel_path=capture_id,
        valence=valence,
        arousal=arousal,
    )

    # Also append to CSV for easy export.
    # CSV path: <MEDIA_ROOT>/users/<id>/user_<id>.csv
    snapshot_name = Path(capture_id).name
    capture_ts_ms = _unix_ms()
    try:
        stem = Path(capture_id).stem  # e.g. user_1_20260222_123148_551904 or legacy 20260222_123148_551904
        parts = stem.split('_')
        ms_token = next((p for p in reversed(parts) if p.isdigit() and len(p) >= 13), None)
        if ms_token is not None:
            capture_ts_ms = int(ms_token)
        else:
            if len(parts) >= 5:
                ts_str = '_'.join(parts[2:])
            elif len(parts) == 3:
                ts_str = stem
            else:
                raise ValueError('unknown stem format')
            parsed = datetime.strptime(ts_str, '%Y%m%d_%H%M%S_%f')
            capture_ts_ms = _unix_ms(parsed)
    except Exception:
        pass

    csv_path = Path(settings.MEDIA_ROOT) / 'users' / str(request.user.id) / f'user_{request.user.id}.csv'
    write_header = not csv_path.exists()
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    with csv_path.open('a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(
            f,
            fieldnames=['video_name', 'timestamp', 'snapshot_name', 'valence', 'arousal'],
        )
        if write_header:
            writer.writeheader()
        writer.writerow(
            {
                'video_name': video_name,
                'timestamp': str(capture_ts_ms),
                'snapshot_name': snapshot_name,
                'valence': f"{valence:.2f}",
                'arousal': f"{arousal:.2f}",
            }
        )

    return JsonResponse({'ok': True})


@login_required
@require_POST
def submit_clip_questionnaire(request):
    """Append one row to user_<id>_clips-ques.csv: timestamp, video_name, clip_valence, clip_arousal, user_valence, user_arousal."""
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)

    video_name = str(payload.get('video_name') or '').strip() or 'unknown'
    try:
        clip_valence = float(payload.get('clip_valence', 0))
        clip_arousal = float(payload.get('clip_arousal', 0))
        user_valence = float(payload.get('user_valence', 0))
        user_arousal = float(payload.get('user_arousal', 0))
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Invalid slider values'}, status=400)

    if not all(1 <= v <= 5 for v in (clip_valence, clip_arousal, user_valence, user_arousal)):
        return JsonResponse({'ok': False, 'error': 'All values must be between 1 and 5'}, status=400)

    user_id = request.user.id
    csv_dir = Path(settings.MEDIA_ROOT) / 'users' / str(user_id)
    csv_dir.mkdir(parents=True, exist_ok=True)
    csv_filename = f'user_{user_id}_clips-ques.csv'
    csv_path = csv_dir / csv_filename

    write_header = not csv_path.exists()
    fieldnames = ['timestamp', 'video_name', 'clip_valence', 'clip_arousal', 'user_valence', 'user_arousal']
    with csv_path.open('a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerow({
            'timestamp': str(_unix_ms()),
            'video_name': video_name,
            'clip_valence': f'{clip_valence:.2f}',
            'clip_arousal': f'{clip_arousal:.2f}',
            'user_valence': f'{user_valence:.2f}',
            'user_arousal': f'{user_arousal:.2f}',
        })

    return JsonResponse({'ok': True})


@login_required
@require_POST
def mark_watched(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)

    video_name = str(payload.get('video_name') or '').strip() or 'unknown'
    try:
        video_index = int(payload.get('video_index'))
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Invalid video_index'}, status=400)

    progress, _ = UserVideoProgress.objects.get_or_create(user=request.user)

    watched, created = WatchedVideo.objects.get_or_create(
        user=request.user,
        video_index=max(0, video_index),
        defaults={'video_name': video_name},
    )
    if not created and watched.video_name != video_name:
        watched.video_name = video_name
        watched.save(update_fields=['video_name'])

    progress.videos_watched = WatchedVideo.objects.filter(user=request.user).count()
    progress.next_video_index = max(progress.next_video_index, video_index + 1)
    progress.save(update_fields=['videos_watched', 'next_video_index', 'updated_at'])

    return JsonResponse({'ok': True, 'videos_watched': progress.videos_watched, 'next_video_index': progress.next_video_index})


@login_required
@require_POST
def submit_mcq_questionnaire(request):
    """Append one row to a common CSV (all users): username_or_email, timestamp, q1..q20."""
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)

    username_or_email = (request.user.email or request.user.username or '').strip() or 'unknown'
    answers = {}
    for i in range(1, 21):
        key = f'q{i}'
        val = str(payload.get(key) or '').strip()
        if not val:
            return JsonResponse({'ok': False, 'error': f'Missing {key}'}, status=400)
        answers[key] = val[:200]  # cap length

    csv_path = Path(settings.MEDIA_ROOT) / 'questionnaire_responses.csv'
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ['username_or_email', 'timestamp'] + [f'q{i}' for i in range(1, 21)]
    write_header = not csv_path.exists()
    with csv_path.open('a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerow({
            'username_or_email': username_or_email,
            'timestamp': str(_unix_ms()),
            **answers,
        })

    return JsonResponse({'ok': True})


def signup_view(request):
    if request.method == 'POST':
        if (request.POST.get('camera_granted') or '') != '1':
            return render(request, 'VidandFace/signup.html', {'error': 'Camera permission is required to sign up.'})
        email = (request.POST.get('email') or '').strip().lower()
        password = request.POST.get('password') or ''
        if not email or not password:
            return render(request, 'VidandFace/signup.html', {'error': 'Email and password are required.'})
        if User.objects.filter(username=email).exists():
            return render(request, 'VidandFace/signup.html', {'error': 'Email already registered.'})

        user = User.objects.create_user(username=email, email=email, password=password)
        login(request, user)
        return redirect(reverse('video-gallery') + '?show_mcq=1')

    return render(request, 'VidandFace/signup.html')


def login_view(request):
    if request.method == 'POST':
        if (request.POST.get('camera_granted') or '') != '1':
            return render(request, 'VidandFace/login.html', {'error': 'Camera permission is required to log in.'})
        email = (request.POST.get('email') or '').strip().lower()
        password = request.POST.get('password') or ''
        user = authenticate(request, username=email, password=password)
        if user is None:
            return render(request, 'VidandFace/login.html', {'error': 'Invalid email or password.'})
        login(request, user)
        return redirect(reverse('video-gallery') + '?show_mcq=1')

    return render(request, 'VidandFace/login.html')


@login_required
def logout_view(request):
    logout(request)
    return redirect('login')


def logout_on_close(request):
    """Called via sendBeacon when tab is closed; invalidates session and returns 204."""
    if request.method != 'POST':
        return HttpResponse(status=405)
    logout(request)
    return HttpResponse(status=204)

