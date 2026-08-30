import io
import threading
import time

import cv2
import numpy as np
import pytest

from attendance import enrolment, recognition
from attendance.attendance_db import list_students
from attendance.enrolment import (
    EnrolmentError,
    encode_photo,
    encoding_write_lock,
    enrol,
    remove_person,
    spread,
    validate_name,
    variety_warning,
)
from attendance.recognition import load_known_encodings, save_known_encodings

# the storage and face_photo fixtures live in conftest.py, shared with test_errors.py


def jpeg_bytes(width=64, height=48, colour=0):
    image = np.full((height, width, 3), colour, dtype=np.uint8)
    ok, buffer = cv2.imencode(".jpg", image)
    assert ok
    return buffer.tobytes()


# --- names --------------------------------------------------------------------

@pytest.mark.parametrize(
    "name", ["Alice", "Alice Chen", "O'Brien", "Jean-Luc", "Dr. Who", "A1", "X" * 64]
)
def test_ordinary_names_are_accepted(name):
    assert validate_name(name) == name


def test_names_are_trimmed():
    assert validate_name("  Alice Chen  ") == "Alice Chen"


# this string becomes a directory name, so anything that could escape the intended folder
# has to be refused rather than quietly rewritten
@pytest.mark.parametrize(
    "name",
    [
        "", "   ",
        "..", "../etc", "..\\windows",
        "a/b", "a\\b",
        ".hidden",
        "Alice.",            # trailing dot: Windows cannot address the directory
        "X" * 65,            # too long
        "Robert'); DROP TABLE students;--",
        "emoji \U0001F600",
        "tab\there",
        "null\x00byte",
    ],
)
def test_dangerous_or_malformed_names_are_refused(name):
    with pytest.raises(EnrolmentError):
        validate_name(name)


# Windows refuses these outright, and failing at mkdir would be a baffling way to find out
@pytest.mark.parametrize("name", ["CON", "con", "PRN", "aux", "NUL", "COM1", "lpt9"])
def test_windows_reserved_names_are_refused(name):
    with pytest.raises(EnrolmentError, match="reserved"):
        validate_name(name)


# --- photo validation ----------------------------------------------------------

def test_a_file_that_is_not_an_image_is_refused():
    with pytest.raises(EnrolmentError, match="not a readable image"):
        encode_photo(b"this is not a jpeg", "notes.txt")


def test_an_empty_file_is_refused():
    with pytest.raises(EnrolmentError, match="empty"):
        encode_photo(b"", "empty.jpg")


def test_an_oversized_file_is_refused():
    too_big = b"x" * (enrolment.MAX_PHOTO_BYTES + 1)

    with pytest.raises(EnrolmentError, match="larger than"):
        encode_photo(too_big, "huge.jpg")


# a valid image with nobody in it teaches the system nothing
def test_an_image_with_no_face_is_refused():
    with pytest.raises(EnrolmentError, match="no face"):
        encode_photo(jpeg_bytes(), "blank.jpg")


def test_a_real_face_produces_an_encoding(face_photo):
    encoding = encode_photo(face_photo, "alice.jpg")

    assert encoding.shape == (recognition.ENCODING_LENGTH,)


# --- enrolling -----------------------------------------------------------------

def test_enrolling_stores_the_photo_and_the_encoding(storage, temp_db, face_photo):
    name, added, problems, _ = enrol("Alice Chen", [("a.jpg", face_photo)])

    assert (name, added, problems) == ("Alice Chen", 1, [])
    assert list(recognition.load_known_encodings()) == ["Alice Chen"]
    assert len(list((storage / "known_faces" / "Alice Chen").iterdir())) == 1


# the uploaded filename is attacker-controlled, so it must never reach the filesystem
def test_the_uploaded_filename_is_not_used_on_disk(storage, temp_db, face_photo):
    enrol("Alice", [("../../evil.jpg", face_photo)])

    written = list((storage / "known_faces" / "Alice").iterdir())
    assert len(written) == 1
    assert "evil" not in written[0].name
    assert written[0].suffix == ".jpg"


def test_enrolling_again_adds_to_the_same_person(storage, temp_db, face_photo):
    enrol("Alice", [("a.jpg", face_photo)])
    _, added, _, _ = enrol("Alice", [("b.jpg", face_photo)])

    known = recognition.load_known_encodings()
    assert added == 1
    assert list(known) == ["Alice"]
    assert len(known["Alice"]) == 2


# one bad photo out of several should not throw away the good ones, but the admin has to
# be told which one was dropped or they will assume everything worked
def test_bad_photos_are_reported_without_losing_the_good_ones(storage, temp_db, face_photo):
    name, added, problems, _ = enrol(
        "Alice",
        [("good.jpg", face_photo), ("blank.jpg", jpeg_bytes()), ("notes.txt", b"nope")],
    )

    assert added == 1
    assert len(problems) == 2
    assert any("no face" in p for p in problems)
    assert any(".jpg, .jpeg or .png" in p for p in problems)


