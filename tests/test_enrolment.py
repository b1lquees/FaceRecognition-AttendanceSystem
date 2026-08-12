import io

import cv2
import numpy as np
import pytest

from attendance import enrolment, recognition
from attendance.attendance_db import list_students
from attendance.enrolment import EnrolmentError, encode_photo, enrol, validate_name

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
    name, added, problems = enrol("Alice Chen", [("a.jpg", face_photo)])

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
    _, added, _ = enrol("Alice", [("b.jpg", face_photo)])

    known = recognition.load_known_encodings()
    assert added == 1
    assert list(known) == ["Alice"]
    assert len(known["Alice"]) == 2


# one bad photo out of several should not throw away the good ones, but the admin has to
# be told which one was dropped or they will assume everything worked
def test_bad_photos_are_reported_without_losing_the_good_ones(storage, temp_db, face_photo):
    name, added, problems = enrol(
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
