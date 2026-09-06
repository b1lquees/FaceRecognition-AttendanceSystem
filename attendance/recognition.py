"""Turning a detected face into a name.

The encoding cache is opened lazily, on first use, and reload_known_encodings() re-reads
it. Opening it at import time as a module-level side effect would be less code and costs
two things worth more than that: importing this module at all would read a file from disk,
and the set of known faces would be frozen for the life of the process, so enrolling
someone new would mean restarting the server.
"""

import os
from pathlib import Path

import face_recognition
import numpy as np

from .config import env_path

# recognition.py lives in attendance/, so the project root is one level up
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# .npz rather than pickle. Unpickling runs arbitrary code by design, which would make this
# cache effectively an executable: anything able to write the file could run code as the
# server. Enrolment from the browser means the web application writes this file in response
# to a request, so a format that executes on load is the wrong shape for it entirely.
#
# Read once, at import, rather than on every call the way get_db_path() does. The
# difference is who overrides it: the database path is redirected by tests that set the
# variable after this package has been imported, while this one is redirected either by a
# deployment, which sets it before the process starts, or by the test suite's `storage`
# fixture, which replaces this attribute directly.
ENCODINGS_FILE = env_path("ENCODINGS_FILE", PROJECT_ROOT / "encodings.npz")

# stored as two parallel arrays rather than one dictionary: npz keys become entry names
# inside a zip, and person names are user input, so using them as keys would mean
# sanitising names to be filesystem-safe. Two flat arrays sidestep that completely.
NAMES_KEY = "names"
ENCODINGS_KEY = "encodings"

ENCODING_LENGTH = 128  # dlib's face encoder always produces a 128-d vector

# the threshold that decides whether a detected face counts as a match. if the smallest
# distance to a known face is at or below this, it is that person; above it, "Unknown".
#
# lower is stricter: fewer false matches, but more failures to recognise someone whose
# lighting or appearance has changed. higher (0.7+) is more forgiving and correspondingly
# more likely to confuse two different people.
#
# 0.5 rather than the 0.6 this started at, and than the 0.6 dlib suggests as a general
# default. The failure that matters here is marking the wrong person present, which is a
# false attendance record that nobody looking at the register would ever spot; a face that
# is not recognised is obvious, immediate, and fixed by standing there another second and
# a half. Two similar-looking people confusing the matcher is the specific thing this
# guards against.
#
# The value is a policy rather than a property of the data: each row keeps the distance it
# was accepted at, so tightening the cutoff leaves history containing distances the current
# rule would reject. Those rows show as borderline in the match column and are not wrong --
# they were accepted under the rule in force when they were recorded.
TOLERANCE = 0.5

# Face *detection* is the expensive step and it does not need full resolution, so frames
# are shrunk before being searched. At 0.5 the detector looks at a quarter of the pixels.
#
# 0.5 rather than the 0.25 used by the desktop viewer: the browser sends 640x480, and a
# quarter of that is 160x120, where a face at arm's length is small enough that the HOG
# detector starts missing it. The desktop script gets away with 0.25 because it reads the
# camera at its native, larger resolution.
#
# Encoding is done at full resolution regardless -- see routes/recognition.py. The
# detector only needs to find roughly where a face is; the encoder needs the detail.
DETECTION_SCALE = 0.5

_known_encodings = None  # populated on first use by get_known_encodings()
_cache_stamp = None      # identity of the file those encodings were read from


def load_known_encodings(path=None):
    """Read the encoding cache from disk. Returns {name: [encoding, ...]}."""
    path = Path(path) if path else ENCODINGS_FILE
    if not path.exists():
        # the cache is gitignored (it holds real face data) so it will not exist on CI or
        # in a fresh clone. an empty dict keeps everything importable and identify_face
        # testable -- everyone simply comes back as "Unknown".
        print(f"{path} not found - no known faces loaded (expected in CI/testing)")
        return {}

    # allow_pickle=False is the entire point of the format change. numpy will happily
    # unpickle object arrays otherwise, which would put back exactly the code-execution
    # risk this moved away from.
    with np.load(path, allow_pickle=False) as data:
        names = data[NAMES_KEY]
        vectors = data[ENCODINGS_KEY]

    grouped = {}
    for name, vector in zip(names, vectors):
        grouped.setdefault(str(name), []).append(vector)

    print(f"Loaded {len(grouped)} known people: {list(grouped)}")
    return grouped


