"""The camera page and the endpoint that identifies a single frame."""

import base64
import binascii
from ipaddress import ip_address

import cv2
import face_recognition
import numpy as np
from flask import Blueprint, current_app, jsonify, render_template, request, session

from ..attendance_db import check_in, check_out, get_student_id
from ..audit import audit
from ..auth_db import get_linked_student_id
from ..decorators import login_required
from ..liveness import LivenessUnavailable, is_live
from ..ratelimit import client_key, rate_limit
from ..recognition import DETECTION_SCALE, get_known_encodings, identify_face

# a blueprint keeps the recognition routes in their own module rather than one flat app file
recognition_bp = Blueprint("recognition", __name__)

# Per frame, not per person: each face costs an encoding and a liveness inference, and a
# frame holding a crowd would otherwise turn one request into dozens of them every 1.5s.
# Five is well past any realistic doorway and nowhere near a denial of service.
MAX_FACES_PER_FRAME = 5

# Which outcome the status pill should show when a frame held several faces. Something
# that actually happened outranks something that did not; a refused spoof outranks a
# merely unrecognised face, because it is the one worth noticing.
STATUS_PRIORITY = [
    "checked_in", "checked_out",
    "already_in", "already_out",
    "spoof", "liveness_error",
    "not_checked_in", "mismatch", "not_linked",
    "unknown",
]


def on_site(address, networks):
    """Whether this client address falls inside one of the permitted networks.

    An address that is missing or will not parse is refused, not admitted. That is the
    entire point of a gate: "I could not tell" has to resolve to no, or the first
    malformed thing to arrive walks straight through it.

    An address of a family nobody listed is refused for the same reason, and by the same
    line of code -- membership across families is False rather than an error, so a site
    answering on both IPv4 and IPv6 has to list both. Failing closed there is right and
    will look exactly like a misconfiguration on the day it happens, which is why the
    README says so out loud.
    """
    try:
        client = ip_address(address or "")
    except ValueError:
        return False
    return any(client in network for network in networks)


def checkin_client_key():
    """What counts as one client of /recognize, which depends on the mode.

    Kiosk: the address, like everywhere else. One camera by a door is one machine at one
    address, so the two agree and there is nothing to fix.

    Personal: the signed-in account. Here the address is actively the wrong unit. Twenty
    people checking themselves in from twenty browsers leave the building through one NAT
    gateway, so the limiter sees a single client posting twenty cameras' worth of frames
    and starts refusing everybody at 60 a minute -- a limit sized for exactly one camera,
    enforced against a whole office. And "same address" is wider than "same machine",
    which is what makes this easy to hit by accident.

    Prefixed, because the value goes straight into the store key next to real addresses
    and a username that looked like one would share a counter with it.

    Be clear about what this does NOT do: it stops the LIMITER being the thing that
    breaks, and it creates no capacity. Recognition is ~1.2s of CPU in the request thread,
    so the server answers roughly 100 frames a minute in total while one browser posts 40.
    Personal mode past a couple of concurrent users runs out of machine long before it
    runs out of allowance, and the answer to that is workers, hardware, or a longer
    interval in camera.html -- never a bigger number here.
    """
    if not current_app.config["KIOSK_MODE"] and "username" in session:
        return f"user:{session['username']}"
    # falls back to the address rather than assuming a username is there: login_required
    # runs first so in practice one always is, but a limiter that raises is a limiter that
    # turns a refusal into a 500
    return client_key()


def outcome(status, name=None, distance=None, marked=False):
    """One face's result, as it appears in the `results` array."""
    return {
        "status": status,
        "name": name,
        "distance": distance if distance is None else round(float(distance), 2),
        "marked": marked,
    }


