#!/usr/bin/env python3
"""face-track.py: where the face sits, per span, so a punch-in can be framed.

A punch-in centred on the frame instead of on the face crops an ear off, and
across a long recording the subject drifts even on a tripod. This samples frames
inside each span, finds the face, and reports the median box, which is stable
against the odd frame where the detector misses or grabs a hand.

Deliberately reports ONE box per span rather than per frame. A crop that follows
the face frame by frame reads as a bad auto-reframe; a locked punch-in reads as
a second camera, which is the effect a jump-cut edit is after.

THE ASPECT-RATIO TRAP, which cost an hour: face detectors take a square input
and letterbox or squash whatever you hand them. Feed a 9:16 phone video in whole
and the face lands on a sliver of the model's input, and BOTH BlazeFace and
YuNet return nothing at all on a large, well-lit, front-facing face. Detection
has to run on a SQUARE window cut from the frame. That is what this does, and
why it scans two windows rather than one.

  ~/.venvs/rvm/bin/python scripts/face-track.py SRC.MOV spans.tsv [--samples 5]

Emits "id cx cy w h n" as TSV, in normalised 0-1 source coordinates. Spans with
no detection inherit the median of the spans that had one and are marked n=0.
"""
import argparse
import os
import subprocess
import sys
import urllib.request

import numpy as np

# Weights are ~230KB and cached outside the repo, alongside the whisper models,
# on the same rule that keeps model blobs out of git.
MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_detector/"
             "blaze_face_short_range/float16/1/blaze_face_short_range.tflite")
MODEL_PATH = os.path.expanduser("~/.cache/cv-models/blaze_face_short_range.tflite")
DET_SIZE = 256          # what the square window is resampled to before detection


def model_path():
    if not os.path.exists(MODEL_PATH):
        os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
        print(f"# fetching BlazeFace weights -> {MODEL_PATH}", file=sys.stderr)
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
    return MODEL_PATH


def probe_size(src):
    """DISPLAY dimensions, with rotation applied.

    A phone MOV stores coded dimensions plus a display matrix, and ffprobe's
    stream width/height are the CODED ones. IMG_4181 reports 2160x3840 and
    decodes to 3840x2160. Take the coded size as truth and every frame you hand
    a detector is squashed by 3x along one axis, which is enough to make a large,
    well-lit, front-facing face undetectable. Always resolve the rotation.
    """
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", src],
        capture_output=True, text=True).stdout.strip().split("\n")[0]
    # csv emits a trailing separator on some containers, so filter rather than unpack.
    nums = [int(x) for x in out.split("x") if x.strip().isdigit()]
    w, h = nums[0], nums[1]

    rot = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream_side_data=rotation", "-of", "default=nw=1:nk=1", src],
        capture_output=True, text=True).stdout.strip().split("\n")
    deg = 0
    for line in rot:
        try:
            deg = int(float(line))
            break
        except ValueError:
            continue
    if deg % 180 != 0:
        w, h = h, w
    return w, h


def grab(src, t, tw, th):
    """One frame at t, decoded small, detection does not need 4K."""
    raw = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{t:.3f}", "-i", src,
         "-frames:v", "1", "-vf", f"scale={tw}:{th}", "-f", "rawvideo",
         "-pix_fmt", "rgb24", "-"],
        capture_output=True).stdout
    if len(raw) < tw * th * 3:
        return None
    return np.frombuffer(raw[:tw * th * 3], dtype=np.uint8).reshape(th, tw, 3)


def detect_face(det, mp, cv2, img):
    """Best face in the frame, in normalised frame coordinates, or None.

    Scans square windows down the frame rather than handing the detector the
    whole portrait image, for the reason in the module docstring. Two windows
    cover a talking head: one at the top, one straddling the middle.
    """
    ih, iw = img.shape[:2]
    side = min(iw, ih)
    best = None
    for top in (0, int((ih - side) * 0.35)):
        top = max(0, min(ih - side, top))
        win = np.ascontiguousarray(img[top:top + side, 0:side])
        small = cv2.resize(win, (DET_SIZE, DET_SIZE))
        res = det.detect(mp.Image(image_format=mp.ImageFormat.SRGB,
                                  data=np.ascontiguousarray(small)))
        for d in res.detections or []:
            score = d.categories[0].score
            b = d.bounding_box
            k = side / DET_SIZE
            cx = (b.origin_x + b.width / 2) * k / iw
            cy = (top + (b.origin_y + b.height / 2) * k) / ih
            w = b.width * k / iw
            h = b.height * k / ih
            # A face box is roughly square; anything far from that is a hand or
            # a chunk of the background pattern.
            if not 0.55 < (w * iw) / (h * ih + 1e-6) < 1.8:
                continue
            if best is None or score > best[0]:
                best = (score, [cx, cy, w, h])
    return best[1] if best else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("spans")
    ap.add_argument("--samples", type=int, default=5)
    a = ap.parse_args()

    import cv2
    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions, vision

    det = vision.FaceDetector.create_from_options(vision.FaceDetectorOptions(
        base_options=BaseOptions(model_asset_path=model_path()),
        min_detection_confidence=0.4))

    W, H = probe_size(a.src)
    scale = 720 / max(W, H)
    tw, th = int(W * scale) // 2 * 2, int(H * scale) // 2 * 2

    rows = []
    for line in open(a.spans):
        if line.startswith("#") or not line.strip():
            continue
        f = line.split("\t")
        rows.append((f[0], float(f[1]), float(f[2])))

    found = {}
    for sid, s, e in rows:
        boxes = []
        for k in range(a.samples):
            t = s + (e - s) * (k + 0.5) / a.samples
            img = grab(a.src, t, tw, th)
            if img is None:
                continue
            b = detect_face(det, mp, cv2, img)
            if b:
                boxes.append(b)
        if boxes:
            found[sid] = np.median(np.array(boxes), axis=0)

    if not found:
        print("no faces found in any span", file=sys.stderr)
        return 1
    fallback = np.median(np.array(list(found.values())), axis=0)

    for sid, s, e in rows:
        b = found.get(sid)
        n = len(found) and (a.samples if b is not None else 0)
        if b is None:
            b = fallback
        print(f"{sid}\t{b[0]:.4f}\t{b[1]:.4f}\t{b[2]:.4f}\t{b[3]:.4f}\t{n}")

    print(f"# {len(found)}/{len(rows)} spans had a detected face", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
