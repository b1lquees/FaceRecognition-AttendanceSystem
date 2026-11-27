import numpy as np
import pytest

from attendance import arcface, recognition
from attendance.recognition import load_known_encodings, save_known_encodings

# the models are downloaded per machine (scripts/download_arcface.py), not kept in git
needs_models = pytest.mark.skipif(
    not (arcface.DETECTOR_FILE.exists() and arcface.RECOGNISER_FILE.exists()),
    reason="ArcFace models not downloaded",
)


def test_landmarks_already_on_the_template_leave_the_face_unchanged():
    face = np.random.default_rng(0).integers(0, 256, (112, 112, 3), dtype=np.uint8)
    aligned = arcface.align(face, arcface.TEMPLATE)
    assert np.abs(aligned.astype(int) - face).max() <= 1  # only rounding differences


def test_a_face_twice_the_size_is_scaled_back_down():
    # a 224x224 image whose landmarks sit at twice the template's positions
    face = np.zeros((224, 224, 3), dtype=np.uint8)
    face[100:110, 70:80] = 255  # a white square around the left eye
    aligned = arcface.align(face, arcface.TEMPLATE * 2)
    assert aligned.shape == (112, 112, 3)
    assert aligned[50:55, 35:40].mean() > 200  # the square is now at half its position


def test_missing_models_say_how_to_get_them(monkeypatch, tmp_path):
    monkeypatch.setattr(arcface, "DETECTOR_FILE", tmp_path / "det_10g.onnx")
    with pytest.raises(arcface.ArcFaceUnavailable, match="download_arcface"):
        arcface.detect(np.zeros((480, 640, 3), dtype=np.uint8))


def test_old_dlib_encodings_are_ignored_rather_than_crashing(tmp_path):
    # an encodings.npz from before the switch holds 128 numbers per face
    path = tmp_path / "encodings.npz"
    np.savez_compressed(path, **{recognition.NAMES_KEY: np.array(["Alice"]),
                                 recognition.ENCODINGS_KEY: np.zeros((1, 128))})
    assert load_known_encodings(path) == {}

    # and a rebuilt one loads normally
    save_known_encodings({"Alice": [np.zeros(recognition.ENCODING_LENGTH)]}, path)
    assert list(load_known_encodings(path)) == ["Alice"]


@needs_models
def test_a_blank_frame_has_no_faces():
    assert arcface.detect(np.zeros((480, 640, 3), dtype=np.uint8)) == []


@needs_models
def test_an_embedding_is_512_numbers_of_length_1():
    face = np.random.default_rng(1).integers(0, 256, (112, 112, 3), dtype=np.uint8)
    vector = arcface.embed(face)
    assert vector.shape == (512,)
    assert np.linalg.norm(vector) == pytest.approx(1)
