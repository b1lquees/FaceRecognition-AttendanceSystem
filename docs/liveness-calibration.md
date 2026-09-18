# Liveness calibration

The anti-spoofing gate ([facenox/face-antispoof-onnx](https://github.com/facenox/face-antispoof-onnx),
Apache 2.0) scores a face and refuses it if the score falls below `LIVENESS_THRESHOLD`.
The threshold is the whole feature: too low and it admits the photograph it exists to
refuse, too high and it refuses the person standing there. It depends on your camera and
your lighting, which is why the feature ships disabled and why this page is a measurement
procedure rather than a recommended number.

Two terms, used throughout:

- **False acceptance** — a spoof scored as real. Someone is marked present who was never
  there.
- **False rejection** — a real face scored as a spoof. Someone present is refused and has
  to try again.

A threshold trades one directly against the other. The only question worth asking of a
camera is whether there is any threshold where the false-acceptance rate is zero and the
false-rejection rate is still small enough to live with. On the camera measured below,
there is not.

The [anti-spoofing section of the README](../README.md#anti-spoofing) is the summary; this
is the method and the full result.

## What calibration found here

On the laptop webcam this was developed on, 40 samples each, a phone screen as the spoof:

| | min | median | max |
| --- | --- | --- | --- |
| Real face | −3.18 | **+1.21** | +6.61 |
| Photo / screen | −6.19 | **−3.31** | +1.05 |

**Those ranges overlap, and the overlap is fatal.** Half the genuine frames score below
the best spoof frame. Blocking every spoof needs a threshold of `+1.06`, which refuses
**20 of 40 real frames**; leaving the default `0.0` refuses 40% of real frames *and*
admits 8% of spoof frames. No setting on this camera is worth having, so the feature stays
off.

Two findings from that measurement generalise:

- **No middle setting helps, because both sides retry.** A threshold admitting 22% of
  spoof frames looks like a compromise until you remember the attacker is still holding
  the phone up: at one check every 1.5 seconds, a photo is through in under five seconds.
  Only 0% admitted is worth anything, and that is the expensive end.
- **The score moves more with lighting than with liveness.** Two runs of the same face on
  the same camera an hour apart gave `+0.87 → +7.22` with nothing negative, then
  `−3.18 → +6.61` with 40% negative. A threshold tuned in one lighting condition is wrong
  in the next — a stronger argument against depending on this than the overlap itself.

## Calibrating your own camera

Your camera may separate cleanly. Measure before assuming either way. Run it as yourself,
then again holding up a printed photo or a face on a phone screen:

```bash
python scripts/calibrate_liveness.py --label real
```

```bash
python scripts/calibrate_liveness.py --label spoof
```

Each run saves its raw scores, in capture order, next to the script. Then, with no camera
needed:

```bash
python scripts/calibrate_liveness.py --compare
```

`--compare` pools every saved run, and reports first how much each one actually measured.
That part matters more than the thresholds it prints. Frames captured back to back are
nearly the same measurement — on the runs in this repository the frame-to-frame correlation
is **+0.91**, which makes 40 samples worth about **two** independent observations. A
threshold priced on two observations is an anecdote with decimal places, and it explains the
instability between two runs an hour apart better than lighting alone does.

So capture across time rather than in one burst, and tag each condition:

```bash
python scripts/calibrate_liveness.py --label real --tag morning --interval 5
```

Repeat for `spoof`, then again with `--tag midday`, `--tag dusk`, `--tag dark`. Runs
accumulate instead of overwriting each other, and `--compare` pools the lot — which is the
point, since one threshold has to survive every condition the door sees.

```bash
python scripts/calibrate_liveness.py --consecutive
```

prices a gate that requires several frames in a row to agree rather than judging one at a
time. It does not compound the way independent sampling would — with correlation that high,
a frame that passed makes the next one likely to pass too — but it still helps, for a
different reason: a spoof must *sustain* a score rather than touch it once, so the threshold
can come down, and a lower threshold refuses fewer real people. On the runs here that moves
real acceptance from 50% to 69% at zero spoofs admitted, across five frames.

That prices every threshold behaving differently from its neighbours — real frames refused
against spoof frames admitted — and names the cheapest one that blocked every spoof, with
what it costs. If that cost exceeds a quarter of genuine frames it tells you to leave the
feature off rather than handing you a number, because a gate refusing a quarter of
someone's frames has stopped being a gate.

If it does give you a threshold:

```bash
export LIVENESS_ENABLED=1
export LIVENESS_THRESHOLD=<the number it printed>
```

If it starts refusing you, set `LIVENESS_ENABLED=0` and recalibrate rather than lowering
the threshold until everything passes. Lowering it until you get in is exactly the motion
that leaves the gate switched on and doing nothing.

