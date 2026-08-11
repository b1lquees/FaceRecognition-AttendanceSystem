# Face Recognition Attendance System

THIS PROJECT IS STILL IN PROGRESS

A web-based attendance system that identifies people from a live webcam feed and records
their attendance automatically. Built with Flask, `face_recognition` (dlib), OpenCV and SQLite.

Point a browser at the camera page, and anyone enrolled in the system is recognised and
marked present — once per day, no duplicates. Administrators can export the full record
as CSV.

![Tests](https://github.com/b1lquees/FaceRecognition-AttendanceSystem/actions/workflows/tests.yml/badge.svg)

---

## Features

- **Live recognition in the browser** — the webcam feed is captured client-side and sent
  to the server for identification every 1.5 seconds.
- **Check-in and check-out** — one record per person per day, enforced by the database,
  with arrival and departure times and the duration between them.
- **Role-based access** — `viewer` accounts can read attendance; `admin` accounts can also
  export it.
- **CSV export** — generated in memory and streamed as a download, for opening in Excel or
  Google Sheets.
- **Today / all-time views** — a filtered daily register and a full searchable archive.
- **Enrolment from the browser** — admins add people by uploading photos; the running
  server picks them up without a restart.
- **Anti-spoofing** — a liveness model refuses printed photos and screens before
  recognition ever runs. Ships disabled pending calibration; see below.
- **Desktop mode** — `scripts/recognise_live.py` runs the same recognition in a native OpenCV
  window, useful for testing without a browser.

---

## How it works

```mermaid
flowchart TD
    A[known_faces/&lt;person&gt;/*.jpg] -->|scripts/build_encodings.py| B[encodings.npz<br/>128-d vectors per person]
    C[Browser webcam] -->|canvas.toDataURL - base64 JPEG| D[POST /recognize]
    B --> E
    D --> L{liveness:<br/>real face?}
    L -->|no| S[Spoof - refused]
    L -->|yes| E[face_recognition<br/>detect + encode]
    E -->|Euclidean distance vs. every known encoding| F{closest distance<br/>&lt; 0.6?}
    F -->|yes| G[check_in / check_out]
    F -->|no| H[Unknown]
    G --> I[(attendance.db)]
```

Recognition works by turning each face into a **128-dimensional vector** (an "encoding").
Two photos of the same person produce vectors that sit close together in that space; two
different people produce vectors that sit far apart. Identifying someone is therefore just
finding the nearest stored encoding and checking that it is near enough — the `TOLERANCE`
constant in [`attendance/recognition.py`](attendance/recognition.py), set to `0.6`.

Lower tolerance means stricter matching: fewer false matches, but more failures to
recognise someone whose appearance has changed. Higher tolerance is more forgiving and
correspondingly more likely to confuse two people.

---

## Project structure

The application is a package built by a **factory function**. Nothing is created at
import time: `create_app()` builds a Flask app on demand, which is what lets the test
suite construct a throwaway app with its own config and its own database.

```
wsgi.py                     entry point (dev server and gunicorn/waitress)
attendance/
    __init__.py             create_app() -- the application factory
    config.py               per-environment settings and secret key handling
    db.py                   connection helper, schema and migrations
    attendance_db.py        attendance queries
    auth_db.py              password hashing, approval, user verification
    decorators.py           @login_required / @admin_required
    security.py             CSRF token generation and checking
    recognition.py          identify_face() and the encoding cache
    liveness.py             anti-spoofing gate
    enrolment.py            adding a person from the browser
    formatting.py           duration / time display helpers
    ratelimit.py            login and signup throttling
    models/                 the anti-spoofing weights and their licence
    routes/
        main.py             /
        auth.py             /login, /logout, /signup
        records.py          /attendance/today, /all, /export
        recognition.py      /camera, /recognize
        admin.py            /admin/users, /admin/enrol
    templates/              base.html plus one file per page
    static/
scripts/                    command-line tools (see below)
tests/                      pytest suite
docs/notes.md               learning notes
pyproject.toml              package declaration and pytest config
```

Everything in `scripts/` is run directly and does one job:

| Script | What it does |
| --- | --- |
| `init_db.py` | Create the tables (safe to re-run) |
| `create_user.py` | Add an account, prompting for the password |
| `build_encodings.py` | Rebuild the encoding cache from `known_faces/` |
| `recognise_live.py` | Desktop webcam viewer, same recognition without a browser |
| `peek_db.py` | Print every attendance row |
| `reset_attendance.py` | Delete one person's records |
| `webcam_test.py` | Check the camera works at all |
| `calibrate_liveness.py` | Measure the anti-spoofing model on your own camera |

These import `attendance`, which is why the project has to be installed with
`pip install -e .` — see Setup below.

---

## Setup

Requires **Python 3.12+**.

```bash
git clone https://github.com/b1lquees/FaceRecognition-AttendanceSystem.git
cd FaceRecognition-AttendanceSystem
python -m venv venv
```

Activate the virtual environment — `venv\Scripts\activate` on Windows,
`source venv/bin/activate` on macOS/Linux — then install:

```bash
pip install -r requirements.txt
pip install --no-deps face-recognition==1.3.0
pip install -e . --no-deps
```

> **Why three commands?**
>
> `face-recognition` declares a dependency on `dlib`, which is published only as a source
> distribution and needs CMake and a C++ compiler to build. `dlib-bin` (already in
> `requirements.txt`) provides the identical `dlib` import as a prebuilt wheel, so
> `--no-deps` skips the source build entirely.
>
> The last line installs this project itself, in editable mode — no files are copied, pip
> just records where the source lives. That is what makes `import attendance` resolve from
> any directory, which the scripts in `scripts/` need: Python puts a script's *own*
> directory on `sys.path`, not the project root.

On Linux, OpenCV also needs a couple of system libraries:

```bash
sudo apt-get install -y libgl1 libglib2.0-0
```

---

## Usage

**1. Enrol faces.** The easiest way is the **Enrol** page at `/admin/enrol`, which takes
photo uploads and makes the person recognisable immediately, with no restart.

To do it from the shell instead, create one folder per person under `known_faces/`,
containing a few clear photos of just that person:

```
known_faces/
  Alice/
    photo1.jpg
    photo2.jpg
  Bob/
    photo1.jpg
```

Then build the encoding cache. Photos containing zero or multiple faces are skipped with a
warning:

```bash
python scripts/build_encodings.py
```

**2. Create the database and an account.** The password is prompted for, never passed as
an argument:

```bash
python scripts/init_db.py
python scripts/create_user.py alice --role admin
```

**3. Run the server.**

```bash
python wsgi.py
```

Open <http://127.0.0.1:5000/login>, sign in, then go to `/camera`.

| Route | Access | Description |
| --- | --- | --- |
| `/login`, `/logout` | public | Sign in and out |
| `/signup` | public | Request an account (created pending, cannot sign in yet) |
| `/camera` | any user | Live recognition page |
| `/recognize` | any user | `POST` endpoint that identifies one frame |
| `/attendance/today` | any user | Today's register |
| `/attendance/all` | any user | Full archive |
| `/attendance/export` | **admin** | CSV download |
| `/admin/users` | **admin** | Approve or reject access requests |
| `/admin/enrol` | **admin** | Add or remove a person |

### Checking in and out

The camera page has a **Check in / Check out** switch, and the mode is sent with each
frame. One row per person per day holds both times:

| Mode | First time today | Again the same day |
| --- | --- | --- |
| Check in | records `time_in` | reports *already checked in* |
| Check out | records `time_out` | reports *already checked out* |

Checking out is deliberately a **choice, not an inference**. Setting `time_out` on every
sighting would mean walking past the camera five minutes after arriving recorded a
five-minute day, and it would make "already checked in" impossible to report. Checking
out before checking in reports *not checked in* and records nothing.

A missing or unrecognised mode falls back to check-in, because an unwanted arrival is
correctable and an unwanted departure is worse.

Both times live in the same row, so the `UNIQUE(date, student_id)` index still means
exactly one record per person per day.

### Anti-spoofing

Recognition alone cannot tell a person from a photograph of them: a printed photo
produces the same encoding the real person does, so it checks them in. A liveness model
([facenox/face-antispoof-onnx](https://github.com/facenox/face-antispoof-onnx),
Apache 2.0, weights and licence in `attendance/models/`) runs **before** recognition and
refuses spoofs, so a photo is never even identified — which is also why a refusal does
not report whose photo it was.

**It ships disabled**, and that is deliberate: the right threshold depends on your
camera and your lighting, and nobody can pick it for you from the outside.

Measured on one webcam, 40 samples each:

| | min | median | max |
| --- | --- | --- | --- |
| Real face | −3.82 | **+1.63** | +5.04 |
| Photo / screen | −11.06 | **−6.72** | −2.39 |

Those separate cleanly at the default threshold of `0.0`: no spoof got through, with
2.39 of margin, at the cost of roughly **20% of frames of a real person being
refused**. That cost is mostly absorbed by the camera retrying every 1.5 seconds.

The trade is deliberately lopsided. A false reject costs a second; a false accept
costs the entire point of the feature — and an attacker holding up a photo gets to
retry too. So the threshold is set to protect the margin against spoofs, not to
minimise inconvenience.

So measure it first. Run this as yourself, then again holding up a printed photo or a
phone screen:

```bash
python scripts/calibrate_liveness.py --label real
```

It reports the range of scores it saw and suggests a threshold separating real from
spoof. If the two ranges overlap, no threshold works and the model is not reliable in
your conditions — which is worth knowing before you depend on it. Once you have a number:

```bash
set LIVENESS_ENABLED=1
set LIVENESS_THRESHOLD=0.0
```

If it starts refusing you, set `LIVENESS_ENABLED=0` and recalibrate rather than lowering
the threshold until everything passes.

### Kiosk mode vs. personal check-in

The system supports two quite different models, chosen with `KIOSK_MODE`.

**Kiosk (`KIOSK_MODE=1`, the default)** — one camera by a door. Anyone enrolled who is
recognised is marked present, whoever happens to be signed in at the station. The account
is the operator running the terminal, not the person being recorded.

**Personal (`KIOSK_MODE=0`)** — each person signs into their own account and checks
themselves in. A recognised face that does not belong to the signed-in account is
refused. This is what makes *"only the registered person can check in"* actually true:
in kiosk mode, being signed in as anyone lets you mark anyone else present, because
`mark_attendance()` records whoever was recognised and never consults the session.

Personal mode requires each account to be **linked to an enrolled person**, which an
admin does from `/admin/users`. Linking is admin-only by design — if people could choose
their own identity at signup, anyone could claim to be anyone, which is the exact
impersonation the mode exists to prevent. An unlinked account cannot check in at all.

A refused check-in deliberately does **not** report who was actually recognised.
Reporting it would tell the signed-in user who was standing in front of the camera,
leaking other people's presence to anyone able to point a webcam at them.

> Note that neither mode defends against a **photograph** of the right person — see
> Known Limitations.

### Accounts and approval

Anyone can request an account at `/signup`, but it is created **pending** and cannot sign
in until an admin approves it from `/admin/users`. Signup always creates a `viewer`; the
role is not a form field, because if it were, anyone who could reach the page could make
themselves an admin.

Promoting someone to admin is deliberately a command-line action, so it always requires
access to the server:

```bash
python scripts/create_user.py alice --role admin
```

Accounts created that way are approved immediately — someone who already has shell access
does not need to ask themselves for permission.

Security-relevant actions are written to an audit log ([attendance/audit.py](attendance/audit.py)): logins and failures, signups, approvals, rejections, account
linking, enrolment, removal, and refused spoof attempts — each with who did it and
from where. Passwords, tokens and face encodings are never logged.

Removing someone from `/admin/enrol` deletes their photos and face data but **keeps
their attendance history**. Un-enrolling means the system stops recognising them from
now on; it does not mean they were never there.

Every `POST` is CSRF-protected by a token stored in the session
([attendance/security.py](attendance/security.py)). Forms carry it in a hidden field;
`/recognize` sends JSON, so it sends the same value as an `X-CSRF-Token` header.

`/login` and `/signup` are rate limited per client address
([attendance/ratelimit.py](attendance/ratelimit.py)) — 10 login attempts per 5 minutes,
5 signups per hour. The counters live in the server process's memory, so they reset on
restart and each worker process keeps its own. That is fine for one classroom-sized
deployment; anything larger wants Flask-Limiter backed by Redis so the count is shared.

---

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `FLASK_SECRET_KEY` | generated for development | Signs session cookies. **Required in production.** |
| `FLASK_ENV` | unset | Set to `production` to make a missing secret key a hard error, and to require HTTPS for the session cookie. |
| `LIVENESS_ENABLED` | `0` (off) | Anti-spoofing. Calibrate before turning on — see below. |
| `LIVENESS_THRESHOLD` | `0.0` | Score above which a face counts as real. Higher is stricter. |
| `LOG_LEVEL` | `INFO` | The audit trail is logged at INFO; raising this discards it. |
| `LOG_FILE` | unset | Also write a rotating log file. Unset means stderr only. |
| `KIOSK_MODE` | `1` (on) | `1` for a shared door camera, `0` for personal check-in. See below. |
| `ATTENDANCE_DB` | `attendance.db` | Path to the SQLite database. The test suite points this at a temporary file. |

The secret key is what stops someone forging a session cookie that claims
`role=admin`, so it must never be committed. In development one is generated
automatically and cached in `.flask_secret_dev` (gitignored). In production:

```bash
export FLASK_SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")
export FLASK_ENV=production
```

---

## Running the tests

```bash
pip install -r requirements-dev.txt
pytest -v
```

The suite covers attendance de-duplication, recognition of unknown faces, password
hashing, and the route-level authentication and authorisation gates. Every test runs
against a throwaway database in pytest's temporary directory, so the real
`attendance.db` is never touched.

---

## Known limitations

These are real constraints, not TODOs that are nearly done. Read them before using this
anywhere that matters.

- **Anti-spoofing ships switched off and uncalibrated.** With `LIVENESS_ENABLED=0`, which
  is the default, holding a printed photo up to the camera marks that person present.
  Turning it on is a two-command job (see Anti-spoofing above) but you must calibrate it
  first, and the honest position is that nobody has measured how it behaves on your
  camera yet.
- **Even calibrated, it does not stop a video replay** on a good screen. It raises the
  bar a long way over "a printed photo works"; it does not eliminate the attack.
- **Recognition runs synchronously in the request thread.** Detection is done on a
  half-scale frame, but encoding is not and cannot be — measured at roughly 1.2s per
  frame on modest hardware. Fine for one camera; it will not hold up under concurrent users.
- **Enrolment quality is only as good as the photos.** A few shots from different
  angles and in different lighting matter far more than one perfect one, and nothing in
  the interface forces that.
- **SQLite with per-call connections** suits a single classroom-sized deployment. It is not
  appropriate for a multi-site or high-concurrency setup.
- **Timestamps use the server's local timezone** and are stored as strings.
- **Recognition accuracy depends heavily on enrolment photo quality** — varied lighting and
  angles help considerably. The underlying model is also documented to be less accurate on
  children and to have measurably uneven accuracy across demographic groups.

## Roadmap

- [ ] Calibrate anti-spoofing and switch it on by default
- [ ] Restructure into a package with a Flask application factory
- [ ] Web-based enrolment, so adding a person does not need shell access
- [ ] Pagination on the all-records view

- [ ] Docker image and a production WSGI entry point
