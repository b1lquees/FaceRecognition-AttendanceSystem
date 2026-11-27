"""Desktop webcam viewer: the same recognition as the web app, in an OpenCV window.

Useful for testing recognition without a browser. The identification logic itself lives
in attendance/recognition.py so that this script and the web app share one implementation
rather than each having their own copy.

    python recognise_live.py    (press q to quit)
"""

import cv2
import numpy as np

from attendance import arcface
from attendance.attendance_db import check_in
from attendance.recognition import get_known_encodings, identify_face


def main():
    known_encodings = get_known_encodings()

    # 0 means the default camera. VideoCapture returns a live connection to it.
    vid_cap = cv2.VideoCapture(0)
    if not vid_cap.isOpened():
        print("Error: could not open camera")
        return

    while True:  # once per frame: capture, detect, display
        ret, frame = vid_cap.read()  # ret says whether it succeeded; frame is a numpy array
        if not ret:
            print("Error: failed to grab frame")
            break

        # opencv stores colours as BGR, ArcFace expects RGB, so reverse the last dimension.
        # ArcFace shrinks the frame for detection itself, so the full frame goes in.
        rgb = np.ascontiguousarray(frame[:, :, ::-1])

        # each face's box and 5 landmarks, then the face as 512 numbers
        for (top, right, bottom, left), landmarks in arcface.detect(rgb):
            name, distance = identify_face(arcface.encode(rgb, landmarks), known_encodings)

            if name != "Unknown":
                if check_in(name, distance) == "checked_in":  # only the first time today
                    print(f"Attendance marked for {name}")

            colour = (0, 255, 0) if name != "Unknown" else (0, 0, 255)  # green known, red unknown
            cv2.rectangle(frame, (left, top), (right, bottom), colour, 2)

            label = name if distance is None else f"{name} ({distance:.2f})"
            cv2.putText(frame, label, (left, top - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, colour, 2)

        cv2.imshow("Video", frame)  # updates the existing window rather than opening a new one

        # waitKey waits 1ms for a keypress and returns -1 if there wasn't one. the & 0xFF
        # masks the result down to a single byte so it can be compared with ord('q').
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    vid_cap.release()  # free the camera so other programs (and the webcam light) can release it
    cv2.destroyAllWindows()


# only runs when executed directly, not when something imports this module -- otherwise
# importing it would immediately try to open the webcam and hang
if __name__ == "__main__":
    main()
