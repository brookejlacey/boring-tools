#!/usr/bin/env python3
"""wall-clean.py: take a fixture off the wall behind the subject, for a whole take.

The 2026-07-28 take has a white switch plate on the grey wall, level with the
back of Brooke's head. It reads as clutter in every framing at once, and it is a
WALL object, so patching the finished ads would mean fixing the same thing six
times at six different crops and getting all six to agree. Fix it once in source
space instead: this writes a cleaned working copy of the take, and every cut
assembled from that copy inherits the fix.

The master is never touched. The output is a working intermediate, which is why
it re-encodes without apology: assemble-cuts.py re-encodes every cut anyway, so
the generation this costs is one the finished ad was always going to pay.

HOW IT WORKS, and what did not work.

The obvious approach, measure what is behind the fixture across the take, then
subtract it, cannot work here, and it is worth writing down why, because it
looks like it should. Hair covers the left half of this fixture for most of the
take, so no temporal estimator recovers that half: a plain median returns hair, a
median over unsaturated frames returns hair too (bleached blonde clears any
threshold loose enough to keep an off-white plate), and picking each pixel's
least-saturated sample returns a blown-out flyaway, which comes back BRIGHTER
than the wall it is supposed to be. Each of those leaves a ghost of the fixture
exactly where it is most exposed, which is the one place it had to be gone.

What works is the thing a colorist would do: borrow. There is a metre of flat
empty wall to camera-right that is clean in every single frame of the take
(verified: 99th-percentile saturation 16, texture 2.6, across all 8692). So the
patch is that wall, taken from the SAME FRAME, shifted across:

    patch(x, y) = frame(x + DX, y)  +  [ wall(x, y) - wall(x + DX, y) ]

The borrowed pixels bring the frame's own grain, its own exposure and its own
noise, already moving at the right speed, so nothing needs to be synthesised and
nothing crawls. The bracketed term is the only modelled part: a smooth
wall-brightness field that corrects for the room's falloff between the two
positions. Being a difference of two nearby samples of a smooth field, it is
small and forgiving.

WALL FIELD. Per pixel, the tightest cluster in that pixel's history is the thing
that is always there; that gives a static plate. Everything on it that is not
flat neutral wall, the fixture, its shadow, any hair that never moved, is then
solved away with Laplace, pinned to the real wall pixels around it, so the field
is continuous by construction and there is no seam to hide.

WHAT IS KEPT. Hair is kept exactly as shot: saturated pixels are foreground, and
so is anything markedly darker than the wall should be, which is what stops a
dark shirt from being painted over on a frame where she leans back. The fixture
is brighter than the wall, so neither guard touches it.

Only the patch window is decoded into Python. The full 4K frame never crosses a
pipe: ffmpeg crops the window out, Python returns the patched window, and ffmpeg
composites it back and encodes.

  scripts/wall-clean.py SRC.MOV OUT.mov --rect X,Y,W,H
  scripts/wall-clean.py SRC.MOV --rect X,Y,W,H --preview 8675 --stem /tmp/p

Coordinates are DISPLAY pixels (rotation already applied), the same space
face-track.py and assemble-cuts.py work in. Before trusting a new --donor, check
it is clean for the WHOLE take, not for the frame you happened to look at.
"""
import argparse
import subprocess
import sys

import numpy as np

# Wall to keep around the fixture, for the field solve to pin against.
RING = 110

# Where the patch is borrowed from, in px to camera-right of the fixture. Must
# be flat empty wall in every frame; 400 was measured clean on IMG_4199 and 300
# was not (hair reaches it around frame 7440).
DONOR_DX = 440

# Hair. Measured on this footage: wall 8.4, the fixture's shadow 8.4, the
# fixture 20, hair 43. The ramp sits in the gap above the fixture.
CHROMA_LO, CHROMA_HI = 26.0, 40.0

# Anything this much darker than the wall field is something in front of it,
# not the fixture, the fixture is brighter than the wall, never darker.
DARK_LO, DARK_HI = 16.0, 38.0

SOFT = 3           # blur radius on the foreground alpha, px
FEATHER = 7        # blur radius on the patch window's own border, px
BG_SAMPLES = 260   # frames sampled to build the wall field
BG_FRAC = 0.22     # per pixel, the fraction of samples in the tightest cluster
MARGIN = 20        # wall kept around the fixture to match the patch against
DARK_ERODE = 14    # a dark region must be at least this wide to be protected


def probe(src, fields):
    return subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", f"stream={fields}", "-of", "default=nw=1:nk=1", src],
        capture_output=True, text=True).stdout.strip().split("\n")


