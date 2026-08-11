import numpy as np
import pytest

from attendance import liveness
from attendance.attendance_db import get_todays_attendance

# the frame / recognises / client / login / csrf fixtures come from conftest.py


def post_frame(client, csrf, frame, mode=None):
    body = {"image": frame}
    if mode is not None:
        body["mode"] = mode
    return client.post("/recognize", json=body, headers={"X-CSRF-Token": csrf})


@pytest.fixture
def liveness_on(app):
    """Turn the gate on for this test. TestingConfig has it off by default."""
    app.config["LIVENESS_ENABLED"] = True
    return app


@pytest.fixture
def liveness_says(monkeypatch, liveness_on):
    """Force the verdict, without running the model.

    These tests are about what the route does with an answer, not about whether the
    network is any good. Model quality cannot be asserted from a generated image -- that
    needs a real camera, a real face and a real printed photo, which is what
    scripts/calibrate_liveness.py exists for.
    """
    def _verdict(accepted, label=None, difference=1.0):
        import attendance.routes.recognition as route

        resolved = label or ("real" if accepted else "spoof")
        monkeypatch.setattr(
            route, "is_live",
            lambda rgb, location, threshold: (accepted, resolved, difference),
        )

    return _verdict


# --- the crop fed to the model ----------------------------------------------------

def test_crop_is_square():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)

    crop = liveness.crop_face(frame, (100, 300, 260, 140))  # top, right, bottom, left

    assert crop.shape[0] == crop.shape[1]


# A face near an edge still gets a full square crop, because the missing part is filled
# by reflecting the image rather than by clipping or by sliding the box inside the frame.
# Clipping would change the crop's shape; sliding would move the face off centre. Both
# would show the model something it was not trained on.
@pytest.mark.parametrize(
    "location",
    [
        (0, 100, 60, 0),        # top-left corner
        (420, 640, 480, 580),   # bottom-right corner
        (0, 640, 40, 600),      # flush against the right edge
        (0, 640, 480, 0),       # the face fills the entire frame
    ],
)
def test_a_face_at_the_edge_still_produces_a_square_crop(location):
    frame = np.zeros((480, 640, 3), dtype=np.uint8)

    crop = liveness.crop_face(frame, location)

    assert crop.shape[0] == crop.shape[1]
    assert crop.shape[0] > 0


def test_an_impossible_face_box_is_rejected():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)

    with pytest.raises(ValueError):
        liveness.crop_face(frame, (100, 50, 50, 100))  # right < left, bottom < top


def test_preprocess_produces_what_the_model_asks_for():
    crop = np.zeros((200, 200, 3), dtype=np.uint8)

    tensor = liveness.preprocess(crop)

    assert tensor.shape == (3, liveness.INPUT_SIZE, liveness.INPUT_SIZE)
    assert tensor.dtype == np.float32
    assert 0.0 <= tensor.min() and tensor.max() <= 1.0


# a non-square crop must be letterboxed, not squashed -- stretching a face changes its
# proportions, which is exactly what the model is reading
def test_preprocess_letterboxes_rather_than_stretching():
    tall = np.zeros((200, 100, 3), dtype=np.uint8)

    tensor = liveness.preprocess(tall)

    assert tensor.shape == (3, liveness.INPUT_SIZE, liveness.INPUT_SIZE)


# --- the model itself -------------------------------------------------------------

# THE test. A previous candidate model returned the same answer for a face, a black
# square and random noise -- it had stopped responding to its input at all, while looking
# perfectly healthy from the outside. Any model that cannot tell these apart is useless
# as a gate, and this catches that in one second rather than in production.
def test_the_model_actually_responds_to_its_input():
    rng = np.random.default_rng(0)
    box = (50, 150, 150, 50)

    black = liveness.score(np.zeros((200, 200, 3), dtype=np.uint8), box)
    white = liveness.score(np.full((200, 200, 3), 255, dtype=np.uint8), box)
    noise = liveness.score(rng.integers(0, 256, (200, 200, 3), dtype=np.uint8), box)

    scores = [black, white, noise]
    assert len(set(round(s, 3) for s in scores)) > 1, (
        f"the model returned effectively the same score for every input: {scores}. "
        "That means it is not reading its input, and the gate would be meaningless."
    )


def test_the_model_file_matches_its_checksum():
    import hashlib

    digest = hashlib.sha256(liveness.MODEL_PATH.read_bytes()).hexdigest()

    assert digest == liveness.MODEL_SHA256


