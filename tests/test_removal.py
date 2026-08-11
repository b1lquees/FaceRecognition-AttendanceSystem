import pytest

from attendance import enrolment, recognition
from attendance.attendance_db import check_in, get_todays_attendance, list_students
from attendance.enrolment import EnrolmentError, enrol, person_directory, remove_person

# the storage and face_photo fixtures come from conftest.py


# --- removing --------------------------------------------------------------------

def test_removing_deletes_the_photos_and_the_encodings(storage, temp_db, face_photo):
    enrol("Alice", [("a.jpg", face_photo)])
    directory = storage / "known_faces" / "Alice"
    assert directory.is_dir()

    removed = remove_person("Alice")

    assert removed == 1
    assert not directory.exists()
    assert recognition.load_known_encodings() == {}


def test_the_camera_stops_recognising_them_without_a_restart(storage, temp_db, face_photo):
    enrol("Alice", [("a.jpg", face_photo)])
    assert "Alice" in recognition.get_known_encodings()

    remove_person("Alice")

    assert "Alice" not in recognition.get_known_encodings()


# THE thing this must not do. Un-enrolling means the system stops recognising somebody
# from now on. It does not mean they were never here, and deleting the days they attended
# would be falsifying a record rather than removing a face.
def test_attendance_history_survives_removal(storage, temp_db, face_photo):
    enrol("Alice", [("a.jpg", face_photo)])
    check_in("Alice", 0.3)
    assert len(get_todays_attendance()) == 1

    remove_person("Alice")

    assert len(get_todays_attendance()) == 1
    assert get_todays_attendance()[0][0] == "Alice"
    assert [row[1] for row in list_students()] == ["Alice"]


def test_removing_one_person_leaves_the_others(storage, temp_db, face_photo):
    enrol("Alice", [("a.jpg", face_photo)])
    enrol("Bob", [("b.jpg", face_photo)])

    remove_person("Alice")

    assert list(recognition.load_known_encodings()) == ["Bob"]
    assert (storage / "known_faces" / "Bob").is_dir()


def test_removing_somebody_who_is_not_enrolled(storage, temp_db):
    with pytest.raises(EnrolmentError, match="not enrolled"):
        remove_person("Nobody")


# --- the path guard --------------------------------------------------------------

# remove_person calls shutil.rmtree. validate_name already refuses separators, but this
# is checked again because a path bug here deletes a directory tree, and that is
# unrecoverable rather than merely wrong.
@pytest.mark.parametrize("name", ["..", "../etc", "a/b", "a\\b", ""])
def test_a_path_that_escapes_known_faces_is_refused(storage, name):
    with pytest.raises(EnrolmentError):
        person_directory(name)


def test_an_ordinary_name_resolves_inside_known_faces(storage):
    resolved = person_directory("Alice Chen")

    assert resolved.parent == enrolment.KNOWN_FACES_DIR.resolve()


# --- through the admin pages -----------------------------------------------------

def test_removing_asks_for_confirmation_first(client, login, storage, temp_db, face_photo):
    enrol("Alice", [("a.jpg", face_photo)])
    login(username="admin1", password="admin-password", role="admin")

    response = client.get("/admin/enrol/remove?name=Alice")

    assert response.status_code == 200
    assert b"Remove Alice?" in response.data
    # the page has to say what survives, because that is the question an admin will have
    assert b"attendance history is kept" in response.data
    # and nothing has happened yet
    assert "Alice" in recognition.load_known_encodings()


def test_the_post_actually_removes(client, login, csrf, storage, temp_db, face_photo):
    enrol("Alice", [("a.jpg", face_photo)])
    login(username="admin1", password="admin-password", role="admin")

    response = client.post(
        "/admin/enrol/remove", data={"name": "Alice", "_csrf_token": csrf}
    )

    assert response.status_code == 302
    assert recognition.load_known_encodings() == {}


def test_a_viewer_cannot_remove_anyone(client, login, csrf, storage, temp_db, face_photo):
    enrol("Alice", [("a.jpg", face_photo)])
    login(role="viewer")

    response = client.post(
        "/admin/enrol/remove", data={"name": "Alice", "_csrf_token": csrf}
    )

    assert response.status_code == 403
    assert "Alice" in recognition.load_known_encodings()


def test_removing_without_a_csrf_token_is_refused(client, login, storage, temp_db, face_photo):
    enrol("Alice", [("a.jpg", face_photo)])
    login(username="admin1", password="admin-password", role="admin")

    response = client.post("/admin/enrol/remove", data={"name": "Alice"})

    assert response.status_code == 400
    assert "Alice" in recognition.load_known_encodings()


def test_confirming_someone_who_is_not_enrolled_redirects_rather_than_erroring(
    client, login, storage, temp_db
):
    login(username="admin1", password="admin-password", role="admin")

    response = client.get("/admin/enrol/remove?name=Nobody")

    assert response.status_code == 302


def test_the_enrol_page_offers_a_remove_link(client, login, storage, temp_db, face_photo):
    enrol("Alice", [("a.jpg", face_photo)])
    login(username="admin1", password="admin-password", role="admin")

    body = client.get("/admin/enrol").get_data(as_text=True)

    assert "/admin/enrol/remove?name=Alice" in body