def display_size(src):
    """DISPLAY dimensions, rotation applied, see face-track.py for why."""
    w, h = (int(v) for v in probe(src, "width,height")[:2])
    rot = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream_side_data=rotation",
         "-of", "default=nw=1:nk=1", src],
        capture_output=True, text=True).stdout.strip().split("\n")
    for line in rot:
        try:
            if int(float(line)) % 180 != 0:
                w, h = h, w
            break
        except ValueError:
            continue
    return w, h


def fps_of(src):
    num, _, den = probe(src, "r_frame_rate")[0].partition("/")
    return float(num) / float(den or 1)


def frame_count(src, fps):
    dur = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nk=1:nw=1", src],
        capture_output=True, text=True).stdout.strip()
    return int(round(float(dur) * fps))


def chroma(bgr):
    """How colourful a pixel is, as max-min, NOT as HSV saturation.

    HSV divides by the maximum, so it reports a dark neutral as colourful: the
    shadow under this fixture measures 20 on that scale against hair's 66, close
    enough that any threshold separating hair from wall also protects the
    shadow, and a shadow left behind after the thing casting it is gone is
    worse than the fixture was. Undivided, the same shadow measures 8.4, exactly
    the wall's own 8.4, while hair sits at 43 and the fixture at 20."""
    f = bgr.astype(np.float32)
    return f.max(axis=2) - f.min(axis=2)


def box_blur(a, r):
    """Separable box blur by cumulative sum. The alpha is a mask, not an image;
    it needs a soft edge, not a gaussian."""
    if r < 1:
        return a.astype(np.float32)
    a = a.astype(np.float32)
    k = 2 * r + 1
    for axis in (0, 1):
        pad = np.pad(a, [(r, r) if i == axis else (0, 0) for i in range(a.ndim)],
                     mode="edge")
        c = np.cumsum(pad, axis=axis, dtype=np.float32)
        c = np.concatenate([np.zeros_like(np.take(c, [0], axis=axis)), c], axis=axis)
        n = a.shape[axis]
        a = (np.take(c, range(k, k + n), axis=axis)
             - np.take(c, range(0, n), axis=axis)) / k
    return a


def ramp(a, lo, hi):
    """Soft 0..1 ramp. A hard threshold on a per-pixel test is what puts speckle
    in the middle of a flat surface: a few noisy pixels cross it, the blur
    spreads them, and the patch is held off across an area where nothing moved."""
    return np.clip((a - lo) / (hi - lo), 0.0, 1.0)


def stream_window(src, x, y, w, h, hwdec=True):
    """Yield the crop window frame by frame as BGR, and nothing else."""
    cmd = ["ffmpeg", "-nostdin", "-v", "error"]
    if hwdec:
        cmd += ["-hwaccel", "videotoolbox"]
    cmd += ["-i", src, "-vf", f"crop={w}:{h}:{x}:{y}",
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    n = w * h * 3
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=n * 4)
    try:
        while True:
            buf = p.stdout.read(n)
            if len(buf) < n:
                break
            yield np.frombuffer(buf, np.uint8).reshape(h, w, 3)
    finally:
        p.stdout.close()
        p.wait()


