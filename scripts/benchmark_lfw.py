"""Compare dlib (what the app uses) with ArcFace on the LFW face-verification benchmark.

    python scripts/benchmark_lfw.py --lfw PATH/TO/lfw --pairs PATH/TO/pairs.txt \
        --arcface PATH/TO/buffalo_l --out benchmark_results

LFW has 6,000 pairs of photos: 3,000 of the same person and 3,000 of two different people.
Each model turns every photo into an embedding (a list of numbers) and scores each pair:
how alike are the two embeddings? Accuracy is measured the standard way, by 10-fold
cross-validation: the same/different cut-off is chosen on 9 folds and tested on the 10th,
so no model is scored on pairs it was tuned on.

Two conditions are run:
  clean     - the photos as they are (the published benchmark)
  classroom - the second photo of every pair is shrunk, darkened, given sensor noise and
              saved as a low-quality JPEG, like a live frame from a phone or webcam at the
              back of a dim room, compared with a clear enrolment photo

Needs: pip install insightface   (ArcFace; its pretrained models are for non-commercial
research use, which a course project is)
"""

import argparse
import json
import time
import zlib
from pathlib import Path

import cv2
import face_recognition
import numpy as np

CLASSROOM_SCALE = 0.4  # 250 px photos become 100 px: a face roughly 40 px across
CLASSROOM_BRIGHTNESS = 0.5  # half the light
CLASSROOM_NOISE = 6  # standard deviation of sensor noise, in 0-255 pixel units
CLASSROOM_JPEG_QUALITY = 50


def read_pairs(path):
    """Official pairs.txt: a header line, then 10 folds of 300 matched + 300 mismatched."""
    lines = Path(path).read_text().split("\n")[1:]
    pairs = []
    for line in lines:
        parts = line.split()
        if len(parts) == 3:  # same person: name, photo number, photo number
            pairs.append(((parts[0], int(parts[1])), (parts[0], int(parts[2])), True))
        elif len(parts) == 4:  # different people: name, number, other name, number
            pairs.append(((parts[0], int(parts[1])), (parts[2], int(parts[3])), False))
    assert len(pairs) == 6000, f"expected 6000 pairs, read {len(pairs)}"
    return pairs


def photo_path(lfw, person):
    name, number = person
    return Path(lfw) / name / f"{name}_{number:04d}.jpg"


def degrade(bgr, seed):
    """The 'classroom' version of a photo: small, dark, noisy and heavily compressed."""
    rng = np.random.default_rng(seed)  # fixed per photo, so every run is identical
    small = cv2.resize(bgr, None, fx=CLASSROOM_SCALE, fy=CLASSROOM_SCALE,
                       interpolation=cv2.INTER_AREA)
    dark = small.astype(np.float32) * CLASSROOM_BRIGHTNESS
    noisy = np.clip(dark + rng.normal(0, CLASSROOM_NOISE, dark.shape), 0, 255).astype(np.uint8)
    ok, jpeg = cv2.imencode(".jpg", noisy, [cv2.IMWRITE_JPEG_QUALITY, CLASSROOM_JPEG_QUALITY])
    return cv2.imdecode(jpeg, cv2.IMREAD_COLOR)


def central(boxes, shape):
    """Of several faces, the one nearest the middle: LFW photos are centred on their person."""
    cy, cx = shape[0] / 2, shape[1] / 2
    return min(boxes, key=lambda b: ((b[0] + b[2]) / 2 - cy) ** 2 + ((b[1] + b[3]) / 2 - cx) ** 2)


class Dlib:
    """The app's current pipeline: HOG face detector + dlib's 128-number ResNet encoder."""
    name = "dlib"

    def embed(self, bgr):
        rgb = np.ascontiguousarray(bgr[:, :, ::-1])
        boxes = face_recognition.face_locations(rgb)  # (top, right, bottom, left)
        found = bool(boxes)
        if found:
            box = central(boxes, rgb.shape)
        else:
            # no face found: fall back to the middle of the photo, where LFW puts the face,
            # so a missed detection costs accuracy instead of dropping the pair
            h, w = rgb.shape[:2]
            box = (int(h * 0.28), int(w * 0.72), int(h * 0.72), int(w * 0.28))
        return face_recognition.face_encodings(rgb, [box])[0], found

    @staticmethod
    def score(a, b):
        return -np.linalg.norm(a - b)  # nearer = more alike, so negate the distance