def test_enrolling_with_no_usable_photos_fails(storage, temp_db):
    with pytest.raises(EnrolmentError, match="No usable photos"):
        enrol("Alice", [("blank.jpg", jpeg_bytes())])


def test_at_least_one_photo_is_required(storage, temp_db):
    with pytest.raises(EnrolmentError, match="At least one photo"):
        enrol("Alice", [])


def test_too_many_photos_at_once(storage, temp_db):
    photos = [(f"{i}.jpg", jpeg_bytes()) for i in range(enrolment.MAX_PHOTOS + 1)]

    with pytest.raises(EnrolmentError, match="At most"):
        enrol("Alice", photos)


# so the person shows up in the admin link dropdown immediately, rather than only after
# they have been recognised once
def test_enrolling_registers_the_student(storage, temp_db, face_photo):
    enrol("Alice Chen", [("a.jpg", face_photo)])

    assert [row[1] for row in list_students()] == ["Alice Chen"]


# the other half of what made enrolment a shell job: the running process has to see the
# new person without being restarted
def test_enrolling_refreshes_the_live_cache(storage, temp_db, face_photo):
    assert recognition.get_known_encodings() == {}

    enrol("Alice", [("a.jpg", face_photo)])

    assert "Alice" in recognition.get_known_encodings()


# --- the admin page --------------------------------------------------------------

def test_the_enrol_page_renders_for_an_admin(client, login):
    login(username="admin1", password="admin-password", role="admin")

    response = client.get("/admin/enrol")

    assert response.status_code == 200
    assert b"Enrol a person" in response.data


# enrolling a face decides who the system will let check in. if people could enrol
# themselves they could enrol themselves under somebody else's name.
def test_a_viewer_cannot_reach_the_enrol_page(client, login):
    login(role="viewer")

    assert client.get("/admin/enrol").status_code == 403


