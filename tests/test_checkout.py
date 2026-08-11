import pytest

from attendance.attendance_db import get_todays_attendance

# the frame and recognises fixtures come from conftest.py


def post_frame(client, csrf, frame, mode=None):
    body = {"image": frame}
    if mode is not None:
        body["mode"] = mode
    return client.post("/recognize", json=body, headers={"X-CSRF-Token": csrf})


# --- the two modes through the route ---------------------------------------------

def test_checking_in_then_out_through_the_endpoint(client, login, csrf, frame, recognises):
    login()
    recognises("Alice")

    assert post_frame(client, csrf, frame, "in").get_json()["status"] == "checked_in"
    assert post_frame(client, csrf, frame, "out").get_json()["status"] == "checked_out"

    name, date, time_in, time_out, confidence = get_todays_attendance()[0]
    assert time_in and time_out


def test_checking_out_without_checking_in(client, login, csrf, frame, recognises):
    login()
    recognises("Alice")

    data = post_frame(client, csrf, frame, "out").get_json()

    assert data["status"] == "not_checked_in"
    assert data["marked"] is False


def test_checking_out_twice(client, login, csrf, frame, recognises):
    login()
    recognises("Alice")
    post_frame(client, csrf, frame, "in")
    post_frame(client, csrf, frame, "out")

    assert post_frame(client, csrf, frame, "out").get_json()["status"] == "already_out"


# the camera posts a frame every 1.5s. holding still while checked in must not keep
# rewriting anything, and must keep saying so.
def test_repeated_check_in_frames_report_already_in(client, login, csrf, frame, recognises):
    login()
    recognises("Alice")
    post_frame(client, csrf, frame, "in")

    for _ in range(3):
        assert post_frame(client, csrf, frame, "in").get_json()["status"] == "already_in"

    assert len(get_todays_attendance()) == 1


# --- the mode field itself --------------------------------------------------------

# a missing or malformed mode has to fall back to check-in: recording an arrival that
# was not wanted is correctable, recording a departure that never happened is worse
@pytest.mark.parametrize("mode", [None, "", "nonsense", "IN", 123])
def test_anything_that_is_not_out_means_check_in(client, login, csrf, frame, recognises, mode):
    login()
    recognises("Alice")

    assert post_frame(client, csrf, frame, mode).get_json()["status"] == "checked_in"


# --- what the pages show ----------------------------------------------------------

def test_the_today_page_shows_both_times(client, login, csrf, frame, recognises):
    login()
    recognises("Alice")
    post_frame(client, csrf, frame, "in")
    post_frame(client, csrf, frame, "out")

    body = client.get("/attendance/today").get_data(as_text=True)

    assert "<th>In</th>" in body
    assert "<th>Out</th>" in body
    assert "<th>Duration</th>" in body


# someone still on site has no leaving time, and the page has to say so rather than
# leaving a blank cell that reads as missing data
def test_someone_still_here_shows_a_dash(client, login, csrf, frame, recognises):
    login()
    recognises("Alice")
    post_frame(client, csrf, frame, "in")

    body = client.get("/attendance/today").get_data(as_text=True)

    assert "—" in body


def test_the_still_here_count(client, login, csrf, frame, recognises):
    login()
    recognises("Alice")
    post_frame(client, csrf, frame, "in")
    recognises("Bob")
    post_frame(client, csrf, frame, "in")
    post_frame(client, csrf, frame, "out")  # Bob leaves

    body = client.get("/attendance/today").get_data(as_text=True)

    assert "Still here" in body
    # Alice is still on site, Bob is not
    assert body.count("—") >= 1


def test_the_csv_export_has_both_time_columns(client, login, csrf, frame, recognises):
    login(role="admin")
    recognises("Alice")
    post_frame(client, csrf, frame, "in")
    post_frame(client, csrf, frame, "out")

    body = client.get("/attendance/export").get_data(as_text=True)

    assert "Name,Date,Time In,Time Out,Confidence" in body.splitlines()[0]
    assert "Alice" in body


def test_the_camera_page_offers_both_modes(client, login):
    login()

    body = client.get("/camera").get_data(as_text=True)

    assert 'value="in"' in body
    assert 'value="out"' in body