def summarise(outcomes):
    """The single status for the pill, when several faces were seen at once."""
    seen = {item["status"] for item in outcomes}
    for status in STATUS_PRIORITY:
        if status in seen:
            return status
    return "unknown"


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

    The outcome is its own field so the page never has to string-match on the name. A
    sentinel like "No face detected" in the name field would be both a name and a control
    signal: it breaks the moment someone is actually called that, and leaves nowhere to
    report an outcome that has no name attached.
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
#
# And a limit, because this is by far the most expensive thing the server does: detection
# plus encoding is around 1.2 seconds of CPU per frame, and it runs in the request thread.
# Two gunicorn workers therefore answer well under two of these a second, so a single
# signed-in account calling it in a loop can occupy the whole server and leave the camera
# by the door unable to check anybody in. Every other limit here guards a password or an
# inbox; this one guards the CPU.
#
# 60 a minute against a camera that posts one frame every 1.5 seconds -- 40 a minute --
# leaves room for a burst of retries after a hiccup while capping the damage one client
# can do. Counted per camera rather than per address -- see checkin_client_key(), which is
# the address in kiosk mode and the signed-in account in personal mode. Same caveat as the
# rest of the module either way: the count lives in one worker's memory, so it is a brake
# rather than a wall.
#
# Which means this number assumes ONE camera per counter. In kiosk mode that is still an
# assumption about the address, and two tabs or two kiosk machines behind the same NAT
# gateway come to 80 a minute between them and both start getting 429s. That is correct as
# far as it goes -- two cameras really are two cameras. It was the wrong unit in personal
# mode, where twenty people on one office gateway are twenty separate check-ins and not one
# client doing twenty times the work, which is why that mode keys on the account instead.
#
# Do not simply raise it in either mode. Two workers at ~1.2s a frame is about 100 frames a
# minute for the whole server, and one camera is already 40 of them, so the machine supports
# roughly two cameras at full rate and no more. A limit of 120 would let a single client ask
# for more than the server can deliver, at which point it has stopped guarding the CPU and
# is only decorating the code. A second camera -- or a twentieth account -- is a capacity
# question: more workers, better hardware, or a longer interval in camera.html, never a
# bigger number here.
@rate_limit(limit=60, per_seconds=60, as_json=True, key=checkin_client_key)
def recognize():
    # Before the body is looked at, let alone decoded. A refusal here should cost nothing,
    # and this is by a wide margin the cheapest check in the function.
    #
    # Deliberately independent of KIOSK_MODE. Personal mode is what makes it necessary --
    # see CHECKIN_NETWORKS in config.py -- but a kiosk deployment that wants its door
    # camera pinned to the LAN gets the same setting rather than a second one.
    networks = current_app.config["CHECKIN_NETWORKS"]
    if networks and not on_site(request.remote_addr, networks):
        # Audited rather than merely logged: a run of these is somebody checking in from
        # somewhere they are not meant to be, which is precisely the thing worth being
        # able to find afterwards. The address is not passed in because audit() already
        # records it on every line.
        audit("checkin.offsite")
        # `offsite` so the page can tell this apart from the session having expired, which
        # is the other thing that stops the loop with a 403. Both are permanent for this
        # page load; only one of them is worth explaining accurately.
        return jsonify({
            "error": "Check-in is only available on site.",
            "offsite": True,
        }), 403

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
    # At DETECTION_SCALE the frame carries a fraction of the pixels for the same answer, and
    # the desktop viewer downscales by the same factor so both paths see the same input.
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

    # A frame can hold more than one face, and until now only the first was looked at --
    # so at a shared camera the second person in shot was silently ignored, while the
    # desktop viewer handled everyone. The two paths now agree.
    #
    # Capped because the work is per face: encoding and a liveness check each cost real
    # time, and a frame containing a crowd photo would otherwise turn one request into
    # fifty inferences, every 1.5 seconds.
    #
    # What actually happens to each face is handle_one_face(), including the liveness gate
    # and why it has to run before recognition rather than after.
    outcomes = []
    for location in face_locations[:MAX_FACES_PER_FRAME]:
        # deliberately not named `outcome`: that is the function these come back from, and
        # binding it here would shadow it for the rest of this scope
        face_outcome = handle_one_face(rgb_frame, location, data.get("mode"))
        if face_outcome is not None:
            outcomes.append(face_outcome)

    if not outcomes:
        return result("no_face")

    return jsonify({"status": summarise(outcomes), "results": outcomes})


def handle_one_face(rgb_frame, location, mode):
    """Everything that happens to a single detected face. Returns a result dict."""
    # Liveness runs BEFORE recognition, deliberately. A photograph of an enrolled person
    # produces the same encoding the real person does, so identifying first and checking
    # afterwards would mean the system knew whose photo it was looking at -- and the only
    # thing standing between that and a check-in would be the order of two if statements.
    # It takes rgb_frame: this model was trained on RGB, unlike the rest of the OpenCV
    # path here, and passing BGR swaps two channels and gives quietly wrong answers.
    if current_app.config["LIVENESS_ENABLED"]:
        try:
            accepted, label, live_score = is_live(
                rgb_frame, location, current_app.config["LIVENESS_THRESHOLD"]
            )
        except LivenessUnavailable:
            # fail closed. an anti-spoofing check that waves everyone through when it
            # breaks is worse than not having one, because it looks like it is working.
            current_app.logger.exception("liveness model unavailable")
            return outcome("liveness_error")

        if not accepted:
            # audited, not just logged: a run of these is somebody standing at the camera
            # holding a photograph up, which is exactly the thing worth being able to
            # find afterwards. the score is recorded so a pattern of near-misses can be
            # told apart from a blatant attempt when tuning the threshold.
            audit("liveness.refused", label=label, score=f"{live_score:.2f}")
            return outcome("spoof")

    encodings = face_recognition.face_encodings(rgb_frame, [location])
    if not encodings:
        return None  # detected but not encodable; nothing to say about it

    name, distance = identify_face(encodings[0], get_known_encodings())

    if name == "Unknown":
        return outcome("unknown", distance=distance)

    # In personal mode the face in view has to belong to the account that is signed in.
    # Without this check, being logged in as anyone lets you mark anyone else present,
    # because check_in() records whoever was recognised and never consults the session
    # at all. Kiosk mode wants exactly that behaviour, which is why this is a setting
    # rather than a fix.
    if not current_app.config["KIOSK_MODE"]:
        linked_student_id = get_linked_student_id(session["username"])
        if linked_student_id is None:
            return outcome("not_linked")
        if linked_student_id != get_student_id(name):
            # the recognised name is deliberately withheld. reporting it would tell the
            # signed-in user who was standing in front of the camera, which leaks other
            # people's presence to anyone able to point a webcam at them.
            return outcome("mismatch")

    # "in" unless the page explicitly asked to check out, so a malformed or missing mode
    # falls back to the safer of the two: recording an arrival that can be corrected is
    # better than recording a departure that was never intended
    status = check_out(name) if mode == "out" else check_in(name, distance)

    return outcome(
        status, name=name, distance=distance,
        marked=status in ("checked_in", "checked_out"),
    )