def test_an_anonymous_visitor_cannot_reach_the_enrol_page(client):
    response = client.get("/admin/enrol")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_a_viewer_cannot_post_to_enrol(client, login, csrf, storage, temp_db):
    login(role="viewer")

    response = client.post(
        "/admin/enrol",
        data={"name": "Sneaky", "_csrf_token": csrf,
              "photos": (io.BytesIO(jpeg_bytes()), "a.jpg")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 403
    assert recognition.load_known_encodings() == {}


def test_enrolling_through_the_page(client, login, csrf, storage, temp_db, face_photo):
    login(username="admin1", password="admin-password", role="admin")

    response = client.post(
        "/admin/enrol",
        data={"name": "Alice Chen", "_csrf_token": csrf,
              "photos": (io.BytesIO(face_photo), "a.jpg")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 302
    assert list(recognition.load_known_encodings()) == ["Alice Chen"]


def test_a_bad_name_is_reported_on_the_page(client, login, csrf, storage, temp_db, face_photo):
    login(username="admin1", password="admin-password", role="admin")

    response = client.post(
        "/admin/enrol",
        data={"name": "../escape", "_csrf_token": csrf,
              "photos": (io.BytesIO(face_photo), "a.jpg")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert b"Name must start with" in response.data
    assert recognition.load_known_encodings() == {}


def test_posting_without_a_csrf_token_is_refused(client, login, storage, temp_db):
    login(username="admin1", password="admin-password", role="admin")

    response = client.post(
        "/admin/enrol",
        data={"name": "Alice", "photos": (io.BytesIO(jpeg_bytes()), "a.jpg")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400


# --- enrolment quality ---------------------------------------------------------------
#
# A set of near-identical photos uploads without complaint, reports "5 photos added", and
# then fails to recognise the person the moment their lighting changes. Nothing in the
# interface used to say so, and the failure arrives days later looking like a broken
# recogniser rather than a thin enrolment.

def test_spread_of_a_single_encoding_is_zero():
    assert spread([np.zeros(128)]) == 0.0


def test_spread_is_the_widest_gap_in_the_set():
    a, b, c = np.zeros(128), np.zeros(128), np.zeros(128)
    b[0] = 0.3
    c[0] = 0.9

    # the widest pair is a-c, not either of the neighbouring ones
    assert spread([a, b, c]) == pytest.approx(0.9)


def test_one_photo_is_called_out_as_thin():
    advice = variety_warning("Alice", [np.zeros(128)])

    assert advice is not None
    assert "one photo" in advice


def test_near_identical_photos_are_called_out():
    same = np.zeros(128)
    barely_different = np.zeros(128)
    barely_different[0] = 0.02       # about what two frames a second apart look like

    advice = variety_warning("Alice", [same, barely_different])

    assert advice is not None
    assert "nearly identical" in advice


def test_a_varied_set_draws_no_comment():
    a, b = np.zeros(128), np.zeros(128)
    b[0] = 0.4                       # the same face in different light and angle

    assert variety_warning("Alice", [a, b]) is None


# it advises, it does not refuse: five near-identical photos beat none at all, and an
# admin working from the only images they have should not be blocked by a heuristic
def test_similar_photos_are_still_enrolled(storage, face_photo):
    name, added, problems, advice = enrol(
        "Alice", [("a.jpg", face_photo), ("b.jpg", face_photo)]
    )

    assert added == 2
    assert problems == []
    assert advice is not None and "nearly identical" in advice
    assert "Alice" in load_known_encodings()


# --- two writers at once --------------------------------------------------------------
#
# Both writers load the whole cache, change one person and write it back. Three steps, and
# three steps interleave: the deployment runs two gunicorn workers, so "two admins at once"
# is not the only way in -- one admin and a background reload is enough. os.replace() makes
# each write atomic, which is a different property from this one. Both writes below finish
# cleanly and produce a valid file; without the lock one of them is simply built from
# contents read before the other landed, and a person disappears.

def test_a_removal_and_an_enrolment_at_once_do_not_undo_each_other(
    temp_db, storage, monkeypatch
):
    """remove_person() itself, raced against the other worker adding somebody.

    Whichever order they land in, the answer is the same: Alice was removed and Bob was
    added, so Bob is on file and Alice is not. Without the lock both read a cache holding
    only Alice, and the loser's save is built from it -- either Bob never existed or Alice
    came back from the dead, depending on which finished last.
    """
    save_known_encodings({"Alice": [np.zeros(128)]}, recognition.ENCODINGS_FILE)

    # widen the read-modify-write window so the two genuinely overlap, rather than the
    # test passing because they happened to miss each other on a fast machine
    real_save = enrolment.save_known_encodings

    def slow_save(known, path=None):
        time.sleep(0.05)
        return real_save(known, path)

    monkeypatch.setattr(enrolment, "save_known_encodings", slow_save)

    # a barrier rather than a sleep: both threads are released at the same instant
    ready = threading.Barrier(2)

    def remover():
        ready.wait()
        remove_person("Alice")

    def enroller():
        ready.wait()
        with encoding_write_lock():
            known = enrolment.load_known_encodings()
            known.setdefault("Bob", []).append(np.zeros(128))
            enrolment.save_known_encodings(known)

    threads = [threading.Thread(target=remover), threading.Thread(target=enroller)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(load_known_encodings(recognition.ENCODINGS_FILE)) == ["Bob"]


# the lock has to actually exclude, not merely exist. If BEGIN IMMEDIATE were ever
# softened to a plain BEGIN it would take the write lock lazily, on first write -- and
# since nothing inside this block writes to the database, that would be never.
def test_the_lock_excludes_a_second_holder(temp_db, storage):
    held = threading.Event()
    second_got_in = threading.Event()

    observed = []

    def first():
        with encoding_write_lock():
            held.set()
            time.sleep(0.3)
            # Recorded rather than asserted here. An AssertionError raised inside a thread
            # does not fail the test -- it prints to stderr and the thread ends quietly --
            # so the check has to be made on the main thread once both have finished.
            observed.append(second_got_in.is_set())

    def second():
        held.wait()
        with encoding_write_lock():
            second_got_in.set()

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert observed == [False]     # the second holder was still outside, waiting
    assert second_got_in.is_set()  # and did get in afterwards, so this was a wait not a hang


# enrol() itself, not just the lock primitive it calls. A real face photo is not needed to
# exercise the part that was broken -- the read-modify-write around the encoder, which had
# no exclusion at all -- and CI has no face photos, so the encoder is stubbed out.
def test_two_enrolments_at_once_do_not_lose_one(temp_db, storage, monkeypatch):
    save_known_encodings({}, recognition.ENCODINGS_FILE)

    monkeypatch.setattr(
        enrolment,
        "encode_photo",
        lambda data, filename="": np.random.rand(recognition.ENCODING_LENGTH),
    )

    # widen the window so the two genuinely overlap rather than missing each other
    real_save = enrolment.save_known_encodings

    def slow_save(known, path=None):
        time.sleep(0.05)
        return real_save(known, path)

    monkeypatch.setattr(enrolment, "save_known_encodings", slow_save)

    failures = []
    ready = threading.Barrier(2)

    def add(name):
        ready.wait()
        try:
            enrol(name, [(f"{name}.jpg", b"pretend-jpeg-bytes")])
        except Exception as error:            # recorded, not raised: an exception in a
            failures.append(f"{name}: {error!r}")   # thread does not fail the test

    threads = [threading.Thread(target=add, args=(name,)) for name in ("Alice", "Bob")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == []
    assert sorted(load_known_encodings(recognition.ENCODINGS_FILE)) == ["Alice", "Bob"]

