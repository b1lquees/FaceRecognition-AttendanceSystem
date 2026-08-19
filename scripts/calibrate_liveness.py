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

Each run saves its raw scores, and

    python scripts/calibrate_liveness.py --compare

reads both files back and prints what every candidate threshold would actually cost:
how many genuine frames it turns away, and how many spoof frames it lets through.

That last part is why the raw scores are kept. A range and a median cannot answer the
question the decision really turns on -- when the two ranges overlap, *how much* of the
real distribution sits down in the overlap -- and without it you are picking a threshold
by feel. If the overlap is thick in both directions then no threshold works, and the
honest conclusion is that the model is not reliable in your conditions, which is worth
knowing before you depend on it rather than after.

Press q to stop.
"""

import argparse
import json
import statistics
import sys
from pathlib import Path

import cv2
import face_recognition

from attendance.liveness import LivenessUnavailable, score
from attendance.recognition import DETECTION_SCALE

# the saved runs live beside the script, so it makes no difference whether you run this
# from the project root or from inside scripts/ -- both find the same two files
SCRIPTS_DIR = Path(__file__).resolve().parent
SCORE_FILE = "liveness-scores-{}.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--label",
        choices=["real", "spoof"],
        help="what you are holding up: 'real' for your own face, 'spoof' for a photo or screen",
    )
    parser.add_argument("--samples", type=int, default=40, help="how many frames to score")
    parser.add_argument(
        "--compare",
        action="store_true",
        help="skip the camera; read both saved runs back and price every threshold",
    )
    args = parser.parse_args()

    if args.compare:
        compare()
        return

    if not args.label:
        parser.error("--label is required unless you are using --compare")

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


# --- keeping the numbers ------------------------------------------------------------

def save(scores, label):
    """Write the raw scores out, not just the summary, and in the order they arrived.

    A range and a median were enough while the two distributions were expected to
    separate cleanly. They stop being enough the moment they overlap: the question
    becomes what fraction of real faces sits below a candidate threshold, and no summary
    statistic answers that.

    Capture order matters and this file sorted it away in its first version, which was a
    real loss. Whether a gate can judge several frames together instead of one at a time
    depends entirely on how much consecutive frames resemble each other -- if a bad score
    means the next one is bad too, then averaging over five frames buys nothing, and
    sorted scores cannot tell you that either way.
    """
    path = SCRIPTS_DIR / SCORE_FILE.format(label)
    path.write_text(json.dumps(list(scores), indent=1), encoding="utf-8")
    return path


def load(label, keep_order=False):
    """The saved run. Sorted by default, because pricing thresholds does not care about
    time -- but keep_order=True for anything asking how one frame relates to the next."""
    path = SCRIPTS_DIR / SCORE_FILE.format(label)
    if not path.exists():
        sys.exit(f"No saved run for '{label}'. Run --label {label} first.")

    scores = json.loads(path.read_text(encoding="utf-8"))
    return scores if keep_order else sorted(scores)


def percentile(sorted_scores, fraction):
    """The score below which `fraction` of the samples fall.

    Nearest-rank rather than interpolating: with 40 samples an interpolated percentile
    invents a value sitting between two measurements, and every number printed here
    should be one the camera actually produced.
    """
    if not sorted_scores:
        return None
    index = max(0, min(len(sorted_scores) - 1, round(fraction * len(sorted_scores)) - 1))
    return sorted_scores[index]


def report(scores, label):
    if not scores:
        sys.exit("No faces were scored, so there is nothing to report.")

    # the file gets the sequence, the summary below gets a sorted copy. Sorting in place
    # here is what quietly destroyed capture order in the first version of this: the sort
    # happened before save() was ever called, so the flag on save() was not enough.
    captured = list(scores)
    scores = sorted(scores)

    wrong = sum(1 for s in scores if (s >= 0) != (label == "real"))

    print(f"\n{len(scores)} samples labelled '{label}'")
    print(f"  range   {scores[0]:+.2f} to {scores[-1]:+.2f}")
    print(f"  median  {statistics.median(scores):+.2f}")
    print(f"  wrong at threshold 0.0: {wrong} of {len(scores)} ({wrong/len(scores):.0%})")

    # the shape of the lower tail, which is the part a threshold has to cut through
    marks = "  ".join(
        f"p{int(f * 100)} {percentile(scores, f):+.2f}" for f in (0.05, 0.10, 0.25, 0.50)
    )
    print(f"  {marks}")

    path = save(captured, label)
    print(f"\n  raw scores saved to {path.name}")

    other = "spoof" if label == "real" else "real"
    if (SCRIPTS_DIR / SCORE_FILE.format(other)).exists():
        print("  both runs are on disk -- run with --compare to price every threshold.")
    else:
        print(f"  now run again with --label {other}, then --compare.")


# --- pricing the thresholds ---------------------------------------------------------

def costs(real, spoof, threshold):
    """What one threshold costs, as (real frames refused, spoof frames let through)."""
    refused = sum(1 for s in real if s < threshold)
    admitted = sum(1 for s in spoof if s >= threshold)
    return refused, admitted


def candidates(real, spoof):
    """Every threshold worth considering: the measurements themselves.

    A threshold only changes behaviour when it crosses a sample, so the sample values --
    and a hair above each of them -- are the entire space of distinct decisions. Sweeping
    in fixed steps would print rows differing from their neighbours in nothing but the
    number at the front.
    """
    points = {0.0}
    for value in list(real) + list(spoof):
        points.add(round(value, 2))
        points.add(round(value + 0.01, 2))
    return sorted(points)


# How many of a genuine person's frames a threshold may refuse before the gate stops
# being worth having.
#
# The camera checks every 1.5 seconds and someone standing there simply waits for the next
# one, so a refusal is a retry rather than a lockout: at a refusal rate r, they are through
# within two checks with probability 1 - r squared. At 0.25 that is 94% within three
# seconds, which reads as a system that works. At 0.5 it is 75%, and one person in four
# watches it fail twice running -- at which point they stop trusting it, which costs more
# than the spoof would have.
#
# It is a judgement, not a measurement, which is why it is one named constant with the
# reasoning attached rather than a number buried in an if.
MAX_REFUSAL_SHARE = 0.25


def safest_threshold(real, spoof):
    """The lowest threshold that let no spoof frame through.

    Lowest, because every step above it refuses more real people for no further gain.
    One nearly always exists -- a hair above the highest spoof score blocks the lot --
    which is exactly why this is not the same question as whether the gate is usable.
    """
    return min(
        (t for t in candidates(real, spoof) if costs(real, spoof, t)[1] == 0),
        default=None,
    )


def recommend(real, spoof, limit=MAX_REFUSAL_SHARE):
    """The threshold worth actually using, or None if blocking spoofs costs too much."""
    safe = safest_threshold(real, spoof)
    if safe is None or not real:
        return None

    # >= rather than >: a limit of 0.25 means a quarter is already too many. Landing
    # exactly on the line recommended the threshold at 50% refusal in one real run, which
    # is the boundary being read as "just about acceptable" instead of "the limit".
    refused, _ = costs(real, spoof, safe)
    if refused / len(real) >= limit:
        return None
    return safe


def compare():
    real, spoof = load("real"), load("spoof")

    print(f"real  : {len(real)} samples, {real[0]:+.2f} to {real[-1]:+.2f}")
    print(f"spoof : {len(spoof)} samples, {spoof[0]:+.2f} to {spoof[-1]:+.2f}")

    if real[0] <= spoof[-1]:
        print(
            f"\nThe ranges overlap between {real[0]:+.2f} and {spoof[-1]:+.2f}. No threshold"
            "\nboth admits every real frame and blocks every spoof one, so what follows is"
            "\nthe trade, priced. Pick a row; do not split the difference between medians."
        )
    else:
        print("\nThe ranges separate cleanly. Any threshold between them works.")

    # Only the decision region. A threshold down among the spoof scores lets most of them
    # through and is not a candidate for anything, and printing one row per sample buries
    # the handful of rows somebody might actually choose between under sixty they would
    # not. The region worth seeing is where spoofs are nearly shut out, plus every row
    # where that has started costing real people.
    noise_floor = max(1, len(spoof) // 4)

    print("\n  threshold   real refused        spoofs let in")
    shown = None
    hidden = 0
    for threshold in candidates(real, spoof):
        refused, admitted = costs(real, spoof, threshold)
        # one row per distinct outcome: consecutive thresholds that behave identically are
        # the same decision written out twice
        if (refused, admitted) == shown:
            continue
        shown = (refused, admitted)

        if admitted > noise_floor and refused == 0:
            hidden += 1
            continue

        print(
            f"  {threshold:+8.2f}   {refused:3d}/{len(real):<3d} ({refused/len(real):4.0%})"
            f"      {admitted:3d}/{len(spoof):<3d} ({admitted/len(spoof):4.0%})"
        )

        # nothing above this row is worth reading: the spoofs are already all blocked, so
        # every higher threshold refuses more real people to buy exactly nothing
        if admitted == 0:
            break

    if hidden:
        print(f"  ({hidden} lower thresholds omitted -- they let most spoofs through)")

    # The recommendation follows the asymmetry the whole feature rests on: a refused real
    # person retries 1.5 seconds later and is mildly annoyed, while an admitted spoof is a
    # false attendance record nobody will ever notice. So shut the spoofs out first, and
    # only then ask what it cost.
    safe = safest_threshold(real, spoof)
    if safe is None:
        print("\nNo spoof samples, so there is nothing here to defend against.")
        return

    refused, _ = costs(real, spoof, safe)
    share = refused / len(real)
    print(f"\nLowest threshold that blocked every spoof frame here: {safe:+.2f}")
    print(f"  it would have refused {refused} of {len(real)} real frames ({share:.0%}),")
    print("  each costing one 1.5-second retry rather than a lockout.")

    if recommend(real, spoof) is None:
        print(f"\n  Refusing {share:.0%} of genuine frames is not a working gate.")
        print("  Leave LIVENESS_ENABLED=0 rather than shipping that.")
    else:
        print("\n  set LIVENESS_ENABLED=1")
        print(f"  set LIVENESS_THRESHOLD={safe:.2f}")

    print("\nMeasured against one camera and one spoof. It is evidence, not a proof.")


if __name__ == "__main__":
    main()