def test_a_missing_model_file_is_reported_not_ignored(monkeypatch, tmp_path):
    monkeypatch.setattr(liveness, "MODEL_PATH", tmp_path / "not-here.onnx")
    monkeypatch.setattr(liveness, "_session", None)

    with pytest.raises(liveness.LivenessUnavailable):
        liveness.get_session()


# a swapped or truncated model must not be loaded silently -- the gate would then be
# doing something nobody chose
def test_a_tampered_model_file_is_refused(monkeypatch, tmp_path):
    impostor = tmp_path / "antispoof.onnx"
    impostor.write_bytes(b"not really a model")
    monkeypatch.setattr(liveness, "MODEL_PATH", impostor)
    monkeypatch.setattr(liveness, "_session", None)

    with pytest.raises(liveness.LivenessUnavailable, match="checksum"):
        liveness.get_session()


# --- the accept/reject rule -------------------------------------------------------

@pytest.mark.parametrize(
    "difference, threshold, expected",
    [
        (8.0, 0.0, True),     # confidently real
        (0.1, 0.0, True),     # barely real, but the model leans that way
        (0.0, 0.0, True),     # exactly on the line counts as real
        (-0.1, 0.0, False),   # barely spoof
        (-4.2, 0.0, False),   # confidently spoof
        (2.0, 5.0, False),    # real, but not confidently enough for a strict threshold
        (-1.0, -3.0, True),   # a lenient threshold accepts a weakly-spoof score
    ],
)
def test_the_threshold_decides(monkeypatch, difference, threshold, expected):
    monkeypatch.setattr(liveness, "score", lambda rgb, location: difference)

    accepted, label, returned = liveness.is_live(None, (0, 1, 1, 0), threshold)

    assert accepted is expected
    assert label == ("real" if expected else "spoof")
    assert returned == difference


# --- through the endpoint ---------------------------------------------------------

def test_a_spoof_is_refused_and_records_nothing(
    client, login, csrf, frame, recognises, liveness_says
):
    login()
    recognises("Alice")
    liveness_says(accepted=False, difference=-4.2)

    data = post_frame(client, csrf, frame).get_json()

    assert data["status"] == "spoof"
    assert data["marked"] is False
    assert get_todays_attendance() == []


# the whole point of the ordering: a photo is rejected before it is ever identified, so
# the response cannot leak whose photo was held up
def test_a_refused_spoof_does_not_name_anyone(
    client, login, csrf, frame, recognises, liveness_says
):
    login()
    recognises("Alice")
    liveness_says(accepted=False)

    body = post_frame(client, csrf, frame).get_data(as_text=True)

    assert "Alice" not in body


def test_a_real_face_passes_through_to_recognition(
    client, login, csrf, frame, recognises, liveness_says
):
    login()
    recognises("Alice")
    liveness_says(accepted=True)

    assert post_frame(client, csrf, frame).get_json()["status"] == "checked_in"


# an anti-spoofing check that waves everyone through when it breaks is worse than not
# having one, because it still looks like it is working
def test_a_broken_model_fails_closed(
    client, login, csrf, frame, recognises, monkeypatch, liveness_on
):
    import attendance.routes.recognition as route

    login()
    recognises("Alice")
    monkeypatch.setattr(
        route, "is_live",
        lambda *a, **k: (_ for _ in ()).throw(liveness.LivenessUnavailable("gone")),
    )

    data = post_frame(client, csrf, frame).get_json()

    assert data["status"] == "liveness_error"
    assert get_todays_attendance() == []


def test_the_check_can_be_turned_off(client, login, csrf, frame, recognises, app, monkeypatch):
    import attendance.routes.recognition as route

    app.config["LIVENESS_ENABLED"] = False
    login()
    recognises("Alice")

    # if the gate ran despite the setting, this would fail the test rather than pass it
    monkeypatch.setattr(
        route, "is_live",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("liveness ran while disabled")),
    )

    assert post_frame(client, csrf, frame).get_json()["status"] == "checked_in"


# It ships OFF, which is unusual for a security control and is a deliberate decision:
# its false-reject rate on any given camera is unknown until measured, and refusing real
# people is worse than not checking. scripts/calibrate_liveness.py measures it.
def test_liveness_ships_disabled_pending_calibration():
    from attendance.config import Config

    assert Config.LIVENESS_ENABLED is False
