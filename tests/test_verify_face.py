import numpy as np

from attendance.recognition import TOLERANCE, verify_face

# real distances, no stubs: a face encoding is 128 numbers, and distance is how far apart
# two of them are. These build encodings at known distances from Alice's.

ALICE = np.zeros(128)
KNOWN = {"Alice": [ALICE], "Bob": [np.full(128, 0.2)]}


def at_distance(d):
    """An encoding exactly d away from ALICE."""
    vector = np.zeros(128)
    vector[0] = d
    return vector


def test_a_close_face_is_verified():
    matched, distance = verify_face(at_distance(0.3), "Alice", KNOWN)
    assert matched
    assert round(distance, 6) == 0.3


def test_a_face_beyond_the_tolerance_is_refused():
    matched, _ = verify_face(at_distance(TOLERANCE + 0.01), "Alice", KNOWN)
    assert not matched


def test_exactly_at_the_tolerance_matches_like_identify_face_does():
    matched, _ = verify_face(at_distance(TOLERANCE), "Alice", KNOWN)
    assert matched


def test_only_the_claimed_person_is_compared():
    # this face is Bob's exactly, but the question is "is this Alice?" -- and it is not
    matched, _ = verify_face(np.full(128, 0.2), "Alice", KNOWN)
    assert not matched


def test_someone_with_no_photos_is_never_verified():
    assert verify_face(ALICE, "Carol", KNOWN) == (False, None)