def static_plate(src, x, y, w, h, total, hwdec=True):
    """Per pixel, the mean of the tightest BG_FRAC cluster of its samples.

    For a locked camera that is whatever was always there. Anything that swept
    past spreads out around it and is outvoted. This is only ever used to build
    the smooth wall field, never as the answer for what is behind the fixture -
    see the module docstring for why that does not work on this take."""
    step = max(1, total // BG_SAMPLES)
    stack, i = [], 0
    for win in stream_window(src, x, y, w, h, hwdec):
        if i % step == 0:
            stack.append(win.copy())
        i += 1
    a = np.stack(stack)
    n = len(stack)
    k = max(8, int(n * BG_FRAC))
    lum = a.mean(axis=3, dtype=np.float32)
    order = np.argsort(lum, axis=0)
    srt = np.take_along_axis(lum, order, axis=0)
    start = np.argmin(srt[k - 1:] - srt[:n - k + 1], axis=0)
    sel = np.take_along_axis(order, start[None] + np.arange(k)[:, None, None], axis=0)
    print(f"    static plate from {n} frames, tightest {k} per pixel", flush=True)
    return np.take_along_axis(a, sel[..., None], axis=0).mean(axis=0, dtype=np.float32)


def wall_field(plate, rect_in_win, donor_dx):
    """A smooth model of the wall's own brightness across the window.

    Fitted ONLY on wall that is verified clean for the whole take, which here
    means everything to camera-right of the fixture. Two things were tried first
    and both failed the same way. "Flat and neutral" as a test for wall is a
    trap: the static plate on the hair side is an average of a hundred positions
    of the same blonde, so it comes out perfectly smooth and only mildly warm,
    passes, and pins the model to hair, the patch then lands visibly warm and
    dark, off by 17 levels of blue. Requiring the boundary to be connected to
    the donor does not save it either, because the smear blends continuously
    into real wall and joins the same component.

    So the model gets a clean half of the picture and extrapolates the 142px
    across the fixture, which is a short reach for a quadratic. Whatever tilt it
    still gets wrong is measured and removed per frame; see Patcher."""
    h, w = plate.shape[:2]
    rx, ry, rw, rh = rect_in_win
    yy, xx = np.mgrid[0:h, 0:w]
    xs, ys = xx.astype(np.float32) / w, yy.astype(np.float32) / h

    g = plate.mean(axis=2)
    known = ((np.abs(g - box_blur(g, 6)) < 2.5)
             & (chroma(plate.astype(np.uint8)) < 16)
             & (np.abs(plate[:, :, 0] - plate[:, :, 2]) < 10))
    known[:, :rx + rw + 8] = False

    terms = [np.ones_like(xs), xs, ys, xs * xs, xs * ys, ys * ys]
    A = np.stack([t[known] for t in terms], axis=1)
    coef = np.stack([np.linalg.lstsq(A, plate[:, :, c][known], rcond=None)[0]
                     for c in range(3)], axis=1)
    field = np.einsum("hwk,kc->hwc", np.stack(terms, axis=2), coef).astype(np.float32)
    return field, int(known.sum()), float(np.std((plate - field)[known]))


class Patcher:
    """Borrows clean wall from the same frame and lays it over the fixture."""

    def __init__(self, field, rect_in_win, dx, margin=MARGIN):
        rx, ry, rw, rh = rect_in_win
        # Work on the fixture plus a margin: the margin is real wall, so it is
        # where the borrowed patch can be checked against the shot it has to
        # match.
        ox, oy = rx - margin, ry - margin
        ow, oh = rw + 2 * margin, rh + 2 * margin
        self.outer = (slice(oy, oy + oh), slice(ox, ox + ow))
        self.donor = (slice(oy, oy + oh), slice(ox + dx, ox + dx + ow))
        self.lift = field[self.outer] - field[self.donor]
        self.field = field[self.outer]
        self.wall = field[self.outer].mean(axis=2)

        # Where the fixture is, inside the outer window.
        m = np.zeros((oh, ow), np.float32)
        m[margin:margin + rh, margin:margin + rw] = 1.0
        self.window = box_blur(m, FEATHER)[:, :, None]
        # The margin ring, where the match is measured.
        self.ring = np.ones((oh, ow), bool)
        # Exclude the fixture AND a couple of px of its edge: leaving the edge
        # in biases the offset toward the bright thing being removed.
        self.ring[margin - 3:margin + rh + 3, margin - 3:margin + rw + 3] = False
        self.offset = None

    def __call__(self, bgr):
        out = bgr.astype(np.float32)
        here = out[self.outer]
        pred = out[self.donor] + self.lift

        wall_now = ((chroma(here.astype(np.uint8)) < 22)
                    & (self.wall - here.mean(axis=2) < DARK_LO))
        # Any model error left after the fit shows up as a constant difference
        # between the borrowed wall and the real wall around the fixture. It is
        # measured on every frame and removed, so a mis-fitted gradient cannot
        # leave a rectangle sitting a few levels off its own background.
        good = self.ring & wall_now & (chroma(out[self.donor].astype(np.uint8)) < 22)
        if good.sum() > 500:
            off = np.median((here - pred)[good], axis=0)
            self.offset = off if self.offset is None else self.offset * 0.85 + off * 0.15
        if self.offset is not None:
            pred = pred + self.offset

        hair = ramp(chroma(here.astype(np.uint8)), CHROMA_LO, CHROMA_HI)
        # A dark shirt or a shadow is in FRONT of the wall; the fixture is only
        # ever brighter than the wall, so this guard cannot suppress it.
        # Only a BIG dark region counts, and the size test is the whole point.
        # The guard exists for a shirt or an arm crossing the wall; the fixture's
        # own cast shadow is dark too, and protecting THAT leaves a shadow
        # hanging in mid-air after the thing casting it is gone. A shadow seam is
        # a dozen px tall and cannot survive an erosion this wide; a shirt is
        # hundreds and does not notice it.
        dark = ramp(ramp(self.wall - here.mean(axis=2), DARK_LO, DARK_HI), 0.5, 0.6)
        dark = ramp(box_blur(dark, DARK_ERODE), 0.80, 0.95)
        fg = np.clip(box_blur(np.maximum(hair, dark), SOFT) * 1.25, 0, 1)[:, :, None]

        m = self.window * (1.0 - fg)
        out[self.outer] = here * (1 - m) + pred * m
        return np.clip(out, 0, 255).astype(np.uint8)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out", nargs="?")
    ap.add_argument("--rect", required=True,
                    help="fixture bounding box in display px: X,Y,W,H")
    ap.add_argument("--donor", type=int, default=DONOR_DX,
                    help="px to camera-right to borrow clean wall from")
    ap.add_argument("--preview", type=int, metavar="FRAME", action="append",
                    help="write before/after stills for a frame and stop "
                         "(repeatable)")
    ap.add_argument("--stem", default="wall-clean",
                    help="path stem for --preview stills")
    ap.add_argument("--vt-q", type=int, default=72,
                    help="videotoolbox quality for the working copy (default 72)")
    ap.add_argument("--soft-decode", action="store_true")
    a = ap.parse_args()

    rx, ry, rw, rh = (int(v) for v in a.rect.split(","))
    W, H = display_size(a.src)
    x = max(0, rx - RING)
    y = max(0, ry - RING)
    w = min(W - x, rw + a.donor + 2 * RING)
    h = min(H - y, rh + 2 * RING)
    x, y, w, h = x // 2 * 2, y // 2 * 2, w // 2 * 2, h // 2 * 2
    rect_in_win = (rx - x, ry - y, rw, rh)
    if rect_in_win[0] + a.donor + rw > w:
        sys.exit("donor region falls outside the frame")

    fps = fps_of(a.src)
    total = frame_count(a.src, fps)
    hwdec = not a.soft_decode
    print(f"    source {W}x{H} @{fps:g}, {total} frames; fixture {rw}x{rh}+{rx}+{ry}, "
          f"donor +{a.donor}px, window {w}x{h}+{x}+{y}")

    plate = static_plate(a.src, x, y, w, h, total, hwdec)
    field, known_px, resid = wall_field(plate, rect_in_win, a.donor)
    print(f"    wall model from {known_px} verified px, residual {resid:.2f}")
    patch = Patcher(field, rect_in_win, a.donor)

    if a.preview:
        import cv2
        cap = cv2.VideoCapture(a.src)
        for f in a.preview:
            cap.set(cv2.CAP_PROP_POS_FRAMES, f)
            ok, fr = cap.read()
            if not ok:
                print(f"    could not read frame {f}", file=sys.stderr)
                continue
            win = fr[y:y + h, x:x + w]
            cv2.imwrite(f"{a.stem}-{f}-before.png", win[:, :rw + 2 * RING])
            cv2.imwrite(f"{a.stem}-{f}-after.png",
                        patch(win)[:, :rw + 2 * RING])
            print(f"    wrote {a.stem}-{f}-before.png / -after.png")
        cap.release()
        cv2.imwrite(f"{a.stem}-plate.png", plate.astype(np.uint8))
        cv2.imwrite(f"{a.stem}-field.png", field.astype(np.uint8))
        return 0

    if not a.out:
        sys.exit("an output path is required unless --preview is given")

    enc = subprocess.Popen(
        ["ffmpeg", "-nostdin", "-v", "error", "-y",
         *([] if a.soft_decode else ["-hwaccel", "videotoolbox"]),
         "-i", a.src,
         "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}",
         "-framerate", f"{fps}", "-i", "-",
         "-filter_complex", f"[0:v][1:v]overlay={x}:{y}:format=auto[v]",
         "-map", "[v]", "-map", "0:a?",
         "-c:v", "hevc_videotoolbox", "-q:v", str(a.vt_q), "-realtime", "0",
         "-tag:v", "hvc1", "-pix_fmt", "yuv420p",
         "-c:a", "copy", "-movflags", "+faststart", a.out],
        stdin=subprocess.PIPE)

    n = 0
    try:
        for win in stream_window(a.src, x, y, w, h, hwdec):
            enc.stdin.write(patch(win).tobytes())
            n += 1
            if n % 600 == 0:
                print(f"      {n}/{total} frames", flush=True)
    finally:
        enc.stdin.close()
        enc.wait()

    print(f"==> {n} frames patched -> {a.out}")
    return 0 if enc.returncode == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