class ArcFace:
    """SCRFD face detector + ArcFace ResNet-50 encoder (512 numbers), from InsightFace."""
    name = "arcface"

    def __init__(self, model_dir):
        from insightface.app import FaceAnalysis

        model_dir = Path(model_dir)
        # FaceAnalysis looks for <root>/models/<name>/; only detection + recognition needed
        self.app = FaceAnalysis(name=model_dir.name, root=str(model_dir.parent.parent),
                                allowed_modules=["detection", "recognition"],
                                providers=["CPUExecutionProvider"])
        self.app.prepare(ctx_id=-1, det_size=(320, 320), det_thresh=0.3)
        self.recogniser = self.app.models["recognition"]

    def embed(self, bgr):
        if min(bgr.shape[:2]) < 160:
            # SCRFD expects a few hundred pixels; give tiny frames a border, not a stretch
            pad = (320 - min(bgr.shape[:2])) // 2
            bgr = cv2.copyMakeBorder(bgr, pad, pad, pad, pad, cv2.BORDER_CONSTANT)
        faces = self.app.get(bgr)
        if faces:
            boxes = [(f.bbox[1], f.bbox[2], f.bbox[3], f.bbox[0]) for f in faces]
            face = faces[boxes.index(central(boxes, bgr.shape))]
            return face.normed_embedding, True
        # same fallback as dlib: the middle of the photo, resized to ArcFace's 112 x 112
        h, w = bgr.shape[:2]
        crop = cv2.resize(bgr[int(h * .2):int(h * .8), int(w * .2):int(w * .8)], (112, 112))
        vector = self.recogniser.get_feat(crop).flatten()
        return vector / np.linalg.norm(vector), False

    @staticmethod
    def score(a, b):
        return float(np.dot(a, b))  # cosine similarity of unit vectors


def ten_fold_accuracy(scores, same):
    """Standard LFW protocol: pick the cut-off on 9 folds, measure it on the held-out fold."""
    folds = np.arange(len(scores)) // 600
    accuracies = []
    for k in range(10):
        train, test = folds != k, folds == k
        candidates = np.unique(scores[train])
        train_acc = [np.mean((scores[train] >= t) == same[train]) for t in candidates]
        threshold = candidates[int(np.argmax(train_acc))]
        accuracies.append(np.mean((scores[test] >= threshold) == same[test]))
    return float(np.mean(accuracies)), float(np.std(accuracies))


def roc(scores, same):
    """True accept rate against false accept rate, over every possible cut-off."""
    order = np.argsort(-scores)
    hits = same[order]
    tar = np.concatenate([[0], np.cumsum(hits) / hits.sum()])
    far = np.concatenate([[0], np.cumsum(~hits) / (~hits).sum()])
    return far, tar


def tar_at_far(far, tar, target):
    """Genuine pairs accepted when only `target` of impostor pairs may get through."""
    return float(tar[np.searchsorted(far, target, side="right") - 1])


def run(model, pairs, lfw, condition, cache):
    """Embed each photo once (degrading the second of each pair if asked), then score.

    cache is shared between conditions: an untouched photo's embedding is the same
    wherever it is used, so it is worked out once per model, not once per use.
    """
    used = set()

    def embedding(person, probe):
        key = (person, probe and condition == "classroom")  # (photo, degraded?)
        if key not in cache:
            bgr = cv2.imread(str(photo_path(lfw, person)))
            if key[1]:
                bgr = degrade(bgr, seed=zlib.crc32(f"{person[0]}{person[1]}".encode()))
            start = time.perf_counter()
            vector, found = model.embed(bgr)
            cache[key] = (vector, found, time.perf_counter() - start)
        used.add(key)
        return cache[key][0]

    scores = np.array([model.score(embedding(a, False), embedding(b, True)) for a, b, _ in pairs])
    same = np.array([s for _, _, s in pairs])
    mean, std = ten_fold_accuracy(scores, same)
    far, tar = roc(scores, same)
    return {
        "accuracy": mean, "accuracy_std": std,
        "auc": float(np.trapezoid(tar, far)),
        "tar_at_far_1pct": tar_at_far(far, tar, 0.01),
        "tar_at_far_0.1pct": tar_at_far(far, tar, 0.001),
        "face_found_rate": float(np.mean([cache[k][1] for k in used])),
        "ms_per_photo": 1000 * float(np.median([cache[k][2] for k in used])),
        "photos": len(used),
        "roc": {"far": far[::10].tolist(), "tar": tar[::10].tolist()},
        "scores": scores.tolist(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--lfw", required=True, help="folder holding one folder per person")
    parser.add_argument("--pairs", required=True, help="the official pairs.txt")
    parser.add_argument("--arcface", help="folder holding det_10g.onnx and w600k_r50.onnx")
    parser.add_argument("--models", default="dlib,arcface")
    parser.add_argument("--conditions", default="clean,classroom")
    parser.add_argument("--out", default="benchmark_results")
    args = parser.parse_args()

    pairs = read_pairs(args.pairs)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for model_name in args.models.split(","):
        model = Dlib() if model_name == "dlib" else ArcFace(args.arcface)
        cache = {}
        for condition in args.conditions.split(","):
            result = run(model, pairs, args.lfw, condition, cache)
            (out / f"{model_name}_{condition}.json").write_text(json.dumps(result))
            print(f"{model_name:8} {condition:9} accuracy {result['accuracy']:.2%} "
                  f"(+/- {result['accuracy_std']:.2%})  TAR@FAR=0.1% "
                  f"{result['tar_at_far_0.1pct']:.2%}  faces found "
                  f"{result['face_found_rate']:.1%}  {result['ms_per_photo']:.0f} ms/photo",
                  flush=True)


if __name__ == "__main__":
    main()
