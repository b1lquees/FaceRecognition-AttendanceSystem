"""ArcFace: finding faces and turning each one into 512 numbers.

Replaces dlib's HOG detector and 128-number encoder. Measured on LFW (scripts/benchmark_lfw.py),
ArcFace scored 99.85% against dlib's 98.72% on clean photos, and 99.37% against 94.47% on
photos degraded to look like a phone at the back of a dim room.

Three steps, each one function:

    detect(rgb)            -> where each face is, plus 5 landmarks (eyes, nose, mouth corners)
    align(rgb, landmarks)  -> the face rotated and scaled to a standard 112x112 position
    embed(aligned)         -> 512 numbers, scaled to length 1

Both models are InsightFace's "buffalo_l" (SCRFD-10G detector, ArcFace ResNet-50
recogniser), run with onnxruntime. Not the insightface package itself: on Windows it needs a
C++ compiler to install, the same problem dlib had. The few lines of pre- and post-processing
below follow InsightFace's own code (MIT licence). The model weights are for non-commercial
research use only. Download them with scripts/download_arcface.py.
"""

from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

MODEL_DIR = Path(__file__).resolve().parent / "models" / "arcface"
DETECTOR_FILE = MODEL_DIR / "det_10g.onnx"
RECOGNISER_FILE = MODEL_DIR / "w600k_r50.onnx"

# the detector looks at a 640x640 copy of the frame: a webcam's 640x480 fits at full size
DETECTION_SIZE = 640
# how sure the detector must be that something is a face (0 to 1); InsightFace's default
DETECTION_THRESHOLD = 0.5
# two boxes overlapping more than this are the same face found twice; keep the surer one
NMS_THRESHOLD = 0.4

# SCRFD predicts faces on three grids, 8, 16 and 32 pixels apart, with 2 anchors per grid cell
STRIDES = (8, 16, 32)
ANCHORS_PER_CELL = 2

# where the 5 landmarks sit in a 112x112 face ArcFace was trained on:
# left eye, right eye, nose tip, left mouth corner, right mouth corner
ALIGNED_SIZE = 112
TEMPLATE = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
                     [41.5493, 92.3655], [70.7299, 92.2041]], dtype=np.float32)

_sessions = {}  # model file -> loaded onnxruntime session, filled on first use


class ArcFaceUnavailable(RuntimeError):
    """The model files are missing, so no face can be found or encoded."""


def session(path):
    """The onnxruntime session for one model file, loaded once and then reused."""
    if path not in _sessions:
        if not path.exists():
            raise ArcFaceUnavailable(
                f"{path} not found. Run: python scripts/download_arcface.py"
            )
        _sessions[path] = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    return _sessions[path]


