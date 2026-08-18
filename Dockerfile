FROM python:3.12-slim

# opencv links against libGL and libglib, and the slim image ships neither, so `import
# cv2` fails before the application starts. The same two packages CI installs on its
# Ubuntu runner, for the same reason.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# requirements before the source, and on their own: this layer is only rebuilt when the
# pins change, so editing a template does not reinstall dlib, opencv and onnxruntime.
COPY requirements.txt .

# The second install is separate and dependency-free because face-recognition's metadata
# asks for the source-only `dlib`, which needs CMake and a C++ toolchain to build, while
# dlib-bin in requirements.txt already provides the `dlib` import. Same dance as
# .github/workflows/tests.yml and the README's setup instructions.
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --no-deps face-recognition==1.3.0

COPY . .

# `import attendance` has to resolve from anywhere, not just /app -- the scripts in
# scripts/ are run from wherever an operator happens to be standing. --no-deps because
# the line above installed everything; pyproject.toml declares the package, not its
# dependencies.
RUN pip install --no-cache-dir -e . --no-deps

# Everything the application writes lives under /data and nothing else does. The image
# deliberately contains no data at all -- .dockerignore keeps the database, the photos
# and the encoding cache out of the build context -- so a container starts empty and
# writes into whatever is mounted here. Without a mount that is the container's own
# writable layer, and `docker rm` takes the attendance record with it. The README's run
# command mounts a named volume; use it.
ENV ATTENDANCE_DB=/data/attendance.db \
    KNOWN_FACES_DIR=/data/known_faces \
    ENCODINGS_FILE=/data/encodings.npz \
    FLASK_ENV=production

# Root in a container is root on the host the moment anything escapes it, and nothing
# here needs root: the application writes to /data and reads everything else. The uid is
# fixed rather than left to the system so that files on a mounted host directory have a
# predictable owner.
RUN useradd --create-home --uid 10001 attendance \
    && mkdir -p /data \
    && chown -R attendance:attendance /data

# declares the intent and gives an anonymous volume to anyone who forgets to mount one.
# It is not a substitute for mounting: an anonymous volume survives `docker rm` but is
# unnamed and easy to lose track of.
VOLUME ["/data"]

USER attendance

EXPOSE 8000

# Two workers rather than the usual per-CPU count. Each one loads its own copy of the
# encodings and its own anti-spoofing session, and recognition is about 1.2s of CPU per
# frame whatever the worker count -- more processes would multiply the memory without
# making a single check any faster.
#
# The timeout is raised from gunicorn's default 30s because the first request to hit a
# fresh worker pays for loading the model as well as answering.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "60", "wsgi:app"]
