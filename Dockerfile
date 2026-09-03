FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1
# downloads the latest package information from debian repositories, so that the next install gets the latest versions of packages
RUN apt-get update \ 
# downloads two system packages libgl1 and libglib2.0-0, which are required by OpenCV to run properly.
#The --no-install-recommends flag tells apt-get to only install the specified packages and not any additional recommended packages, which helps keep the image size smaller.
# apt-get update downloads package metadata once installation is finished you dont need those package lists anymore so removing them reduces image size
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
# establishes the applications working directory 
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
# any source code change invalidates the layer containing the dependency installation if u only modify a code 
# docker caches layers, so if you change the source code, it will invalidate the layer containing the dependency installation and rebuild it.
#This is inefficient because it means that every time you change the source code, you have to reinstall all the dependencies, which can take a long time. By copying the requirements.txt file first and installing the dependencies before copying the source code, you can take advantage of Docker's caching mechanism and avoid unnecessary reinstallation of dependencies.
# docker layer caching
# dockerignore is used to exclude files and directories from the build context, which can help reduce the size of the image and speed up the build process. In this case, it is used to exclude the database, photos, and encoding cache from the build context, which are not needed for building the image.
COPY . .

# `import attendance` has to resolve from anywhere, not just /app -- the scripts in scripts/ are run from wherever an operator happens to be standing, and Python puts the
# script's own directory on sys.path, not the project root. A regular install rather than `-e` because there is nothing to edit in an image: the source is fixed at /app the
# moment the layer above is built, so what an editable install buys in a checkout it cannot buy here. What a real install does buy is a self-contained copy in
# site-packages -- templates, stylesheet and the anti-spoofing weights included, which is what package-data in pyproject.toml is for -- rather than a .pth file pointing back at
# /app. --no-deps because the line above installed everything; pyproject.toml declares the package, not its dependencies.
# The egg-info is setuptools' build scratch, written into the source tree by either kind of install. Nothing reads it -- the installed metadata lives in site-packages -- and 
# leaving it behind puts a root-owned directory in a tree the runtime user cannot write.
RUN pip install --no-cache-dir --no-deps .     && rm -rf attendance.egg-info

# Everything the application writes lives under /data and nothing else does. The image
# deliberately contains no data at all -- .dockerignore keeps the database, the photos
# and the encoding cache out of the build context -- so a container starts empty and
# writes into whatever is mounted here. Without a mount that is the container's own
# writable layer, and `docker rm` takes the attendance record with it. The README's run
# command mounts a named volume; use it.
#
# /data is also the only writable path in the container: /app belongs to root and the
# application does not run as root. Anything else that needs a file written to it --
# LOG_FILE, most obviously, which is unset by default and logs to stderr instead -- has
# to point somewhere under /data too.
ENV ATTENDANCE_DB=/data/attendance.db \
    KNOWN_FACES_DIR=/data/known_faces \
    ENCODINGS_FILE=/data/encodings.npz \
    APP_ENV=production

# Root in a container is root on the host the moment anything escapes it, and nothing
# here needs root: the application writes to /data and reads everything else. The uid is
# fixed rather than left to the system so that files on a mounted host directory have a
# predictable owner.
#
# That fixed uid owns /data in the image, which a named or anonymous volume inherits when
# Docker seeds it. A *bind* mount inherits nothing -- it arrives owned by whatever owns
# the host directory -- so `-v ./data:/data` fails on the first write unless the host
# directory is chowned to 10001. Named volume unless you have a reason.
RUN useradd --create-home --uid 10001 attendance \
    && mkdir -p /data \
    && chown -R attendance:attendance /data

# declares the intent and gives an anonymous volume to anyone who forgets to mount one.
# It is not a substitute for mounting: an anonymous volume survives `docker rm` but is
# unnamed and easy to lose track of.
VOLUME ["/data"]

USER attendance

EXPOSE 8000

# Answers a question the process table cannot: whether this container has a database to
# serve from. A container brought up without anyone running init_db.py is up, responsive,
# and 500s on every page. docker-compose.yml sets the same check with its own timings --
# this one is for `docker run`, which otherwise reports "Up 6 days" and means nothing by
# it. The start period covers the first request to a fresh worker, which loads the face
# encodings and the anti-spoofing session as well as answering.
#
# python rather than curl because the slim image ships neither curl nor wget, and adding
# one for this would be a package installed forever to run a four-line request. urlopen
# raises on 503, which exits non-zero, which is the signal.
#
# Note what this does not buy: marking a container unhealthy does NOT restart it. Compose
# acts on health in `depends_on`, and restart-on-unhealthy is a Swarm feature.
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=5)"]

# Two workers rather than the usual per-CPU count. Each one loads its own copy of the
# encodings and its own anti-spoofing session, and recognition is about 1.2s of CPU per
# frame whatever the worker count -- more processes would multiply the memory without
# making a single check any faster.
#
# The timeout is raised from gunicorn's default 30s because the first request to hit a
# fresh worker pays for loading the model as well as answering.
#
# --access-logfile - because gunicorn writes no access log at all by default, and
# werkzeug's is deliberately silenced in audit.py (it repeats the audit trail, one line
# per frame posted to /recognize). Without this a healthy container is silent, and there
# is nowhere to see that a request arrived but the response was a 404. It costs one line
# from 127.0.0.1 every 30 seconds for the healthcheck above, which gunicorn has no way to
# filter out; that is the price of seeing the other requests.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "60", \
     "--access-logfile", "-", "wsgi:app"]
