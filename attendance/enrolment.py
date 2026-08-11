"""Adding a person to the system from the browser.

Enrolling used to mean shell access: drop photos into known_faces/, run
build_encodings.py, restart the server. That is fine for the person who built the thing
and useless for anyone running it.

Everything here treats its input as hostile, because it now is. Before this, the only way
to get a file into known_faces/ was to already be on the machine.
"""

import re
import uuid
from pathlib import Path

import cv2
import face_recognition
import numpy as np

from .attendance_db import register_student
from .recognition import (
    load_known_encodings,
    reload_known_encodings,
    save_known_encodings,
)
from .recognition import PROJECT_ROOT

KNOWN_FACES_DIR = PROJECT_ROOT / "known_faces"

# Deliberately strict, because this string becomes a directory name. Letters, digits,
# spaces, dots, apostrophes and hyphens cover ordinary names; anything else is refused
# rather than silently rewritten, so what an admin typed is what gets stored. Requiring
# the first character to be alphanumeric is what stops "..", "  ", ".hidden" and friends.
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .'\-]{0,63}$")

# Windows refuses to create a directory with any of these names, whatever the extension.
# A person called Con is unlikely, but failing at mkdir with an OS error would be a
# baffling way to find out.
RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}

ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png"}
MAX_PHOTO_BYTES = 5 * 1024 * 1024
MAX_PHOTOS = 10

# How large the whole upload is allowed to be.
#
# This has to be derived from the two limits above rather than picked separately. The
# app-wide MAX_CONTENT_LENGTH is 8 MB, sized for a webcam frame, and enrolment invites
# ten photos of 5 MB -- so the form was promising 50 MB through an 8 MB door. Three
# ordinary phone photos were enough to hit it, and the rejection arrived as a bare 413
# with no explanation, from a page that had just said 5 MB each was fine.
#
# The extra megabyte is multipart overhead: boundaries, headers and the name field.
MAX_REQUEST_BYTES = MAX_PHOTOS * MAX_PHOTO_BYTES + 1024 * 1024


class EnrolmentError(ValueError):
    """Something about the submission was wrong, in a way worth telling the user."""


def validate_name(name):
    """Return the cleaned name, or raise EnrolmentError explaining what is wrong."""
    name = (name or "").strip()

    if not name:
        raise EnrolmentError("A name is required.")
    if not NAME_PATTERN.match(name):
        raise EnrolmentError(
            "Name must start with a letter or number and use only letters, numbers, "
            "spaces, dots, apostrophes and hyphens (up to 64 characters)."
        )
    if name.lower() in RESERVED_NAMES:
        raise EnrolmentError(f"{name!r} is a reserved name on Windows and cannot be used.")
    # a trailing dot or space is legal in the pattern but produces a directory Windows
    # cannot reliably address
    if name != name.rstrip(". "):
        raise EnrolmentError("Name cannot end with a dot or a space.")

    return name


def encode_photo(data, filename=""):
    """Turn one uploaded image into a single face encoding.

    Raises EnrolmentError if the bytes are not a decodable image, or do not contain
    exactly one face. Exactly one matters in both directions: zero means there is nothing
    to learn, and two or more means we cannot tell which face is the person being
    enrolled -- guessing would quietly teach the system the wrong face.
    """
    label = filename or "photo"

    if len(data) > MAX_PHOTO_BYTES:
        raise EnrolmentError(f"{label}: larger than {MAX_PHOTO_BYTES // (1024 * 1024)} MB.")
    if not data:
        raise EnrolmentError(f"{label}: file is empty.")

    # decoded rather than trusted by extension or content-type, both of which are just
    # claims made by whoever uploaded the file
    array = np.frombuffer(data, np.uint8)
    bgr = cv2.imdecode(array, cv2.IMREAD_COLOR)
    if bgr is None:
        raise EnrolmentError(f"{label}: not a readable image.")

    rgb = np.ascontiguousarray(bgr[:, :, ::-1])
    encodings = face_recognition.face_encodings(rgb)

    if len(encodings) == 0:
        raise EnrolmentError(f"{label}: no face found.")
    if len(encodings) > 1:
        raise EnrolmentError(f"{label}: {len(encodings)} faces found, need exactly one.")

    return encodings[0]


def enrol(name, photos):
    """Add or extend a person. photos is a list of (filename, bytes).

    Returns (name, added_count, problems). Photos that fail are reported rather than
    aborting the whole submission: one bad photo out of five should not throw away the
    four good ones, and the admin needs to know which one to replace.

    Enrolling an existing person appends to their photos rather than replacing them --
    more angles and lighting make recognition better, so this is almost always what is
    wanted.
    """
    name = validate_name(name)

    if not photos:
        raise EnrolmentError("At least one photo is required.")
    if len(photos) > MAX_PHOTOS:
        raise EnrolmentError(f"At most {MAX_PHOTOS} photos at a time.")

    accepted = []
    problems = []

    for filename, data in photos:
        suffix = Path(filename or "").suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            problems.append(f"{filename or 'photo'}: must be .jpg, .jpeg or .png.")
            continue
        try:
            accepted.append((suffix, data, encode_photo(data, filename)))
        except EnrolmentError as error:
            problems.append(str(error))

    if not accepted:
        raise EnrolmentError(
            "No usable photos. " + " ".join(problems)
        )

    person_dir = KNOWN_FACES_DIR / name
    person_dir.mkdir(parents=True, exist_ok=True)

    # the uploaded filename is never used on disk. it is attacker-controlled, and a
    # random name also means two uploads called photo.jpg cannot overwrite each other.
    for suffix, data, _ in accepted:
        (person_dir / f"{uuid.uuid4().hex}{suffix}").write_bytes(data)

    known = load_known_encodings()
    known.setdefault(name, []).extend(encoding for _, _, encoding in accepted)
    save_known_encodings(known)

    # so the person appears in the admin link dropdown straight away, rather than only
    # after they have been recognised once
    register_student(name)

    # and so the running server sees them without a restart, which was the other half of
    # what made enrolment a shell job
    reload_known_encodings()

    return name, len(accepted), problems
