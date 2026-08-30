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
import re
import statistics
import sys
import time
from pathlib import Path

import cv2
import face_recognition

from attendance.liveness import LivenessUnavailable, score
from attendance.recognition import DETECTION_SCALE

# the saved runs live beside the script, so it makes no difference whether you run this
# from the project root or from inside scripts/ -- both find the same two files
SCRIPTS_DIR = Path(__file__).resolve().parent
SCORE_FILE = "liveness-scores-{}.json"

# A tagged run keeps its own file, so several conditions can sit on disk at once rather
# than each capture silently overwriting the last. That is not a convenience. The reason
# is in the sampling section of --compare: one burst of frames measures one moment, and
# the moment turns out to matter more than the camera does.
TAGGED_FILE = "liveness-scores-{}-{}.json"
TAG_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--label",
        choices=["real", "spoof"],
        help="what you are holding up: 'real' for your own face, 'spoof' for a photo or screen",
    )
    parser.add_argument("--samples", type=int, default=40, help="how many frames to score")
    parser.add_argument(
        "--tag",
        help="name this run (morning, dusk, backlit) so it does not overwrite the last",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.0,
        help="seconds between samples; spreads a run over time instead of one burst",
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="skip the camera; pool every saved run and price every threshold",
    )
    parser.add_argument(
        "--consecutive",
        action="store_true",
        help="skip the camera; price a gate that needs N frames in a row to agree",
    )
    args = parser.parse_args()

    if args.tag and not TAG_PATTERN.match(args.tag):
        parser.error("--tag must be letters, digits, dashes or underscores (max 32)")

    if args.compare:
        compare()
        return

    if args.consecutive:
        consecutive()
        return

    if not args.label:
        parser.error("--label is required unless using --compare or --consecutive")

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
                # Spread over time on purpose. Frames taken back to back, of the same
                # face in the same light, are very nearly the same measurement -- see
                # the sampling section of --compare -- so forty frames in forty seconds
                # is nothing like forty observations. Waiting is what buys independent
                # ones, and independence is what every percentage here rests on.
                if args.interval and idle(camera, args.interval):
                    break

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

    report(scores, args.label, args.tag)


