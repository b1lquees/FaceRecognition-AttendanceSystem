"""The arithmetic behind the liveness threshold.

Everything else in calibrate_liveness.py needs a camera and a person holding a phone, so
it can only be exercised by hand. These four functions are the part that decides what the
threshold should be, they are pure, and getting them wrong is expensive in a quiet way:
a threshold recommended one step too low lets spoofs through and nothing anywhere says so.

The script is not importable as a module -- scripts/ is deliberately not a package -- so
it is loaded from its path the way a person would run it.
"""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "calibrate_liveness.py"


@pytest.fixture(scope="module")
def calib():
    spec = importlib.util.spec_from_file_location("calibrate_liveness", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- what a threshold costs ---------------------------------------------------------

def test_a_threshold_refuses_real_frames_below_it(calib):
    real = [0.5, 1.5, 2.5, 3.5]

    assert calib.costs(real, [], 2.0) == (2, 0)


def test_a_threshold_admits_spoof_frames_at_or_above_it(calib):
    spoof = [-1.0, 0.5, 2.0, 3.0]

    # 2.0 itself counts as admitted: liveness.is_live() passes on >= threshold, and this
    # has to price the same rule the gate applies, not a near-miss of it
    assert calib.costs([], spoof, 2.0) == (0, 2)


def test_nothing_is_refused_or_admitted_when_the_ranges_separate(calib):
    real = [3.0, 4.0, 5.0]
    spoof = [-5.0, -4.0, -3.0]

    assert calib.costs(real, spoof, 0.0) == (0, 0)


# --- picking one --------------------------------------------------------------------

# The recommendation exists to be the *lowest* safe threshold. Anything higher refuses
# more real people for no further gain, which is the failure mode of picking by eye.
def test_the_recommendation_is_the_cheapest_threshold_that_blocks_every_spoof(calib):
    real = [1.0, 2.0, 3.0, 4.0]
    spoof = [-3.0, -1.0, 0.5]

    safe = calib.safest_threshold(real, spoof)

    assert calib.costs(real, spoof, safe)[1] == 0, "recommended a threshold spoofs get past"
    assert safe == pytest.approx(0.51), "not the lowest one that does the job"


# The distinction this test exists to hold: a threshold that blocks every spoof almost
# always exists, because a hair above the highest spoof score blocks the lot. Whether it
# is worth using is a different question, and conflating the two is how you end up
# recommending a number that refuses everybody.
def test_a_blocking_threshold_still_exists_when_a_spoof_outscores_every_real_face(calib):
    real = [1.0, 2.0]
    spoof = [-1.0, 5.0]   # one spoof above everything genuine

    safe = calib.safest_threshold(real, spoof)

    assert safe == pytest.approx(5.01)
    assert calib.costs(real, spoof, safe) == (2, 0), "blocks the spoof by refusing everyone"


def test_nothing_is_recommended_when_blocking_spoofs_refuses_everybody(calib):
    assert calib.recommend([1.0, 2.0], [-1.0, 5.0]) is None


def test_a_threshold_is_recommended_when_the_cost_is_bearable(calib):
    real = [0.4, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    spoof = [-6.0, -1.0, 0.5]

    # 1 of 10 refused, comfortably under the limit: a gate worth switching on
    assert calib.recommend(real, spoof, limit=0.25) == pytest.approx(0.51)


def test_the_refusal_limit_is_a_parameter_not_a_hardcoded_number(calib):
    real = [0.4, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    spoof = [-6.0, -1.0, 0.5]

    assert calib.recommend(real, spoof, limit=0.05) is None


# The boundary, and it is not academic: a real calibration run landed on exactly 50%
# refusal, and a `>` comparison recommended switching the gate on at that number.
def test_landing_exactly_on_the_limit_is_a_refusal_not_an_acceptance(calib):
    real = [0.0, 0.0, 5.0, 5.0]      # half of them sit below the safe threshold
    spoof = [-1.0, 1.0]

    assert calib.costs(real, spoof, 1.01) == (2, 0), "exactly half refused"
    assert calib.recommend(real, spoof, limit=0.5) is None


# the default has to be strict enough to reject that same 50% case without being told
def test_the_default_limit_rejects_a_gate_that_refuses_half_of_real_frames(calib):
    real = [0.0, 0.0, 5.0, 5.0]
    spoof = [-1.0, 1.0]

    assert calib.MAX_REFUSAL_SHARE <= 0.5
    assert calib.recommend(real, spoof) is None


# overlapping ranges are the interesting case and the one this project actually measured:
# a threshold exists, but it is not free
def test_an_overlap_still_yields_a_threshold_with_a_stated_price(calib):
    real = [0.9, 1.5, 3.0, 4.0, 5.0]
    spoof = [-6.0, -1.0, 2.2]

    safe = calib.safest_threshold(real, spoof)
    refused, admitted = calib.costs(real, spoof, safe)

    assert admitted == 0
    assert refused == 2, "0.9 and 1.5 sit under the threshold and would be turned away"


# --- the tail -----------------------------------------------------------------------

# nearest-rank, so every number printed is one the camera really produced
def test_percentiles_return_measured_values_not_interpolated_ones(calib):
    scores = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]

    assert calib.percentile(scores, 0.10) == 1.0
    assert calib.percentile(scores, 0.50) == 5.0
    assert calib.percentile(scores, 1.0) == 10.0


def test_percentile_of_nothing_is_nothing(calib):
    assert calib.percentile([], 0.5) is None


# --- the sweep ----------------------------------------------------------------------

def test_every_measured_score_is_a_candidate_threshold(calib):
    points = calib.candidates([1.0, 2.0], [-1.0])

    # each sample, and a hair above it -- those are the only places behaviour changes
    for expected in (-1.0, -0.99, 1.0, 1.01, 2.0, 2.01):
        assert expected in points

    assert 0.0 in points, "the default threshold should always be priced"
    assert points == sorted(points)


# --- what gets written to disk ------------------------------------------------------

# The bug this guards: report() sorted its argument in place before save() ever saw it,
# so the files recorded a sorted list whatever save() intended. Capture order is the only
# thing that can answer whether consecutive frames resemble each other, which is the
# question behind judging several frames together instead of one at a time. Sorting threw
# it away silently -- the file looked entirely reasonable.
def test_the_saved_file_keeps_the_order_the_camera_produced(calib, tmp_path, monkeypatch):
    monkeypatch.setattr(calib, "SCRIPTS_DIR", tmp_path)
    jumbled = [3.0, -1.0, 2.0, -4.0]

    calib.save(jumbled, "real")

    assert calib.load("real", keep_order=True) == jumbled


def test_loading_sorts_by_default_because_pricing_does_not_care_about_time(
    calib, tmp_path, monkeypatch
):
    monkeypatch.setattr(calib, "SCRIPTS_DIR", tmp_path)
    calib.save([3.0, -1.0, 2.0, -4.0], "spoof")

    assert calib.load("spoof") == [-4.0, -1.0, 2.0, 3.0]


def test_reporting_does_not_reorder_what_it_saves(calib, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(calib, "SCRIPTS_DIR", tmp_path)
    jumbled = [3.0, -1.0, 2.0, -4.0]

    calib.report(list(jumbled), "real")
    capsys.readouterr()

    assert calib.load("real", keep_order=True) == jumbled
