"""Turning a detected face into a name.

recognise_live.py used to open the encoding cache at import time, as a module-level side
effect, and app.py imported the resulting dictionary directly. Two problems came from
that: importing anything at all read a file from disk, and the web app's set of known
faces was frozen for the life of the process, so enrolling someone new meant restarting
the server. Loading is now lazy, and reload_known_encodings() re-reads the file.
"""

import os
from pathlib import Path

import face_recognition
import numpy as np

# recognition.py lives in attendance/, so the project root is one level up
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# .npz, not the .pkl this used to be. Unpickling runs arbitrary code by design, so the
# cache file was effectively executable: anything able to write it could run code as the
# server. That was an acceptable risk while only a shell user could produce it. Enrolment
# from the browser means the web application now writes this file, and a format that
# executes on load is the wrong shape for that entirely.
ENCODINGS_FILE = PROJECT_ROOT / "encodings.npz"

# stored as two parallel arrays rather than one dictionary: npz keys become entry names
# inside a zip, and person names are user input, so using them as keys would mean
# sanitising names to be filesystem-safe. Two flat arrays sidestep that completely.
NAMES_KEY = "names"
ENCODINGS_KEY = "encodings"

ENCODING_LENGTH = 128  # dlib's face encoder always produces a 128-d vector

# the threshold that decides whether a detected face counts as a match. if the smallest
# distance to a known face is at or below this, it is that person; above it, "Unknown".
#
# lower (say 0.4) is stricter: fewer false matches, but more failures to recognise
# someone whose lighting or appearance has changed. higher (0.7+) is more forgiving and
# correspondingly more likely to confuse two different people.
TOLERANCE = 0.6

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


def get_known_encodings():
    """The loaded encodings, reading them from disk the first time they are asked for."""
    global _known_encodings
    if _known_encodings is None:
        _known_encodings = load_known_encodings()
    return _known_encodings


def reload_known_encodings():
    """Re-read the cache from disk, e.g. after someone has just been enrolled."""
    global _known_encodings
    _known_encodings = load_known_encodings()
    return _known_encodings


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
