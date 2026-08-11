"""Measure the anti-spoofing model against your own camera, and pick a threshold.

The model ships switched off, because how well it works depends on your camera and your
lighting, and nobody can tell you that from the outside. This script is how you find out.

    python scripts/calibrate_liveness.py

Run it twice, at least:

  1. As yourself, in front of the camera. Every one of those is a REAL face, so every
     score should be positive. Scores that go negative are false rejections -- you being
     refused entry to your own building.

  2. Holding up a printed photo, or a face on a phone screen. Those are SPOOFS, and every
     score should be negative. Scores that go positive are the attack getting through.

It prints the range it saw, and suggests a threshold that separates the two if one
exists. If the ranges overlap, no threshold separates them and the honest conclusion is
that the model is not reliable in your conditions -- which is worth knowing before you
depend on it rather than after.

Press q to stop.
"""

import argparse
import statistics
import sys

import cv2
import face_recognition

from attendance.liveness import LivenessUnavailable, score
from attendance.recognition import DETECTION_SCALE


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--label",
        choices=["real", "spoof"],
        required=True,
        help="what you are holding up: 'real' for your own face, 'spoof' for a photo or screen",
    )
    parser.add_argument("--samples", type=int, default=40, help="how many frames to score")
    args = parser.parse_args()

    camera = cv2.VideoCapture(0)
    if not camera.isOpened():
        sys.exit("Could not open the camera.")

    print(f"Collecting {args.samples} samples labelled '{args.label}'. Press q to stop early.\n")
    scores = []

    try:
        while len(scores) < args.samples:
            ok, frame = camera.read()
            if not ok:
                print("Failed to read a frame.", file=sys.stderr)
                break

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            small = cv2.resize(rgb, (0, 0), fx=DETECTION_SCALE, fy=DETECTION_SCALE)
            locations = face_recognition.face_locations(small)

            status = "no face"
            colour = (0, 165, 255)
            if locations:
                back = 1 / DETECTION_SCALE
                box = tuple(int(edge * back) for edge in locations[0])
                try:
                    value = score(rgb, box)
                except LivenessUnavailable as error:
                    sys.exit(f"\n{error}")
                scores.append(value)

                # the sign is the verdict at the default threshold of 0.0
                agrees = (value >= 0) == (args.label == "real")
                status = f"{value:+.2f}  {'ok' if agrees else 'WRONG'}"
                colour = (0, 200, 0) if agrees else (0, 0, 255)

                top, right, bottom, left = box
                cv2.rectangle(frame, (left, top), (right, bottom), colour, 2)

            cv2.putText(frame, f"{len(scores)}/{args.samples}  {status}", (12, 32),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, colour, 2)
            cv2.imshow("liveness calibration - press q to stop", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        camera.release()
        cv2.destroyAllWindows()

    report(scores, args.label)


def report(scores, label):
    if not scores:
        sys.exit("No faces were scored, so there is nothing to report.")

    scores.sort()
    wrong = sum(1 for s in scores if (s >= 0) != (label == "real"))

    print(f"\n{len(scores)} samples labelled '{label}'")
    print(f"  range   {scores[0]:+.2f} to {scores[-1]:+.2f}")
    print(f"  median  {statistics.median(scores):+.2f}")
    print(f"  wrong at threshold 0.0: {wrong} of {len(scores)} ({wrong/len(scores):.0%})")

    if label == "real":
        print(f"\n  Lowest real score was {scores[0]:+.2f}.")
        print("  A threshold above that would start refusing genuine people.")
        print("  Run again with --label spoof, then set LIVENESS_THRESHOLD between the")
        print("  highest spoof score and this number.")
    else:
        print(f"\n  Highest spoof score was {scores[-1]:+.2f}.")
        print("  A threshold below that would let spoofs through.")
        print("  Set LIVENESS_THRESHOLD above it, and check it is still below your")
        print("  lowest real score -- if it is not, the two overlap and no threshold works.")


if __name__ == "__main__":
    main()
