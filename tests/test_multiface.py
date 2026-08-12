import numpy as np
import pytest

from attendance.attendance_db import get_todays_attendance
from attendance.pagination import paginate
from attendance.routes.recognition import MAX_FACES_PER_FRAME, summarise


def post_frame(client, csrf, frame, mode=None):
    body = {"image": frame}
    if mode is not None:
        body["mode"] = mode
    return client.post("/recognize", json=body, headers={"X-CSRF-Token": csrf})


@pytest.fixture
def sees(monkeypatch):
    """Put several named faces in one frame.

    Detection is stubbed to report N boxes and identify_face to name them in order, which
    is the only way to test a crowd without one: the generated frame contains no faces at
    all, and a real multi-person photo cannot live in a gitignored repository.
    """
    def _sees(*names):
        import attendance.routes.recognition as route

        boxes = [(0, 10 * (i + 1), 10, 10 * i) for i in range(len(names))]
        monkeypatch.setattr(route.face_recognition, "face_locations", lambda image: boxes)
        monkeypatch.setattr(
            route.face_recognition, "face_encodings",
            lambda image, locations: [np.zeros(128) for _ in locations],
        )
        remaining = list(names)
        monkeypatch.setattr(
            route, "identify_face",
            lambda encoding, known: (remaining.pop(0), 0.3) if remaining else ("Unknown", 0.9),
        )
    return _sees


# --- several people at once -------------------------------------------------------

# the bug this fixes: only the first face was ever looked at, so at a shared camera the
# second person in shot was silently ignored while the desktop viewer handled everyone
def test_everyone_in_the_frame_is_checked_in(client, login, csrf, frame, sees):
    login()
    sees("Alice", "Bob", "Priya")

    data = post_frame(client, csrf, frame).get_json()

    assert len(data["results"]) == 3
    assert {row[0] for row in get_todays_attendance()} == {"Alice", "Bob", "Priya"}


def test_each_face_gets_its_own_outcome(client, login, csrf, frame, sees):
    login()
    sees("Alice", "Bob")
    post_frame(client, csrf, frame)  # both checked in

    sees("Alice", "Bob")
    data = post_frame(client, csrf, frame).get_json()

    assert [r["status"] for r in data["results"]] == ["already_in", "already_in"]


def test_a_recognised_and_an_unrecognised_face_together(client, login, csrf, frame, sees):
    login()
    sees("Alice", "Unknown")

    data = post_frame(client, csrf, frame).get_json()

    statuses = [r["status"] for r in data["results"]]
    assert "checked_in" in statuses
    assert "unknown" in statuses
    assert [row[0] for row in get_todays_attendance()] == ["Alice"]


# each face costs an encoding and a liveness inference. a frame holding a crowd photo
# would otherwise turn one request into dozens of them, every 1.5 seconds.
def test_the_number_of_faces_per_frame_is_capped(client, login, csrf, frame, sees):
    login()
    sees(*[f"Person{i}" for i in range(MAX_FACES_PER_FRAME + 5)])

    data = post_frame(client, csrf, frame).get_json()

    assert len(data["results"]) == MAX_FACES_PER_FRAME


# --- the summary status -----------------------------------------------------------

# the pill can only show one thing, so something that actually happened outranks
# something that did not
@pytest.mark.parametrize(
    "statuses, expected",
    [
        (["unknown", "checked_in"], "checked_in"),
        (["already_in", "checked_in"], "checked_in"),
        (["unknown", "already_in"], "already_in"),
        (["unknown", "spoof"], "spoof"),           # a refused spoof is worth noticing
        (["unknown", "mismatch"], "mismatch"),
        (["unknown"], "unknown"),
    ],
)
def test_the_pill_shows_the_most_important_outcome(statuses, expected):
    assert summarise([{"status": s} for s in statuses]) == expected


def test_a_single_face_still_reports_normally(client, login, csrf, frame, sees):
    login()
    sees("Alice")

    data = post_frame(client, csrf, frame).get_json()

    assert data["status"] == "checked_in"
    assert len(data["results"]) == 1
    assert data["results"][0]["name"] == "Alice"


# --- the pagination helper --------------------------------------------------------

def test_the_first_page_starts_at_the_beginning():
    page = paginate(1, total=120, size=50)

    assert (page.number, page.offset, page.count) == (1, 0, 3)


def test_the_offset_follows_the_page():
    assert paginate(3, total=120, size=50).offset == 100


# an off-by-one here silently skips or repeats a row, which is why it is worth its own test
def test_a_partial_last_page_is_still_a_page():
    assert paginate(1, total=101, size=50).count == 3


def test_an_exact_fit_does_not_add_an_empty_page():
    assert paginate(1, total=100, size=50).count == 2


# "page 1 of 0" looks like a bug; an empty list is one empty page
def test_an_empty_list_is_one_page():
    page = paginate(1, total=0, size=50)

    assert (page.count, page.needed) == (1, False)


# a stale bookmark or a mangled url should not be an error page
@pytest.mark.parametrize("requested, expected", [(999, 3), (0, 1), (-4, 1), (None, 1)])
def test_out_of_range_pages_are_clamped(requested, expected):
    assert paginate(requested, total=120, size=50).number == expected


def test_controls_are_only_needed_when_there_is_more_than_one_page():
    assert paginate(1, total=50, size=50).needed is False
    assert paginate(1, total=51, size=50).needed is True


def test_the_edges_know_where_they_are():
    first = paginate(1, total=120, size=50)
    last = paginate(3, total=120, size=50)

    assert (first.has_previous, first.has_next) == (False, True)
    assert (last.has_previous, last.has_next) == (True, False)
