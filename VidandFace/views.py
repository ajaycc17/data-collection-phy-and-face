from pathlib import Path
import base64
import json
import csv
import random
from urllib.parse import urlencode
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
from django.db import IntegrityError, transaction

from .models import CaptureRating, UserVideoProgress, WatchedVideo, PayoutDetailsSubmission


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

    watched_names = set(
        WatchedVideo.objects.filter(user=request.user).values_list('video_name', flat=True)
    )

    current_video_names = {vf['name'] for vf in video_files}
    watched_current_names = watched_names.intersection(current_video_names)

    has_payout_submission = PayoutDetailsSubmission.objects.filter(user=request.user).exists()

    if total > 0 and len(watched_current_names) >= total:
        if getattr(settings, 'ASK_PAYOUT_DETAILS', False) and not has_payout_submission:
            return redirect('completion-details')
        return redirect('completion-thank-you')

    # Which video to show (0-based). Use ?i=0, ?i=1, ...
    # Default comes from persisted current video name (stable across new videos).
    default_index = 0
    if progress.current_video_name:
        for idx, vf in enumerate(video_files):
            if vf['name'] == progress.current_video_name:
                default_index = idx
                break
    try:
        current_index = int(request.GET.get('i', str(default_index)))
    except ValueError:
        current_index = default_index

    if total == 0:
        current_video = None
        current_index = 0
    else:
        current_index = max(0, min(current_index, total - 1))

        nav = (request.GET.get('nav') or '').strip().lower()
        if nav in {'next', 'prev'}:
            if nav == 'next':
                candidates = [
                    idx
                    for idx in range(total)
                    if video_files[idx]['name'] not in watched_names
                    and idx != current_index
                ]
            else:
                candidates = [
                    idx
                    for idx in range(total)
                    if video_files[idx]['name'] in watched_names
                    and idx != current_index
                ]

            if candidates:
                chosen = random.SystemRandom().choice(candidates)
                params = {'i': str(chosen)}
                return redirect(f"{reverse('video-gallery')}?{urlencode(params)}")

        current_video = video_files[current_index]

    current_name = current_video['name'] if current_video else ''
    is_watched = bool(current_name) and current_name in watched_current_names
    can_next = any(
        vf['name'] not in watched_current_names and vf['name'] != current_name
        for vf in video_files
    )

    # Persist "where the user currently is" and whether Next is active.
    # IMPORTANT: we do NOT store a computed "next video id" in the DB.
    progress.current_video_name = current_name
    progress.next_active = bool(is_watched)
    # Keep the stored watched count consistent with DB rows.
    progress.videos_watched = len(watched_current_names)
    progress.save(update_fields=['current_video_name', 'next_active', 'videos_watched', 'updated_at'])

    progress_pct = (100 * progress.videos_watched / total) if total else 0

    show_mcq_once = bool(request.session.pop('show_mcq_once', False))

    context = {
        'video_files': video_files,
        'current_video': current_video,
        'current_index': current_index,
        'is_watched': is_watched,
        'show_mcq_once': show_mcq_once,
        'total': total,
        'has_prev': False,
        'has_next': total > 0 and can_next,
        'user_email': request.user.email or request.user.username,
        'videos_watched': progress.videos_watched,
        'progress_pct': round(progress_pct, 1),
        'capture_interval_ms': getattr(settings, 'CAPTURE_INTERVAL_MS', 10000),
        'ask_payout_details': bool(getattr(settings, 'ASK_PAYOUT_DETAILS', False)) and not has_payout_submission,
    }

    return render(request, 'VidandFace/video_gallery.html', context)