def save_known_encodings(known_encodings, path=None):
    """Write the cache, flattening {name: [encoding, ...]} into two parallel arrays."""
    path = Path(path) if path else ENCODINGS_FILE

    names = []
    vectors = []
    for name, encodings in known_encodings.items():
        for encoding in encodings:
            names.append(name)
            vectors.append(encoding)

    # np.array([]) of an empty list has the wrong shape to be stacked back later, so an
    # empty cache needs its dimensions stated explicitly
    stacked = (
        np.array(vectors, dtype=np.float64)
        if vectors
        else np.zeros((0, ENCODING_LENGTH), dtype=np.float64)
    )

    path.parent.mkdir(parents=True, exist_ok=True)

    # written to a neighbouring temp file and then moved into place. os.replace is atomic
    # on the same filesystem, so a crash or a concurrent read never sees a half-written
    # cache -- which matters now that a web request can trigger this while the camera is
    # reading the same file.
    temp_path = path.with_suffix(path.suffix + ".tmp")
    np.savez_compressed(
        temp_path, **{NAMES_KEY: np.array(names, dtype=str), ENCODINGS_KEY: stacked}
    )
    # savez_compressed appends .npz if the name lacks it, so ask for the file it wrote
    written = temp_path if temp_path.exists() else temp_path.with_suffix(temp_path.suffix + ".npz")
    os.replace(written, path)
    return path


def file_stamp(path):
    """What the encoding file looks like on disk right now, or None if it is not there.

    Modification time and size together rather than the time alone. Filesystems differ in
    how finely they record mtime, and the write this has to notice is os.replace() landing
    a file that was created moments earlier -- two of those inside one tick of a coarse
    clock would otherwise be indistinguishable. The size is free and breaks that tie.
    """
    try:
        info = Path(path).stat()
    except OSError:
        return None  # missing, or unreadable: either way there is nothing to have cached
    return (info.st_mtime_ns, info.st_size)


def get_known_encodings():
    """The loaded encodings, re-read whenever the file on disk has changed underneath.

    The re-read is the point, and it is about running more than one worker. This cache is
    per process and the deployment runs two of them (see the Dockerfile), so an enrolment
    only writes through whichever worker served that request. Without the re-read the
    other worker holds its own set until a restart, and because frames arrive every 1.5s
    and are spread across both, a newly enrolled person would be recognised in roughly
    half of them -- which reads as poor recognition rather than a stale cache, and so is
    the kind of fault nobody reports usefully. Removal is the same problem pointing
    somewhere worse: the entire purpose of un-enrolling somebody is to stop recognising
    them, and a worker with a stale cache carries on doing it.

    Checking every call rather than on a timer because a stat() is microseconds against
    the ~1.2s recognising a frame costs. At that ratio it is free, and it needs no
    invalidation protocol between processes -- the file is the shared state, so the file
    is what gets asked.
    """
    global _known_encodings, _cache_stamp

    stamp = file_stamp(ENCODINGS_FILE)
    if _known_encodings is None or stamp != _cache_stamp:
        _known_encodings = load_known_encodings()
        _cache_stamp = stamp
    return _known_encodings


def reload_known_encodings():
    """Force a re-read, e.g. immediately after someone has just been enrolled.

    get_known_encodings() notices the file changing on its own, so this is not what makes
    an enrolment visible -- it makes it visible in this process immediately rather than at
    the next stat, and it is the entry point the command-line scripts call. Clearing the
    stamp as well as the encodings matters: leaving a stale stamp behind would make the
    very next read decide the file had changed and load it a second time.
    """
    global _known_encodings, _cache_stamp
    _known_encodings = None
    _cache_stamp = None
    return get_known_encodings()


def identify_face(unknown_encoding, known_encodings=None, tolerance=TOLERANCE):
    """Find the closest known face to unknown_encoding.

    Returns (name, distance), where name is "Unknown" if nothing is close enough.
    known_encodings defaults to the loaded cache; tests pass their own dictionary.
    """
    if known_encodings is None:
        known_encodings = get_known_encodings()

    best_name = "Unknown"
    best_distance = None

    for name, encodings_list in known_encodings.items():  # .items() gives key and value
        # compares this one face against every stored photo of that person and returns
        # a list of distances, e.g. [0.42, 0.31, 0.38]
        distances = face_recognition.face_distance(encodings_list, unknown_encoding)
        min_distance = np.min(distances)  # their best-matching photo

        if best_distance is None or min_distance < best_distance:
            best_distance = min_distance
            best_name = name

    if best_distance is None or best_distance > tolerance:
        return "Unknown", best_distance

    return best_name, best_distance
