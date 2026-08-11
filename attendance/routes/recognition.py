"""The camera page and the endpoint that identifies a single frame."""

import base64
import binascii

import cv2
import face_recognition
import numpy as np
from flask import Blueprint, current_app, jsonify, render_template, request, session

from ..attendance_db import check_in, check_out, get_student_id
from ..auth_db import get_linked_student_id
from ..decorators import login_required
from ..liveness import LivenessUnavailable, is_live
from ..recognition import DETECTION_SCALE, get_known_encodings, identify_face

recognition_bp = Blueprint("recognition", __name__)


def result(status, name=None, distance=None, marked=False):
    """One shape for every answer.

    status is the field the page switches on:
        no_face        - nothing detected in the frame
        spoof          - a face, but the liveness model says it is a photo or a screen
        liveness_error - the liveness model could not run, so nothing was recorded
        unknown        - a face, but not one that is enrolled
        checked_in     - arrival recorded just now
        already_in     - recognised, but already checked in today
        checked_out    - departure recorded just now
        already_out    - already checked out today
        not_checked_in - tried to check out without having checked in
        not_linked     - personal mode, and this account is not linked to a person
        mismatch       - personal mode, and the face is not the signed-in person

    Previously the page string-matched on the name, where "No face detected" was both a
    name and a control signal. That works until someone is actually called that, and it
    left no room for outcomes that have no name to report.
    """
    return jsonify({
        "status": status,
        "name": name,
        "distance": distance if distance is None else round(float(distance), 2),
        "marked": marked,
    })


@recognition_bp.route("/camera")
@login_required
def camera():
    return render_template("camera.html")


@recognition_bp.route("/recognize", methods=["POST"])
@login_required  # this is the only route that writes to the database. without the guard,
# anyone who could reach the server could mark attendance without ever logging in.
def recognize():
    # silent=True makes get_json() return None on malformed JSON instead of raising,
    # so a bad request becomes a clean 400 rather than a 500 with a stack trace
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or "image" not in data:
        return jsonify({"error": "expected a JSON body with an 'image' field"}), 400

    image_data = data["image"]
    if not isinstance(image_data, str):
        return jsonify({"error": "'image' must be a base64 data URL string"}), 400

    # canvas.toDataURL() produces "data:image/jpeg;base64,<payload>" -- everything before
    # the comma is metadata we don't need. rpartition splits from the right, and also
    # handles a bare payload with no comma: it returns ("", "", image_data), so `encoded`
    # still ends up holding the full string.
    _, _, encoded = image_data.rpartition(",")

    try:
        image_bytes = base64.b64decode(encoded, validate=True)  # base64 text back to raw bytes
    except (binascii.Error, ValueError):
        return jsonify({"error": "'image' is not valid base64"}), 400

    nparr = np.frombuffer(image_bytes, np.uint8)  # the compressed jpeg viewed as an array of bytes
    frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)  # decompressed into a pixel matrix
    if frame is None:  # imdecode returns None rather than raising when the bytes aren't an image
        return jsonify({"error": "could not decode the image"}), 400

    # opencv stores colours as BGR while face_recognition expects RGB, so reverse the last
    # dimension. ascontiguousarray copies it into a fresh block of memory, which the
    # underlying C library (dlib) requires.
    rgb_frame = np.ascontiguousarray(frame[:, :, ::-1])

    # Detect on a shrunk copy: the detector is the slow part and does not need the detail.
    # This was running at full resolution while the desktop viewer had always downscaled,
    # so the web path was doing roughly four times the work for the same answer.
    small = cv2.resize(rgb_frame, (0, 0), fx=DETECTION_SCALE, fy=DETECTION_SCALE)
    small_locations = face_recognition.face_locations(small)  # (top, right, bottom, left) per face

    if len(small_locations) == 0:
        return result("no_face")

    # Scale the boxes back up to full-frame coordinates, then encode from the ORIGINAL
    # frame. Encoding from the shrunk copy would be faster still, but the encoder resizes
    # each face to 150x150 internally, so feeding it a shrunk face throws away detail the
    # 128-d vector depends on -- and a worse vector means worse matching.
    scale_back = 1 / DETECTION_SCALE
    face_locations = [
        tuple(int(edge * scale_back) for edge in box) for box in small_locations
    ]

    # Liveness runs BEFORE recognition, deliberately. A photograph of an enrolled person
    # produces the same encoding the real person does, so identifying first and checking
    # afterwards would mean the system knew whose photo it was looking at -- and the only
    # thing standing between that and a check-in would be the order of two if statements.
    # It takes rgb_frame: this model was trained on RGB, unlike the rest of the OpenCV
    # path here, and passing BGR swaps two channels and gives quietly wrong answers.
    if current_app.config["LIVENESS_ENABLED"]:
        try:
            accepted, label, live_score = is_live(
                rgb_frame, face_locations[0], current_app.config["LIVENESS_THRESHOLD"]
            )
        except LivenessUnavailable:
            # fail closed. an anti-spoofing check that waves everyone through when it
            # breaks is worse than not having one, because it looks like it is working.
            current_app.logger.exception("liveness model unavailable")
            return result("liveness_error")

        if not accepted:
            current_app.logger.warning(
                "liveness rejected a face: label=%s live_score=%.3f", label, live_score
            )
            return result("spoof")

    face_encodings = face_recognition.face_encodings(rgb_frame, face_locations)

    if len(face_encodings) == 0:
        return result("no_face")

    # just handle the first detected face for now
    name, distance = identify_face(face_encodings[0], get_known_encodings())

    if name == "Unknown":
        return result("unknown", distance=distance)

    # In personal mode the face in view has to belong to the account that is signed in.
    # Without this check, being logged in as anyone lets you mark anyone else present,
    # because mark_attendance() records whoever was recognised and never consults the
    # session at all. Kiosk mode wants exactly that behaviour, which is why this is a
    # setting rather than a fix.
    if not current_app.config["KIOSK_MODE"]:
        linked_student_id = get_linked_student_id(session["username"])
        if linked_student_id is None:
            return result("not_linked")
        if linked_student_id != get_student_id(name):
            # the recognised name is deliberately withheld. reporting it would tell the
            # signed-in user who was standing in front of the camera, which leaks other
            # people's presence to anyone able to point a webcam at them.
            return result("mismatch")

    # "in" unless the page explicitly asked to check out, so a malformed or missing mode
    # falls back to the safer of the two: recording an arrival that can be corrected is
    # better than recording a departure that was never intended
    if data.get("mode") == "out":
        status = check_out(name)
    else:
        status = check_in(name, distance)

    return result(
        status,
        name=name,
        distance=distance,
        marked=status in ("checked_in", "checked_out"),
    )
