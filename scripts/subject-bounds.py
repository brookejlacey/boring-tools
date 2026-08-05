#!/usr/bin/env python3
"""subject-bounds.py: where she actually is in the frame, per cut.

Everything laid over the shot (brand cards, the app screen) has to clear her,
and by how much decides how big it is allowed to be. Both aspects need this and
they need different numbers:

  LANDSCAPE: her RIGHT EDGE. Everything goes in the wall to camera-right, so the
  column starts at the edge plus clearance. This was hand-measured once at 1438
  for the tightest cut; measuring every cut puts it at 1151, so the column had
  been sitting 286px too far right and every card was built at 360px inside a
  647px gap.

  PORTRAIT: her TOP. There is no wall (she spans the full width in every frame),
  so a card can only live in the headroom, and the headroom is not constant: it
  runs 256px on the widest cut down to 107px on the tightest. The badge was a
  fixed 380x92 at y=46 on every cut, which lands ON her hair for five of the
  nineteen cuts in the 60. Measuring lets the layout size the card to the
  headroom it actually has and skip the cuts where nothing legible fits.

MEASURING NAIVELY DOES NOT WORK, and the failure is worth recording. The room
falls off about 15 levels from the middle of the frame to the right edge (the
same falloff wall-clean.py models to borrow a clean plate). Compare each column
against a single wall reference and that gradient reads as subject, so the
detector reports her edge at the frame border on every cut. Compare against a
rolling baseline of the wall just beside it and the gradient cancels, because a
falloff is a drift and she is a step.

Two more things that are hers-but-not: the chair back sits in the bottom right
and her hands come up when she gestures, so the landscape scan stops at 0.80 of
frame height.

  scripts/subject-bounds.py CLIP.mp4 [--out FILE] [--samples 4]

Reads CLIP's sibling .timeline.tsv. Landscape writes subject-edge.tsv with a
`#max` line (the column is sized to the TIGHTEST cut: a card window routinely
spans a wide, a mid and a close, and a card that resizes under itself mid-hold
reads as a glitch). Portrait writes subject-top.tsv with a `#min` line and the
per-cut table, because portrait layout has to be decided per window.
"""
import argparse
import os
import statistics
import subprocess
import sys
import tempfile

from PIL import Image

RUN = 6          # consecutive deviating columns/rows before it counts as an edge
STEP = 14        # luma step that separates subject from the room's own drift
Y_BOT = 0.80     # landscape: excludes the chair back and her gesturing hands


def load_timeline(path):
    rows = []
    for line in open(path):
        if line.startswith("#") or not line.strip():
            continue
        f = line.rstrip("\n").split("\t")
        rows.append({"id": f[0], "out": float(f[1]), "dur": float(f[4]),
                     "frame": f[5], "label": f[6] if len(f) > 6 else ""})
    return rows


def grab(clip, t, tmp):
    p = os.path.join(tmp, "f.png")
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{t:.3f}",
                    "-i", clip, "-frames:v", "1", p, "-y"], check=True)
    return Image.open(p).convert("L")


def right_edge(im):
    W, H = im.size
    px = im.load()
    y0, y1 = int(H * 0.04), int(H * Y_BOT)
    med = {x: statistics.median(px[x, y] for y in range(y0, y1, 6))
           for x in range(W - 1, int(W * 0.30), -1)}
    baseline = statistics.median(med[x] for x in range(W - 40, W - 5))
    run = 0
    for x in range(W - 6, int(W * 0.30), -1):
        if abs(med[x] - baseline) > STEP:
            run += 1
            if run >= RUN:
                return x + RUN
        else:
            run = 0
            baseline = 0.88 * baseline + 0.12 * med[x]
    return int(W * 0.30)


def subject_top(im, band=None):
    """First row where she intrudes into a band, scanning down from the top.

    NOT the rolling-median used sideways, and the reason is worth keeping: a row
    median stays wall until she covers half the row, so on a portrait crop where
    her hair enters from the middle the median never trips and the detector
    reports the fallback for two thirds of the cuts. Count deviating pixels
    against the ceiling strip instead, which fires as soon as any of her is in
    the band.

    `band` is (x0, x1); default is the full width. The layout passes the box it
    actually wants to put a card in, because "how far down is she" has no single
    answer: at the top-right corner she is much further down than at centre.
    """
    W, H = im.size
    px = im.load()
    x0, x1 = band or (int(W * 0.06), int(W * 0.94))
    x0, x1 = max(0, x0), min(W, x1)
    cols = list(range(x0, x1, 6)) or [x0]
    ref = statistics.median(px[x, y] for x in cols for y in range(4, 34, 4))
    need = max(4, len(cols) // 6)
    for y in range(6, int(H * 0.55)):
        if sum(1 for x in cols if abs(px[x, y] - ref) > 26) >= need:
            return y
    return int(H * 0.55)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clip")
    ap.add_argument("--out")
    ap.add_argument("--samples", type=int, default=4)
    ap.add_argument("--band", metavar="X0,X1",
                    help="portrait: measure her top only inside this x band, "
                         "which is the box the card wants (default full width)")
    a = ap.parse_args()

    tl = os.path.splitext(a.clip)[0] + ".timeline.tsv"
    if not os.path.exists(tl):
        sys.exit(f"no timeline beside {a.clip}")

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "default=nw=1:nk=1", a.clip],
        capture_output=True, text=True).stdout.split()
    W, H = int(probe[0]), int(probe[1])
    portrait = H > W

    tmp = tempfile.mkdtemp()
    rows = load_timeline(tl)
    fracs = [(i + 0.5) / a.samples for i in range(a.samples)]
    here = os.path.dirname(os.path.abspath(a.clip))
    out = a.out or os.path.join(here, "subject-top.tsv" if portrait
                                else "subject-edge.tsv")

    band = None
    if a.band:
        band = tuple(int(v) for v in a.band.split(","))

    lines = ["# id\tframe\tlabel\t" + ("top_px" if portrait else "edge_px")]
    if portrait and band:
        lines.insert(0, f"# band\t{band[0]}\t{band[1]}")
    vals = []
    for r in rows:
        samples = [grab(a.clip, r["out"] + r["dur"] * f, tmp) for f in fracs]
        v = (min(subject_top(im, band) for im in samples) if portrait
             else max(right_edge(im) for im in samples))
        vals.append(v)
        lines.append(f"{r['id']}\t{r['frame']}\t{r['label']}\t{v}")
        print(f"{r['id']:>4} {r['frame']:>6} {r['label']:<32} {v:>6}",
              file=sys.stderr)

    tightest = min(vals) if portrait else max(vals)
    lines.append(f"{'#min' if portrait else '#max'}\t{tightest}")
    with open(out, "w") as fh:
        fh.write("\n".join(lines) + "\n")

    what = "min subject top" if portrait else "max subject edge"
    print(f"\n{what} {tightest} -> {out}", file=sys.stderr)
    print(tightest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