def detect(rgb, threshold=DETECTION_THRESHOLD):
    """Every face in an RGB frame, surest first.

    Returns a list of (box, landmarks): box is (top, right, bottom, left) in pixels, the same
    shape face_recognition used, so the liveness crop and the rest of the app are unchanged;
    landmarks is a 5x2 array of (x, y) points for align().
    """
    height, width = rgb.shape[:2]

    # 1. shrink the frame to fit 640x640 without stretching it, and pad the rest with black
    scale = DETECTION_SIZE / max(height, width)
    resized = cv2.resize(rgb, (int(width * scale), int(height * scale)))
    canvas = np.zeros((DETECTION_SIZE, DETECTION_SIZE, 3), dtype=np.uint8)
    canvas[:resized.shape[0], :resized.shape[1]] = resized

    # 2. pixels from 0..255 to about -1..1, laid out as 1 x 3 x 640 x 640, as SCRFD was trained
    blob = cv2.dnn.blobFromImage(canvas, 1 / 128, (DETECTION_SIZE, DETECTION_SIZE),
                                 (127.5, 127.5, 127.5))
    detector = session(DETECTOR_FILE)
    outputs = detector.run(None, {detector.get_inputs()[0].name: blob})

    # 3. outputs come as 3 score arrays, then 3 box arrays, then 3 landmark arrays (one per grid).
    # Each grid point predicts distances from itself to the box edges and to each landmark.
    boxes, scores, landmarks = [], [], []
    for i, stride in enumerate(STRIDES):
        cells = DETECTION_SIZE // stride
        # the (x, y) pixel position of every grid point, each repeated once per anchor
        ys, xs = np.mgrid[:cells, :cells]
        centres = np.stack([xs, ys], axis=-1).reshape(-1, 2) * stride
        centres = np.repeat(centres, ANCHORS_PER_CELL, axis=0).astype(np.float32)

        score = outputs[i][:, 0]
        keep = score >= threshold
        distances = outputs[i + 3][keep] * stride  # left, top, right, bottom
        points = outputs[i + 6][keep] * stride     # dx, dy for each of the 5 landmarks
        centre = centres[keep]

        boxes.append(np.hstack([centre - distances[:, :2], centre + distances[:, 2:]]))
        landmarks.append(np.tile(centre, 5) + points)
        scores.append(score[keep])

    boxes = np.vstack(boxes) / scale  # back to the original frame's pixels
    landmarks = np.vstack(landmarks).reshape(-1, 5, 2) / scale
    scores = np.concatenate(scores)

    # 4. many neighbouring grid points find the same face; keep only the surest of each cluster
    xywh = [[x1, y1, x2 - x1, y2 - y1] for x1, y1, x2, y2 in boxes.tolist()]
    kept = cv2.dnn.NMSBoxes(xywh, scores.tolist(), threshold, NMS_THRESHOLD)
    kept = sorted(np.array(kept).flatten(), key=lambda k: -scores[k])

    faces = []
    for k in kept:
        x1, y1, x2, y2 = boxes[k]
        # clipped to the frame: a face at the edge can have a box running off it
        box = (max(0, int(y1)), min(width, int(x2)), min(height, int(y2)), max(0, int(x1)))
        faces.append((box, landmarks[k]))
    return faces


def align(rgb, landmarks):
    """The face turned, scaled and moved so its 5 landmarks land on ArcFace's template.

    Finds the best rotation + uniform scale + shift (a "similarity" transform, so the face is
    never stretched) by least squares: for each landmark (x, y) -> template point (u, v),
        u = a*x - b*y + tx
        v = b*x + a*y + ty
    which is 10 equations in the 4 unknowns a, b, tx, ty.
    """
    x, y = landmarks[:, 0], landmarks[:, 1]
    ones, zeros = np.ones(5), np.zeros(5)
    equations = np.vstack([np.stack([x, -y, ones, zeros], axis=1),
                           np.stack([y, x, zeros, ones], axis=1)])
    targets = np.concatenate([TEMPLATE[:, 0], TEMPLATE[:, 1]])
    a, b, tx, ty = np.linalg.lstsq(equations, targets, rcond=None)[0]
    matrix = np.array([[a, -b, tx], [b, a, ty]], dtype=np.float32)
    return cv2.warpAffine(rgb, matrix, (ALIGNED_SIZE, ALIGNED_SIZE), borderValue=0)


def embed(aligned):
    """512 numbers describing an aligned 112x112 RGB face, scaled to length 1.

    Length 1 means the dot product of two embeddings is their cosine similarity: 1 for the
    same direction (same person), about 0 for unrelated faces.
    """
    blob = cv2.dnn.blobFromImage(aligned, 1 / 127.5, (ALIGNED_SIZE, ALIGNED_SIZE),
                                 (127.5, 127.5, 127.5))
    recogniser = session(RECOGNISER_FILE)
    vector = recogniser.run(None, {recogniser.get_inputs()[0].name: blob})[0][0]
    return vector / np.linalg.norm(vector)


def encode(rgb, landmarks):
    """align() then embed(): one detected face straight to its 512 numbers."""
    return embed(align(rgb, landmarks))
