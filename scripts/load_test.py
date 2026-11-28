"""How long does a whole class take to check in at once, on this computer?

    python scripts/load_test.py                    # 40 students, a photo from known_faces/
    python scripts/load_test.py --students 60 --photo me.jpg

Starts its own copy of the app (the same server settings as start.bat) on a throwaway
database, so the real register, accounts and enrolled faces are never touched. It enrols
one face, makes that many student accounts linked to it, starts a class, and then has
every student do exactly what a phone does -- send the class code, then a camera frame --
all at the same moment. Each student comes from a different pretend address, like real
phones through the tunnel. It reports how long everyone took.
"""

import argparse
import base64
import http.cookiejar
import json
import logging
import os
import re
import statistics
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
PORT = 5097
BASE = f"http://127.0.0.1:{PORT}"
PASSWORD = "load-test-password"


def phone_frame(photo):
    """The photo as a phone sends it: a 640x480 JPEG, as a data URL."""
    image = cv2.imread(str(photo))
    scale = min(640 / image.shape[1], 480 / image.shape[0])
    image = cv2.resize(image, None, fx=scale, fy=scale)
    canvas = np.full((480, 640, 3), 128, np.uint8)
    y, x = (480 - image.shape[0]) // 2, (640 - image.shape[1]) // 2
    canvas[y:y + image.shape[0], x:x + image.shape[1]] = image
    ok, jpeg = cv2.imencode(".jpg", canvas, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return "data:image/jpeg;base64," + base64.b64encode(jpeg.tobytes()).decode()


class Student:
    """One phone: its own cookies and its own address."""

    def __init__(self, number):
        self.username = f"student{number:03d}"
        self.address = f"10.0.{number // 250}.{number % 250 + 1}"
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.csrf = None

    def request(self, path, data=None, json_body=None):
        headers = {"X-Forwarded-For": self.address}
        if json_body is not None:
            data = json.dumps(json_body).encode()
            headers.update({"Content-Type": "application/json", "X-CSRF-Token": self.csrf})
        elif data is not None:
            data = urllib.parse.urlencode(data).encode()
        req = urllib.request.Request(BASE + path, data=data, headers=headers)
        try:
            with self.opener.open(req, timeout=120) as response:
                return response.read().decode()
        except urllib.error.HTTPError as error:  # 400s carry a JSON answer too
            return error.read().decode()

    def sign_in(self):
        page = self.request("/login")
        token = re.search(r'name="_csrf_token" value="([^"]+)"', page).group(1)
        self.request("/login", {"username": self.username, "password": PASSWORD,
                                "_csrf_token": token})
        # the session is new after signing in, so its token is read from the check-in page
        self.csrf = re.search(r'CSRF_TOKEN = "([^"]+)"', self.request("/checkin")).group(1)

    def check_in(self, class_id, frame, current_code):
        """Code, then face, like the phone page. Returns (status, seconds)."""
        start = time.perf_counter()
        for _ in range(2):  # a second try if the code changed between reading and sending
            answer = json.loads(self.request(
                "/checkin/code", json_body={"code": f"FA:{class_id}:{current_code()}"}))
            if answer.get("status") == "ok":
                break
        else:
            return answer.get("status"), time.perf_counter() - start
        answer = json.loads(self.request("/recognize", json_body={"image": frame}))
        return answer.get("status"), time.perf_counter() - start


def default_photo():
    folder = Path(os.environ.get("KNOWN_FACES_DIR", "").strip() or ROOT / "known_faces")
    photos = sorted(p for p in folder.glob("*/*") if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    if not photos:
        sys.exit(f"No photos in {folder}: pass one with --photo")
    return photos[0]


def main():
    parser = argparse.ArgumentParser(description="Time a whole class checking in at once.")
    parser.add_argument("--students", type=int, default=40)
    parser.add_argument("--photo", type=Path, help="one face; default: from known_faces/")
    args = parser.parse_args()
    photo = args.photo or default_photo()

    # a throwaway copy of everything the app writes, set before the app is imported
    temp = Path(tempfile.mkdtemp(prefix="load_test_"))
    os.environ.update(ATTENDANCE_DB=str(temp / "attendance.db"),
                      KNOWN_FACES_DIR=str(temp / "known_faces"),
                      ENCODINGS_FILE=str(temp / "encodings.npz"),
                      KIOSK_MODE="0", FLASK_SECRET_KEY="load-test-key",
                      LOG_LEVEL="WARNING")  # no audit line for each of the logins
    logging.getLogger("waitress.queue").setLevel(logging.ERROR)  # nor "queue depth" notes
    os.environ.pop("APP_ENV", None)  # plain http here, so not the https-only production cookie
    sys.path.insert(0, str(ROOT))

    from waitress import serve

    from attendance import db
    from attendance.attendance_db import get_student_id
    from attendance.auth_db import create_user, link_user_to_student, list_approved_users
    from attendance.enrolment import enrol
    from attendance.sessions_db import current_code, get_session, start_session
    from wsgi import app

    app.config["DEBUG"] = False
    db.create_schema()
    print(f"Enrolling {photo.name} and making {args.students} student accounts...")
    enrol("Load Test", [(photo.name, photo.read_bytes())])
    for number in range(args.students):
        create_user(f"student{number:03d}", PASSWORD)
    for user_id, *_ in list_approved_users():
        link_user_to_student(user_id, get_student_id("Load Test"))
    class_id = start_session("Load test", minutes=30)
    code = lambda: current_code(get_session(class_id))  # noqa: E731

    # the same server settings as start.bat, on its own port
    threading.Thread(target=serve, daemon=True, kwargs=dict(
        app=app, listen=f"127.0.0.1:{PORT}", threads=8, trusted_proxy="127.0.0.1",
        trusted_proxy_count=1, trusted_proxy_headers="x-forwarded-for x-forwarded-proto",
        log_untrusted_proxy_headers=False, _quiet=True)).start()
    time.sleep(2)

    students = [Student(n) for n in range(args.students)]
    for student in students:
        student.sign_in()
    frame = phone_frame(photo)

    # one student alone first, for comparison
    status, alone = students[0].check_in(class_id, frame, code)
    print(f"One student alone: {status} in {alone:.2f} s")

    results = [None] * len(students)
    go = threading.Barrier(len(students) - 1)

    def run(i):
        go.wait()  # everyone starts at the same moment
        results[i] = students[i].check_in(class_id, frame, code)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(1, len(students))]
    start = time.perf_counter()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    total = time.perf_counter() - start

    timings = [seconds for _, seconds in results[1:]]
    # every account is linked to the same face, so after the first student the rest are
    # "already checked in": recognised and matched all the same, which is what is timed
    recognised = sum(status in ("checked_in", "already_in") for status, _ in results[1:])
    others = sorted({status for status, _ in results[1:]} - {"checked_in", "already_in"})
    print(f"\n{len(timings)} students at the same moment, on {os.cpu_count()} CPU cores:")
    print(f"  recognised and matched:  {recognised} of {len(timings)}"
          + (f"  (other answers: {', '.join(others)})" if others else ""))
    print(f"  everyone done after:     {total:.1f} s")
    print(f"  typical student waited:  {statistics.median(timings):.1f} s")
    print(f"  slowest student waited:  {max(timings):.1f} s")
    if max(timings) > 12:
        print("  (close to the 15-second limit between scanning the code and the face check:\n"
              "   with this many at once, some phones may be asked to scan again)")


if __name__ == "__main__":
    main()
