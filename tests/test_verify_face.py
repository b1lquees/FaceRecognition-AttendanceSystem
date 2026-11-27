import numpy as np

from attendance.recognition import ENCODING_LENGTH, TOLERANCE, verify_face

# real distances, no stubs: a face encoding is 512 numbers of total length 1, and distance is
# 1 minus the cosine similarity of two of them. These build encodings at known distances.


def at_distance(d, axis=1):
    """An encoding exactly d away from ALICE, turned towards `axis`."""
    vector = np.zeros(ENCODING_LENGTH)
    vector[0] = 1 - d
    vector[axis] = np.sqrt(1 - (1 - d) ** 2)
    return vector


ALICE = at_distance(0)
BOB = at_distance(0.9, axis=2)
KNOWN = {"Alice": [ALICE], "Bob": [BOB]}


def test_a_close_face_is_verified():
    matched, distance = verify_face(at_distance(0.3), "Alice", KNOWN)
    assert matched
    assert round(distance, 6) == 0.3


def test_a_face_beyond_the_tolerance_is_refused():
    matched, _ = verify_face(at_distance(TOLERANCE + 0.01), "Alice", KNOWN)
    assert not matched


def test_exactly_at_the_tolerance_matches_like_identify_face_does():
    # a hair inside, because 1 - cos(...) is not exact in floating point
    matched, _ = verify_face(at_distance(TOLERANCE - 1e-9), "Alice", KNOWN)
    assert matched


def test_only_the_claimed_person_is_compared():
    # this face is Bob's exactly, but the question is "is this Alice?" -- and it is not
    matched, _ = verify_face(BOB, "Alice", KNOWN)
    assert not matched


def test_someone_with_no_photos_is_never_verified():
    assert verify_face(ALICE, "Carol", KNOWN) == (False, None)
