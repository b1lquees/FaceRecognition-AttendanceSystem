"""Download the two ArcFace model files the app needs (run once per computer).

    python scripts/download_arcface.py

Fetches InsightFace's "buffalo_l" pack (about 290 MB) from its GitHub release, keeps only
the face detector (det_10g.onnx) and the face recogniser (w600k_r50.onnx), and checks each
against a known checksum so a broken or swapped download is caught. They are saved in
attendance/models/arcface/, which git ignores: the recogniser is 166 MB, over GitHub's
100 MB file limit.

The models are InsightFace's, licensed for non-commercial research use only, which a
course project is.
"""

import hashlib  # computes the checksum of each file
import shutil  # copies a file out of the zip in chunks
import tempfile  # a temporary place for the zip while it downloads
import urllib.request  # downloads the zip
import zipfile  # reads the zip
from pathlib import Path

URL = "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip"

# where the app looks for the models (attendance/arcface.py reads the same folder)
MODEL_DIR = Path(__file__).resolve().parent.parent / "attendance" / "models" / "arcface"

# file name -> its SHA-256 checksum. If even one byte differs the checksum changes completely,
# so a match means this is exactly the file the app was built and tested with.
FILES = {
    "det_10g.onnx": "5838f7fe053675b1c7a08b633df49e7af5495cee0493c7dcf6697200b85b5b91",
    "w600k_r50.onnx": "4c06341c33c2ca1f86781dab0e829f88ad5b64be9fba56e56bc9ebdefc619e43",
}


def sha256(path):
    """The SHA-256 checksum of a file, read 1 MB at a time so a big file needs little memory."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def already_there():
    """True if both models are already downloaded and correct, so there is nothing to do."""
    return all((MODEL_DIR / name).exists() and sha256(MODEL_DIR / name) == checksum
               for name, checksum in FILES.items())


def show_progress(blocks, block_size, total):
    """Called by urlretrieve as the download goes; prints e.g. '  42%'."""
    if total > 0:
        print(f"\r  {min(100, blocks * block_size * 100 // total):3d}%", end="", flush=True)


def main():
    if already_there():
        print(f"Models already downloaded in {MODEL_DIR}")
        return

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        zip_path = Path(temp) / "buffalo_l.zip"
        print(f"Downloading {URL}")
        urllib.request.urlretrieve(URL, zip_path, show_progress)
        print()

        with zipfile.ZipFile(zip_path) as archive:
            for name, checksum in FILES.items():
                # the file may sit at the top of the zip or inside a folder; find it either way
                member = next(m for m in archive.namelist() if m.endswith(name))
                target = MODEL_DIR / name
                with archive.open(member) as source, open(target, "wb") as out:
                    shutil.copyfileobj(source, out)
                if sha256(target) != checksum:
                    target.unlink()  # never leave a wrong model where the app would load it
                    raise SystemExit(f"{name} failed its checksum; delete it and try again")
                print(f"  saved {target}")

    print("Done.")


if __name__ == "__main__":
    main()
