# Collector (Django + django-tailwind)

This is a Django project that:
- Serves a **video gallery** from files in `media/videos/`.
- While a user watches a video, periodically captures **webcam snapshots**.
- Prompts the user for **valence/arousal ratings** for captured snapshots.
- Collects additional **questionnaires** and writes results to CSV for export.

---

## Tech stack

- Python 3.11 + Django 5
- SQLite (`db.sqlite3`) for core app data
- Tailwind via `django-tailwind` + PostCSS (theme app: `theme/`)
- Node.js + npm (used by Tailwind build)

---

## Project layout (high level)

- `collector/` – Django project settings/urls
- `VidandFace/` – main Django app (views, models, templates)
- `theme/` – Tailwind theme app (`theme/static_src/` contains npm project)
- `media/` – runtime data: videos, captures, CSV exports
- `static/` – project-level static assets

---

## Requirements (PC setup)

1) Install:
- **Python 3.11**
- **Node.js + npm** (LTS is fine)

2) Recommended: git, VS Code

---

## Setup (Windows / PC)

### 1) Create & activate a virtualenv

From the repo root:

**PowerShell**

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

### 2) Install Python dependencies

```powershell
pip install -r requirements.txt
```

### 3) Create your `.env`

Copy `.env.example` to `.env` (keep `.env` private; it’s ignored by `.gitignore`).

```powershell
copy .env.example .env
```

Edit `.env` and set at least `DJANGO_SECRET_KEY`.

### 4) Run migrations

```powershell
python manage.py migrate
```

### 5) Install Node dependencies (Tailwind)

The Tailwind npm project lives in `theme/static_src/`.

```powershell
cd theme\static_src
npm install
```

### 6) Build Tailwind CSS (one-time check)

```powershell
npm run build
cd ..\..\..
```

### 7) Run the dev server

You have two options:

**Option A (recommended): run Django + Tailwind watcher together**

```powershell
honcho start -f Procfile.tailwind
```

**Option B: two terminals**

Terminal 1:
```powershell
python manage.py runserver
```

Terminal 2:
```powershell
python manage.py tailwind start
```

Open: http://127.0.0.1:8000/

---

## Setup (macOS/Linux)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python manage.py migrate
cd theme/static_src && npm install && npm run build
honcho start -f Procfile.tailwind
```

---

## Environment variables (`.env`)

See `.env.example` for the canonical list.

### Required

- `DJANGO_SECRET_KEY` – Django secret key

### Common

- `DJANGO_DEBUG` – `1`/`0` (defaults to `1`)
- `DJANGO_ALLOWED_HOSTS` – comma-separated list, e.g. `localhost,127.0.0.1`

### Tailwind / Node

- `NPM_BIN_PATH` – path to npm used by `django-tailwind`.
  - Defaults to `npm`.
  - On Windows you may need something like:
    - `NPM_BIN_PATH=C:\\Program Files\\nodejs\\npm.cmd`

### App behavior

- `CAPTURE_INTERVAL_SEC` – capture interval in seconds (default: `10`)

---

## Where to put videos

The gallery reads videos from:

- `media/videos/`

Supported extensions:
- `.mp4`, `.webm`, `.ogg`, `.mov`, `.m4v`

Example:

```
media/
  videos/
    video1.mp4
    video2.mov
```

On refresh, the app lists these files and serves them via `MEDIA_URL`.

---

## Where data is stored (hierarchy)

This project stores data in both **SQLite** and **CSV files** for easy export.

### SQLite database

- `db.sqlite3`

Models include:
- `CaptureRating` – links a user, video name, and capture file path to valence/arousal
- `WatchedVideo` – tracks which video index a user completed
- `UserVideoProgress` – tracks next video index and overall progress

### Captured images

When a snapshot is taken, it’s written under `MEDIA_ROOT/users/...`:

```
media/
  users/
    <user_id>/
      captures/
        <video_slug>/
          user_<user_id>_<unix_ms>.jpg
          user_<user_id>_<unix_ms>_2.jpg   # if filename collision
```

- `video_slug` is derived from the video filename.
- Filenames use **Unix milliseconds**.

### Per-user ratings CSV

When the user submits a valence/arousal rating, a row is appended to:

- `media/users/<user_id>/user_<user_id>.csv`

Columns:
- `video_name`
- `timestamp` (Unix milliseconds)
- `snapshot_name` (the capture filename)
- `valence` (1.00–5.00)
- `arousal` (1.00–5.00)

### Per-user clip questionnaire CSV

- `media/users/<user_id>/user_<user_id>_clips-ques.csv`

Columns:
- `timestamp` (Unix milliseconds)
- `video_name`
- `clip_valence`, `clip_arousal`, `user_valence`, `user_arousal`

### Global MCQ questionnaire CSV

- `media/questionnaire_responses.csv`

Columns:
- `username_or_email`
- `timestamp` (Unix milliseconds)
- `q1..q20`

---

## Working with timestamps (Unix ms)

All CSV `timestamp` fields are stored as **Unix milliseconds**.

Python example:

```python
from datetime import datetime, timezone
ms = 1730000000000
print(datetime.fromtimestamp(ms/1000, tz=timezone.utc))
```

---

## Tailwind commands

From repo root:

- Watch mode: `python manage.py tailwind start`
- One-off build: `python manage.py tailwind build`

Or directly in the theme npm folder:

```bash
cd theme/static_src
npm run dev
npm run build
```

Build output CSS is written to:
- `theme/static/css/dist/styles.css`

---

## Troubleshooting

### Tailwind fails to start (`npm` not found)
Set `NPM_BIN_PATH` in `.env` to your npm executable path (see `.env.example`).

### Port already in use
If Django fails with “port 8000 is already in use”, either stop the other process or run:

```bash
python manage.py runserver 0.0.0.0:8001
```

(And update your Procfile if you want that permanently.)

---

## Security notes

- Do not commit `.env`.
- Captures may contain sensitive images; treat the `media/` folder as sensitive data.