def idle(camera, seconds):
    """Keep the preview alive between samples. Returns True if the user asked to stop."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        ok, frame = camera.read()
        if ok:
            cv2.imshow("liveness calibration - press q to stop", frame)
        if cv2.waitKey(30) & 0xFF == ord("q"):
            return True
    return False


# --- how much a run actually measured -------------------------------------------------

def lag1(scores):
    """How strongly each score resembles the next one, within a single run."""
    if len(scores) < 3:
        return 0.0
    mean = statistics.fmean(scores)
    spread = sum((s - mean) ** 2 for s in scores)
    if not spread:
        return 1.0
    pairs = sum((scores[i] - mean) * (scores[i + 1] - mean) for i in range(len(scores) - 1))
    return pairs / spread


def effective_samples(runs):
    """How many independent observations a set of runs is really worth.

    Forty consecutive frames of one face are not forty measurements of anything. They are
    one face, in one light, sampled forty times in forty seconds, and the score barely
    moves between them -- so nearly all of that data is one observation written down
    repeatedly. The standard correction for a series that resembles itself is
    n(1-r)/(1+r), and at r = 0.9 it takes forty samples down to about two.

    Printed rather than kept quiet, because it decides whether anything else here means
    anything. A threshold priced on two observations is not a measurement; it is an
    anecdote carrying decimal places.
    """
    total = 0.0
    for _, run in runs:
        r = max(0.0, min(0.99, lag1(run)))
        total += len(run) * (1 - r) / (1 + r)
    return total


def sampling_note(label, runs):
    frames = sum(len(run) for _, run in runs)
    effective = effective_samples(runs)
    correlations = ", ".join(f"{lag1(run):+.2f}" for _, run in runs)
    print(f"  {label:<6} {frames:3d} frames over {len(runs)} run(s)"
          f"   frame-to-frame correlation {correlations}"
          f"   worth about {effective:.0f} independent samples")
    return effective


# --- keeping the numbers ------------------------------------------------------------

def save(scores, label, tag=None):
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
    name = TAGGED_FILE.format(label, tag) if tag else SCORE_FILE.format(label)
    path = SCRIPTS_DIR / name
    path.write_text(json.dumps(list(scores), indent=1), encoding="utf-8")
    return path


def load_runs(label):
    """Every saved run for this label, kept separate, in capture order.

    Separate rather than concatenated because two things here are computed *within* a run
    and would be nonsense across the join: how one frame relates to the next, and whether
    N frames in a row agree. Gluing two runs end to end invents a pair of adjacent frames
    that were never adjacent -- taken hours apart, in different light.
    """
    paths = sorted(SCRIPTS_DIR.glob(f"liveness-scores-{label}*.json"))
    if not paths:
        sys.exit(f"No saved run for '{label}'. Run --label {label} first.")
    return [(path, json.loads(path.read_text(encoding="utf-8"))) for path in paths]


def load(label, keep_order=False):
    """Every saved run for this label, pooled into one list.

    Pooled on purpose: a threshold gets chosen once and then has to face every lighting
    condition the door sees, so pricing it against one condition at a time is precisely
    how you arrive at a number that works in the morning and fails after lunch.
    """
    scores = [value for _, run in load_runs(label) for value in run]
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


def report(scores, label, tag=None):
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

    path = save(captured, label, tag)
    print(f"\n  raw scores saved to {path.name}")

    other = "spoof" if label == "real" else "real"
    if list(SCRIPTS_DIR.glob(f"liveness-scores-{other}*.json")):
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


# Below this many independent samples, the percentages further down are not measurements.
# Two observations can put a threshold almost anywhere, and the table would still print to
# the nearest percent -- which is exactly how a number with nothing behind it gets believed.
MIN_EFFECTIVE_SAMPLES = 10


def warn_about_sampling():
    print()
    print("  WARNING. Frames taken back to back are nearly the same measurement, so these")
    print("  runs are worth far fewer independent samples than they contain. Every number")
    print("  below is priced on that, and a threshold picked from it will not survive the")
    print("  next lighting condition -- which is the instability already recorded between")
    print("  two runs an hour apart. That is a sampling problem, not a camera problem, and")
    print("  a better camera does not fix it.")
    print()
    print("  Capture across time and conditions instead of in one burst:")
    print("      python scripts/calibrate_liveness.py --label real  --tag morning --interval 5")
    print("      python scripts/calibrate_liveness.py --label spoof --tag morning --interval 5")
    print("  then again with --tag midday, --tag dusk, --tag dark. --compare pools them all.")


def compare():
    real_runs, spoof_runs = load_runs("real"), load_runs("spoof")
    real, spoof = load("real"), load("spoof")

    for path, _ in real_runs + spoof_runs:
        print(f"  pooled: {path.name}")

    print()
    print("How much was actually measured:")
    weakest = min(sampling_note("real", real_runs), sampling_note("spoof", spoof_runs))
    if weakest < MIN_EFFECTIVE_SAMPLES:
        warn_about_sampling()

    print()
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


# --- a gate that wants several frames to agree ----------------------------------------

MAX_CONSECUTIVE = 5


def stretches_all_passing(runs, threshold, n):
    """Share of n-frame stretches, within a run, where every frame is at or above threshold."""
    windows = []
    for _, scores in runs:
        for i in range(len(scores) - n + 1):
            windows.append(all(s >= threshold for s in scores[i:i + n]))
    return 100.0 * sum(windows) / len(windows) if windows else 0.0


def consecutive():
    """Price a gate that needs N frames in a row rather than judging one at a time.

    The appeal is that a spoof scraping past the threshold once is unlikely to manage it
    five times running. That reasoning only holds while consecutive frames are close to
    independent, and the frame-to-frame correlation printed above is what says whether
    they are: when it is high, a frame that passed makes the next one likely to pass too,
    and requiring more of them buys far less than the arithmetic suggests.

    It can still help, for a different reason worth keeping separate in your head. A spoof
    now has to *sustain* a score rather than touch it once, which lets the threshold come
    down -- and a lower threshold refuses fewer real people. That is the trade below.
    """
    real_runs, spoof_runs = load_runs("real"), load_runs("spoof")

    print("How much was actually measured:")
    weakest = min(sampling_note("real", real_runs), sampling_note("spoof", spoof_runs))
    if weakest < MIN_EFFECTIVE_SAMPLES:
        warn_about_sampling()

    real, spoof = load("real"), load("spoof")
    print()
    print("   N   threshold   real stretches accepted   spoof stretches admitted")
    print("   -   ---------   -----------------------   ------------------------")

    for n in range(1, MAX_CONSECUTIVE + 1):
        blocking = [
            t for t in candidates(real, spoof)
            if stretches_all_passing(spoof_runs, t, n) == 0.0
        ]
        if not blocking:
            print(f"   {n}       --       no threshold blocks every {n}-frame spoof stretch")
            continue
        threshold = min(blocking)
        accepted = stretches_all_passing(real_runs, threshold, n)
        admitted = stretches_all_passing(spoof_runs, threshold, n)
        print(f"   {n}     {threshold:+6.2f}            {accepted:5.1f}%"
              f"                    {admitted:5.1f}%")

    print()
    print("N is latency: at 1.5s a frame, that is how long somebody stands there before the")
    print("gate can say yes. The last column is the only one that has to be zero -- somebody")
    print("holding a photo up simply waits for the next stretch.")


if __name__ == "__main__":
    main()
