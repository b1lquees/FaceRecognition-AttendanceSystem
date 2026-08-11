"""Deciding whether a detected face belongs to a live person or a photograph.

Recognition alone cannot tell the difference: a printed photo of an enrolled person
produces the same 128-d encoding the real person does, so it checks them in. This module
is the gate that stops that.

The model is from facenox/face-antispoof-onnx (Apache 2.0, licence text alongside the
weights in models/). It takes a 128x128 RGB crop of a face and returns two logits,
[real, spoof]; the decision is the difference between them against a threshold.

Preprocessing here is a deliberate port of that project's own inference code, not a
reimplementation from a description. That distinction matters: an earlier attempt with a
different model used preprocessing taken from a model card and produced a classifier that
returned the same answer for a face, a black square and random noise. The lesson is that
"documented preprocessing" is a hypothesis until the model's outputs actually vary.

What this does NOT do: stop a good video replay on a decent screen. It raises the bar a
long way over "a printed photo works". It does not eliminate the attack.
"""

from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

MODEL_PATH = Path(__file__).resolve().parent / "models" / "antispoof.onnx"

# pinned so a swapped or truncated file is caught rather than silently changing what the
# gate does. checked on load, not on every call.
MODEL_SHA256 = "af2381b88f38769222ed93379e12444e2a50814575de1c46170de570c55a42b6"

INPUT_SIZE = 128

# the face box is squared off and enlarged by this before being fed in. not a free
# parameter: it is what the model was trained with, and changing it degrades accuracy
# without ever looking broken.
BBOX_EXPANSION = 1.5

_session = None


class LivenessUnavailable(RuntimeError):
    """The model could not be loaded, so no judgement can be made."""


def get_session():
    """The ONNX session, created and verified on first use."""
    global _session
    if _session is None:
        if not MODEL_PATH.exists():
            raise LivenessUnavailable(
                f"Liveness model not found at {MODEL_PATH}. It ships with the repository; "
                "if it is missing the checkout is incomplete."
            )
        import hashlib

        digest = hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest()
        if digest != MODEL_SHA256:
            raise LivenessUnavailable(
                f"Liveness model at {MODEL_PATH} does not match the expected checksum. "
                f"Expected {MODEL_SHA256}, got {digest}."
            )
        _session = ort.InferenceSession(
            str(MODEL_PATH), providers=["CPUExecutionProvider"]
        )
    return _session


def crop_face(rgb_frame, face_location, expansion=BBOX_EXPANSION):
    """Square crop around the face, enlarged, with edges reflected rather than clipped.

    face_location is face_recognition's (top, right, bottom, left).

    Reflecting at the edges rather than sliding the box back inside the frame is what
    lets this work on a close-up portrait. The face is kept centred in the crop, which is
    how the model saw faces during training; sliding the box would move the face off
    centre, and clipping would change the crop's shape.
    """
    top, right, bottom, left = face_location
    height, width = rgb_frame.shape[:2]

    box_w = right - left
    box_h = bottom - top
    if box_w <= 0 or box_h <= 0:
        raise ValueError(f"invalid face box: {face_location}")

    # square it off on the longer side, so the aspect ratio never distorts
    side = max(box_w, box_h)
    centre_x = left + box_w / 2
    centre_y = top + box_h / 2

    size = int(side * expansion)
    x = int(centre_x - size / 2)
    y = int(centre_y - size / 2)

    # the part of the wanted crop that actually exists in the frame
    x1, y1 = max(0, x), max(0, y)
    x2, y2 = min(width, x + size), min(height, y + size)

    # and how much is missing off each edge, to be filled by reflection
    pad_top = max(0, -y)
    pad_left = max(0, -x)
    pad_bottom = max(0, (y + size) - height)
    pad_right = max(0, (x + size) - width)

    inside = rgb_frame[y1:y2, x1:x2, :]
    return cv2.copyMakeBorder(
        inside, pad_top, pad_bottom, pad_left, pad_right, cv2.BORDER_REFLECT_101
    )


def preprocess(crop, size=INPUT_SIZE):
    """Letterbox to size x size, scale to [0,1], and reorder to CHW."""
    old_h, old_w = crop.shape[:2]
    ratio = float(size) / max(old_h, old_w)
    new_h, new_w = int(old_h * ratio), int(old_w * ratio)

    # AREA when shrinking and LANCZOS when enlarging: each is the better choice for its
    # direction, and resampling artefacts are exactly the kind of texture this model reads
    interpolation = cv2.INTER_LANCZOS4 if ratio > 1.0 else cv2.INTER_AREA
    resized = cv2.resize(crop, (new_w, new_h), interpolation=interpolation)

    delta_w = size - new_w
    delta_h = size - new_h
    top, bottom = delta_h // 2, delta_h - delta_h // 2
    left, right = delta_w // 2, delta_w - delta_w // 2
    letterboxed = cv2.copyMakeBorder(
        resized, top, bottom, left, right, cv2.BORDER_REFLECT_101
    )

    return letterboxed.transpose(2, 0, 1).astype(np.float32) / 255.0


def score(rgb_frame, face_location):
    """Return the real-minus-spoof logit difference for one face.

    Positive leans real, negative leans spoof, and the magnitude is how strongly.

    rgb_frame must be RGB. This model was trained on RGB, unlike the rest of the OpenCV
    path in this project which is BGR; passing the wrong one swaps two channels and gives
    quietly wrong answers rather than an error.
    """
    tensor = np.expand_dims(preprocess(crop_face(rgb_frame, face_location)), axis=0)
    session = get_session()
    logits = session.run([], {session.get_inputs()[0].name: tensor})[0][0]
    return float(logits[0]) - float(logits[1])


def is_live(rgb_frame, face_location, threshold):
    """Whether this face should be accepted as a real person.

    Returns (accepted, label, logit_difference).

    threshold is on the logit difference, so 0.0 is "whichever the model leans towards".
    Raising it demands more confidence before accepting a face, which rejects more
    spoofs and also more real people. There is no universally right value -- it depends
    on the camera and the lighting, which is what scripts/calibrate_liveness.py is for.
    """
    difference = score(rgb_frame, face_location)
    accepted = difference >= threshold
    return accepted, ("real" if accepted else "spoof"), difference
