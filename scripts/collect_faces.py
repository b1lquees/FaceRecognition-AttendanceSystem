"""Collect face crops for training the anti-spoofing model.

    python scripts/collect_faces.py real  --person bilquees --session laptop-room1
    python scripts/collect_faces.py spoof --person bilquees --session samsungA05S-screen-room1

Saves dataset/<label>/<person>__<session>__<n>.jpg. Person and session are kept in the
file name so training can split by them: the test set must hold people and sessions the
model never saw, or near-identical frames leak between train and test.

Press q to stop.
"""

import argparse  # reads the options typed after the script name (real/spoof, --person, ...)
import time  # gives the current time, used to space out the saved images
from pathlib import Path  # handles folder and file paths the same way on Windows and Linux

import cv2  # OpenCV: opens the webcam, resizes images, draws text, saves .jpg files
import face_recognition  # finds where faces are in an image
import numpy as np  # images are numpy arrays; used here to copy one into fresh memory

from attendance.liveness import crop_face  # the app's own face-cropping code, so training

# images are cut exactly the way the live system will cut them
from attendance.recognition import DETECTION_SCALE  # 0.5: the app searches for faces on a

# half-size frame because it is faster; we use the same value so both behave alike

# where the images go: a "dataset" folder in the project root (one level above scripts/)
DATASET = Path(__file__).resolve().parent.parent / "dataset"
SIZE = 224  # every crop is resized to 224x224 pixels, the input size MobileNet expects


def main():
    # 1. read the options typed on the command line
    parser = argparse.ArgumentParser()
    # first, required: what is in front of the camera -- a real face or a spoof
    parser.add_argument("label", choices=["real", "spoof"])
    # whose face it is; stored in the file name so we can split by person later
    parser.add_argument("--person", required=True)
    # the setup (device + spoof type + place); also stored for splitting later
    parser.add_argument("--session", required=True, help="e.g. iphone-screen-library")
    # wait at least this many seconds between saves, so images are not near-copies
    parser.add_argument("--interval", type=float, default=0.5, help="seconds between saves")
    # stop automatically after saving this many images
    parser.add_argument("--max", type=int, default=200)
    args = parser.parse_args()  # turns the typed options into args.label, args.person, ...

    # 2. prepare the output folder
    out = DATASET / args.label  # dataset/real or dataset/spoof
    out.mkdir(parents=True, exist_ok=True)  # create it if missing; no error if it exists
    prefix = f"{args.person}__{args.session}__"  # e.g. "bilquees__samsungA05S-screen-room1__"
    # count files this person+session already has, so a second run continues the
    # numbering (0012, 0013, ...) instead of overwriting the first run's images
    saved = len(list(out.glob(prefix + "*.jpg")))

    # 3. open the camera
    cap = cv2.VideoCapture(0)  # 0 = the computer's default webcam
    last = 0.0  # time of the last save; 0 means "save the first good frame straight away"
    count = 0  # images saved in this run

    # 4. main loop: runs once per camera frame until we have enough images ---
    while count < args.max:
        ok, frame = cap.read()  # grab one frame; ok is False if the camera failed
        if not ok:
            break  # stop rather than loop forever on a broken camera

        # OpenCV gives colours as BGR; face_recognition and our crops use RGB, so reverse
        # the colour channels. ascontiguousarray copies the result into one solid block of
        # memory, which the face detector (written in C++) requires.
        rgb = np.ascontiguousarray(frame[:, :, ::-1])
        # shrink the frame to half size; finding faces is the slow part and doesn't need
        # full detail
        small = cv2.resize(rgb, (0, 0), fx=DETECTION_SCALE, fy=DETECTION_SCALE)
        # list of face boxes found, each as (top, right, bottom, left) in the small frame
        boxes = face_recognition.face_locations(small)

        # save only when there is exactly one face (so the label can't be wrong, e.g. a
        # real person standing behind a spoof) and enough time has passed since the last save
        if len(boxes) == 1 and time.time() - last >= args.interval:
            # scale the box back up to full-size coordinates, because we crop from the
            # full-resolution frame to keep as much detail as possible
            box = tuple(int(v / DETECTION_SCALE) for v in boxes[0])
            # cut a square around the face (with some margin), then resize to 224x224
            crop = cv2.resize(crop_face(rgb, box), (SIZE, SIZE))
            # save as e.g. dataset/real/bilquees__samsungA05S-screen-room1__0007.jpg;
            # imwrite expects BGR, so the colour channels are flipped back with [:, :, ::-1]
            cv2.imwrite(str(out / f"{prefix}{saved:04d}.jpg"), crop[:, :, ::-1])
            saved += 1  # next file number
            count += 1  # one more image this run
            last = time.time()  # remember when we saved, for the interval check

        # draw a status line on the preview: progress and how many faces are in view,
        # so you can see when the script is (or isn't) saving
        cv2.putText(frame, f"{args.label}: {count}/{args.max}  faces: {len(boxes)}",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        cv2.imshow("Collect", frame)  # show the live preview window
        # waitKey(1) refreshes the window and checks the keyboard for 1 ms; q quits early
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    # 5. clean up
    cap.release()  # free the webcam so other programs can use it (and the light turns off)
    cv2.destroyAllWindows()  # close the preview window
    print(f"Saved {count} images to {out}")  # summary in the terminal


# run main() only when this file is started directly, not when another file imports it
if __name__ == "__main__":
    main()