@login_required
def completion_details(request):
    if not bool(getattr(settings, 'ASK_PAYOUT_DETAILS', False)):
        return redirect('completion-thank-you')

    if PayoutDetailsSubmission.objects.filter(user=request.user).exists():
        return redirect('completion-thank-you')

    videos_dir = Path(settings.MEDIA_ROOT) / 'videos'
    allowed_extensions = {'.mp4', '.webm', '.ogg', '.mov', '.m4v'}

    video_names = []
    if videos_dir.exists():
        for file_path in sorted(videos_dir.iterdir()):
            if file_path.is_file() and file_path.suffix.lower() in allowed_extensions:
                video_names.append(file_path.name)

    total = len(video_names)
    watched_names = set(
        WatchedVideo.objects.filter(user=request.user).values_list('video_name', flat=True)
    )
    watched_current_names = watched_names.intersection(set(video_names))
    if total > 0 and len(watched_current_names) < total:
        return redirect('video-gallery')

    error = ''
    upi_id = ''
    confirm_upi_id = ''
    whatsapp_number = ''

    if request.method == 'POST':
        upi_id = str(request.POST.get('upi_id') or '').strip()
        confirm_upi_id = str(request.POST.get('confirm_upi_id') or '').strip()
        whatsapp_number = str(request.POST.get('whatsapp_number') or '').strip()

        if not upi_id or not confirm_upi_id or not whatsapp_number:
            error = 'Please fill all fields.'
        elif upi_id != confirm_upi_id:
            error = 'UPI IDs do not match.'
        else:
            submission = None
            try:
                with transaction.atomic():
                    submission, created = PayoutDetailsSubmission.objects.get_or_create(user=request.user)
                    if not created:
                        return redirect('completion-thank-you')

                csv_path = Path(settings.MEDIA_ROOT) / 'payout_details.csv'
                csv_path.parent.mkdir(parents=True, exist_ok=True)
                write_header = not csv_path.exists()

                with csv_path.open('a', newline='', encoding='utf-8') as f:
                    writer = csv.DictWriter(
                        f,
                        fieldnames=['timestamp', 'user_email', 'mobile_number', 'upi_id'],
                    )
                    if write_header:
                        writer.writeheader()
                    writer.writerow(
                        {
                            'timestamp': _unix_ms(),
                            'user_email': request.user.email or request.user.username or '',
                            'mobile_number': whatsapp_number,
                            'upi_id': upi_id,
                        }
                    )
            except IntegrityError:
                return redirect('completion-thank-you')
            except Exception:
                if submission is not None:
                    PayoutDetailsSubmission.objects.filter(user=request.user).delete()
                raise

            return redirect('completion-thank-you')

    return render(
        request,
        'VidandFace/completion_details.html',
        {
            'error': error,
            'upi_id': upi_id,
            'confirm_upi_id': confirm_upi_id,
            'whatsapp_number': whatsapp_number,
            'user_email': request.user.email or request.user.username,
        },
    )


@login_required
def completion_thank_you(request):
    return render(request, 'VidandFace/completion_thank_you.html', {})


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

    video_name = str(payload.get('video_name') or '').strip()
    video_index_raw = payload.get('video_index', None)

    # Backward-compatible fallback: if client still posts an index, map it to a name.
    if not video_name and video_index_raw is not None:
        try:
            video_index = int(video_index_raw)
        except (TypeError, ValueError):
            return JsonResponse({'ok': False, 'error': 'Invalid video_index'}, status=400)

        videos_dir = Path(settings.MEDIA_ROOT) / 'videos'
        allowed_extensions = {'.mp4', '.webm', '.ogg', '.mov', '.m4v'}
        video_names = []
        if videos_dir.exists():
            for file_path in sorted(videos_dir.iterdir()):
                if file_path.is_file() and file_path.suffix.lower() in allowed_extensions:
                    video_names.append(file_path.name)
        if 0 <= video_index < len(video_names):
            video_name = video_names[video_index]

    if not video_name:
        return JsonResponse({'ok': False, 'error': 'Missing video_name'}, status=400)

    progress, _ = UserVideoProgress.objects.get_or_create(user=request.user)

    WatchedVideo.objects.get_or_create(
        user=request.user,
        video_name=video_name,
    )

    progress.videos_watched = WatchedVideo.objects.filter(user=request.user).count()
    # Persist current video and unlock Next. Do NOT persist a "next" target.
    progress.current_video_name = video_name
    progress.next_active = True
    progress.save(update_fields=['videos_watched', 'current_video_name', 'next_active', 'updated_at'])

    return JsonResponse({'ok': True, 'videos_watched': progress.videos_watched, 'current_video_name': progress.current_video_name, 'next_active': progress.next_active})


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
        request.session['show_mcq_once'] = True
        return redirect('video-gallery')

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
        request.session['show_mcq_once'] = True
        return redirect('video-gallery')

    return render(request, 'VidandFace/login.html')


@login_required
def logout_view(request):
    logout(request)
    return redirect('login')


def logout_on_close(request):
    """No-op endpoint.

    Historically this was called via `sendBeacon` on `beforeunload/pagehide`.
    Those events also fire during a normal refresh/navigation, which caused users
    to be logged out unexpectedly.

    Per current UX: users should only be logged out when they explicitly press
    the Logout button.
    """
    if request.method != 'POST':
        return HttpResponse(status=405)
    return HttpResponse(status=204)

