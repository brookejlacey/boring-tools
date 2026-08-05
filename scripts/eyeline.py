#!/usr/bin/env python3
"""eyeline.py: how far off the lens the subject is looking, per span.

On a phone shoot the lens and the on-screen preview are inches apart, so a
presenter watching herself is looking slightly past the audience the whole time.
It is invisible in a wide shot and obvious in a close-up, which makes it a
FRAMING input: the cuts where the eyeline drifts furthest should not be the ones
punched in tightest.

Measures the iris centre against the centre of the eye opening, in eye-widths,
which normalises out how large the face is in frame. Positive x is the subject's
gaze drifting to frame-right, positive y is downward.

  ~/.venvs/rvm/bin/python scripts/eyeline.py SRC.MOV spans.tsv [--samples 6]

Emits "id dx dy mag n" as TSV. `mag` is the offset magnitude in eye-widths; on a
front-facing phone shoot, under ~0.06 reads as direct address at any size, and
past ~0.12 it is visible in a close-up.

Reads the DISPLAY orientation, and detects on a square window. See face-track.py
for why both of those matter.
"""
import argparse
import os
import subprocess
import sys
import urllib.request

import numpy as np

MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_landmarker/"
             "face_landmarker/float16/1/face_landmarker.task")
MODEL_PATH = os.path.expanduser("~/.cache/cv-models/face_landmarker.task")
DET_SIZE = 512      # iris landmarks need more resolution than a face box does

# MediaPipe face-mesh indices.
L_IRIS = [468, 469, 470, 471, 472]
R_IRIS = [473, 474, 475, 476, 477]
L_CORNERS = (33, 133)      # outer, inner
R_CORNERS = (362, 263)     # inner, outer
L_LID = (159, 145)         # upper, lower
R_LID = (386, 374)


def model_path():
    if not os.path.exists(MODEL_PATH):
        os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
        print(f"# fetching FaceLandmarker weights -> {MODEL_PATH}", file=sys.stderr)
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
    return MODEL_PATH


def probe_display_size(src):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", src],
        capture_output=True, text=True).stdout.strip().split("\n")[0]
    nums = [int(x) for x in out.split("x") if x.strip().isdigit()]
    w, h = nums[0], nums[1]
    rot = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream_side_data=rotation", "-of", "default=nw=1:nk=1", src],
        capture_output=True, text=True).stdout.strip().split("\n")
    for line in rot:
        try:
            if int(float(line)) % 180 != 0:
                w, h = h, w
            break
        except ValueError:
            continue
    return w, h


def eye_offset(pts, iris, corners, lid):
    """Iris centre minus eye-opening centre, in eye-widths."""
    c = np.mean([pts[i] for i in iris], axis=0)
    a, b = pts[corners[0]], pts[corners[1]]
    width = np.linalg.norm(np.array(a) - np.array(b))
    if width < 1e-6:
        return None
    centre_x = (a[0] + b[0]) / 2
    centre_y = (pts[lid[0]][1] + pts[lid[1]][1]) / 2
    return np.array([(c[0] - centre_x) / width, (c[1] - centre_y) / width])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("spans")
    ap.add_argument("--samples", type=int, default=6)
    a = ap.parse_args()

    import cv2
    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions, vision

    lmk = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=model_path()), num_faces=1))

    W, H = probe_display_size(a.src)
    scale = 1080 / max(W, H)
    tw, th = int(W * scale) // 2 * 2, int(H * scale) // 2 * 2
    side = min(tw, th)

    rows = []
    for line in open(a.spans):
        if line.startswith("#") or not line.strip():
            continue
        f = line.split("\t")
        rows.append((f[0], float(f[1]), float(f[2])))

    for sid, s, e in rows:
        offs = []
        for k in range(a.samples):
            t = s + (e - s) * (k + 0.5) / a.samples
            raw = subprocess.run(
                ["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{t:.3f}", "-i", a.src,
                 "-frames:v", "1", "-vf", f"scale={tw}:{th}", "-f", "rawvideo",
                 "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
            if len(raw) < tw * th * 3:
                continue
            img = np.frombuffer(raw[:tw * th * 3], dtype=np.uint8).reshape(th, tw, 3)
            win = np.ascontiguousarray(img[0:side, 0:side])
            small = cv2.resize(win, (DET_SIZE, DET_SIZE))
            res = lmk.detect(mp.Image(image_format=mp.ImageFormat.SRGB,
                                      data=np.ascontiguousarray(small)))
            if not res.face_landmarks:
                continue
            pts = [(p.x, p.y) for p in res.face_landmarks[0]]
            if len(pts) < 478:
                continue
            l = eye_offset(pts, L_IRIS, L_CORNERS, L_LID)
            r = eye_offset(pts, R_IRIS, R_CORNERS, R_LID)
            got = [v for v in (l, r) if v is not None]
            if got:
                offs.append(np.mean(got, axis=0))
        if offs:
            m = np.median(np.array(offs), axis=0)
            print(f"{sid}\t{m[0]:+.4f}\t{m[1]:+.4f}\t{np.linalg.norm(m):.4f}\t{len(offs)}")
        else:
            print(f"{sid}\t\t\t\t0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
