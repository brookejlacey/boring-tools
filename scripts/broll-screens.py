#!/usr/bin/env python3
"""broll-screens.py: app screens as b-roll, cut to the words that describe them.

A founder saying "one small puzzle off a real work situation" over her own face
is an assertion. The same line over the actual exercise screen is a
demonstration, and the 2026-07-25 push plan asks for the app to be visible
inside the ad rather than only named.

Renders a PNG sequence sized to the target video, transparent everywhere a
screen is not, so it composites under the captions in a single pass. That keeps
the whole thing to one extra encode generation rather than one per layer.

TWO MODES, because the two aspects are not the same problem.

  9:16 CUTAWAY (default). She spans the full frame width, so there is nowhere
  to put a screen except over her. The screen takes the frame on a dark backdrop
  and reads as a cutaway.

  16:9 COLUMN (--column X0,W). A portrait phone centred in a landscape frame
  leaves roughly three quarters of that frame flat black, for ten seconds of a
  fifty-four second ad, which is a dead frame and not a cutaway. In landscape
  the screen goes in the same wall column as the brand cards, beside her head,
  so she stays on camera while the app is shown. The column is measured, not
  guessed: see scripts/subject-edge.py. This also dissolves a scheduling
  problem rather than working around it. Card windows are subtracted against
  the b-roll, which used to mean a screen punched a hole in the card run; now
  it means the column swaps from a card to a phone and back, which is the
  format doing its job.

Screens push in from the right the way a real navigation does, which is what
sells it as a screen recording rather than a slideshow, and each screen drifts
slowly while it sits so nothing on screen is ever frozen.

  ~/.venvs/rvm/bin/python scripts/broll-screens.py PLAN.tsv W H FPS FRAMES OUTDIR
      [--column X0,W]

PLAN.tsv: out_start<TAB>out_end<TAB>image  (times in output seconds)
"""
import argparse
import os
import sys

from PIL import Image, ImageDraw, ImageFilter

# The paper the screens sit on in cutaway mode. Deliberately not white: a white
# card behind a white app screen loses the device edge entirely.
BACKDROP = (18, 20, 24, 255)
SLIDE = 0.28          # seconds of push-in at the head of each screen
DRIFT = 0.045         # fraction of screen height it travels while held
MARGIN = 0.94         # of frame height the screen occupies in cutaway mode
COL_H = 0.80          # of frame height in column mode: clears the chair back
COL_TOP = 0.10        # where the phone sits vertically in column mode
RADIUS = 0.043        # corner radius as a fraction of phone width
SHADOW_BLUR = 30
SHADOW_DY = 12
SHADOW_A = 92


def load_plan(path):
    rows = []
    for line in open(path):
        if line.startswith("#") or not line.strip():
            continue
        f = line.rstrip("\n").split("\t")
        rows.append((float(f[0]), float(f[1]), f[2]))
    return rows


def ease(x):
    """Fast out, settle in. Linear reads mechanical on a short push."""
    return 1 - pow(1 - x, 3)


def rounded_device(shot, radius):
    """Round the phone grab's corners and hang a shadow on it, the same depth
    cue the brand cards use, so a screen in the wall reads as an object on the
    wall rather than a rectangle pasted over the room."""
    mask = Image.new("L", shot.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, shot.width - 1, shot.height - 1], radius=radius, fill=255)
    dev = shot.copy()
    dev.putalpha(mask)

    pad = SHADOW_BLUR * 2
    out = Image.new("RGBA", (dev.width + 2 * pad, dev.height + 2 * pad + SHADOW_DY),
                    (0, 0, 0, 0))
    sh = Image.new("RGBA", out.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle(
        [pad, pad + SHADOW_DY, pad + dev.width - 1, pad + SHADOW_DY + dev.height - 1],
        radius=radius, fill=(14, 18, 26, SHADOW_A))
    out.alpha_composite(sh.filter(ImageFilter.GaussianBlur(SHADOW_BLUR / 2)))
    out.alpha_composite(dev, (pad, pad))
    return out, pad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("width", type=int)
    ap.add_argument("height", type=int)
    ap.add_argument("fps", type=float)
    ap.add_argument("frames", type=int)
    ap.add_argument("outdir")
    ap.add_argument("--column", metavar="X0,W",
                    help="landscape: place the phone in the wall column at x0 "
                         "with that width, transparent elsewhere")
    a = ap.parse_args()

    W, H, fps = a.width, a.height, a.fps
    os.makedirs(a.outdir, exist_ok=True)
    plan = load_plan(a.plan)

    col = None
    if a.column:
        cx0, cw = (int(v) for v in a.column.split(","))
        col = (cx0, cw)

    # Pre-scale every screen once. They are 1290x2796 phone grabs, far taller
    # than either output, so height is the binding dimension in both modes.
    cache = {}
    for _, _, img in plan:
        if img in cache:
            continue
        im = Image.open(img).convert("RGBA")
        if col:
            # Height-bound inside the column: a 647px-wide phone would stand
            # 1400px tall in a 1080 frame.
            target_h = int(H * COL_H)
            scale = target_h / im.height
            w = max(2, int(im.width * scale))
            if w > col[1]:
                scale = col[1] / im.width
                w, target_h = col[1], max(2, int(im.height * scale))
            shot = im.resize((w, target_h), Image.LANCZOS)
            cache[img] = rounded_device(shot, round(w * RADIUS))
        else:
            target_h = int(H * MARGIN * (1 + DRIFT))
            scale = target_h / im.height
            cache[img] = im.resize((max(2, int(im.width * scale)), target_h),
                                   Image.LANCZOS)

    # Most frames of a b-roll layer are empty. Encode one transparent PNG and
    # hard-link the rest to it: the sequence still needs contiguous numbering
    # for ffmpeg, but nothing is gained by re-compressing the same blank 1500
    # times, and on a 1080x1920 layer that is most of the runtime.
    blank_path = os.path.join(a.outdir, "_blank.png")
    Image.new("RGBA", (W, H), (0, 0, 0, 0)).save(blank_path, compress_level=1)

    written = 0
    for fi in range(a.frames):
        t = fi / fps
        cur = None
        for s, e, img in plan:
            if s <= t < e:
                cur = (s, e, img)
                break
        path = os.path.join(a.outdir, f"br{fi:06d}.png")
        if cur is None:
            os.link(blank_path, path)
            continue

        s, e, img = cur
        p = min(1.0, (t - s) / SLIDE) if SLIDE > 0 else 1.0

        if col:
            dev, pad = cache[img]
            cx0, cw = col
            # Centred in the column, pushing in from the frame edge. Nothing
            # else in the frame moves, so the layer stays transparent.
            frame = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            x = int(cx0 + (cw - (dev.width - 2 * pad)) / 2 - pad
                    + (1 - ease(p)) * (W - cx0))
            top = int(H * COL_TOP) - pad
            frame.alpha_composite(dev, (x, top))
            frame.save(path, optimize=False, compress_level=1)
            written += 1
            continue

        shot = cache[img]
        frame = Image.new("RGBA", (W, H), BACKDROP)

        held = (t - s) / max(1e-6, (e - s))
        y_travel = shot.height - int(H * MARGIN)
        # Cutaway: sit the screen centred and let it drift upward as it holds,
        # which reads as a slow scroll.
        top = int((H - H * MARGIN) / 2) - int(y_travel * held)
        x = int((W - shot.width) / 2 + (1 - ease(p)) * W * 0.55)

        frame.alpha_composite(shot, (x, top))
        frame.save(path, optimize=False, compress_level=1)
        written += 1

    print(f"    b-roll: {written}/{a.frames} frames carry a screen", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
