#!/usr/bin/env python3
"""wall-graphics.py: brand cards in the empty wall beside her, cut to the beats.

The 2026-07-28 ad is a talking head against a plain grey wall with a third of
the 16:9 frame doing nothing. This fills it the way the creator edits do: small
cards that arrive on a line, hold, and leave, carrying the one fact that line is
making. It is a layer, not a redesign, the cut, the captions and the app b-roll
are untouched.

WHERE THEY GO IS MEASURED, NOT GUESSED. The subject's right-hand edge was
measured on every cut of the finished render: 1217px at the widest framing and
1438px at the tightest, so a column starting at 1512 clears the closest push-in
by 74px and never has to be nudged per cut.

VERTICAL GETS A DIFFERENT ANSWER, because it has no wall. Measured the same way,
she spans the full 1080 in every frame of every cut, and headroom is 92-210px
against platform chrome. There is no empty wall to put anything on, so 9:16 gets
a compact badge that sits over the shot rather than cards pretending to be on a
wall that is not in frame. Anything else would be covering her face.

SCHEDULING IS BY LABEL, not by timestamp. Cards are attached to cut labels from
the timeline, so the same plan renders correctly against the 15, the 30 and the
60 without three sets of hand-typed times, and survives a recut. Windows are
then subtracted against the b-roll plan: when the app screens take the frame, a
card floating over them is clutter, and a card that resumes afterwards is not.

Every claim on a card has to be true and checkable. The set here is the app's
own price and access terms, her own credential, and the product's own promise -
no install counts, no ratings, no numbers the product has not earned.

  scripts/wall-graphics.py CLIP.mp4 OUTDIR [--broll PLAN.tsv]

Reads CLIP's sibling .timeline.tsv. Writes br%06d.png sized to the clip, for
caption-burn.py --underlay.
"""
import argparse
import os
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT = "/System/Library/Fonts/Avenir Next.ttc"
FACE = {"regular": 7, "medium": 5, "demi": 2, "bold": 0, "heavy": 8}

# ---------------------------------------------------------------- BRAND CONFIG
# This whole block is the part you replace. Everything below it is mechanics.
# The values shipped here are the real ones from the ad set this tool was built
# for, kept as a worked example rather than blanked out, because a placeholder
# teaches you nothing about what a card wants to say.
#
# Override the two image paths with env vars so you do not have to edit code:
#   WALL_ICON=assets/app-icon.png WALL_WORDMARK=assets/wordmark.png

ICON = os.environ.get("WALL_ICON", "assets/app-icon.png")
WORDMARK = os.environ.get("WALL_WORDMARK", "assets/wordmark.png")

# Design tokens. Flat, no gradients: a gradient is the first thing video
# compression turns to mud, and a flat card reads at a phone's bitrate.
NAVY = (30, 42, 58)          # the authority colour, all type
CLAY = (196, 164, 132)       # the warm accent, chips and rules
WARM_WHITE = (250, 248, 245) # the card itself
MUTED = (108, 118, 132)      # second-line type

# Card copy. Every line here has to be a claim the product can actually back.
# No install counts, no ratings, no invented numbers. If you want a proof number
# on a card, it has to come from somewhere real first.
STAT_BIG = "5"
STAT_UNIT = "minutes"
STAT_SUB = "ONE PUZZLE, ONE ROUND"
CTA_HEAD = "First 3 free"
CTA_SUB = "no card required"
CTA_URL = "yourproduct.app"

IN_T, OUT_T = 0.34, 0.22      # seconds of entrance / exit
RISE = 20                     # px the card travels in on entrance


def ease_out(p):
    """The same curve as video/src/anim.tsx, so the layers agree."""
    return 1 - pow(1 - min(max(p, 0.0), 1.0), 3)


def font(weight, size):
    return ImageFont.truetype(FONT, size, index=FACE[weight])


def load_timeline(path):
    rows = []
    for line in open(path):
        if line.startswith("#") or not line.strip():
            continue
        f = line.rstrip("\n").split("\t")
        rows.append({"id": f[0], "out": float(f[1]), "dur": float(f[4]),
                     "frame": f[5], "label": f[6] if len(f) > 6 else ""})
    return rows


def read_edge_max(path, fallback=1438):
    """The measured subject edge, or the old hand-measured close-up if the
    measurement has not been run for this shoot yet."""
    try:
        for line in open(path):
            if line.startswith("#max"):
                return int(line.split("\t")[1])
    except Exception:
        pass
    return fallback


def load_broll(path):
    spans = []
    if not path:
        return spans
    for line in open(path):
        if line.startswith("#") or not line.strip():
            continue
        f = line.split("\t")
        spans.append((float(f[0]), float(f[1])))
    return spans


def subtract(window, spans):
    """Window minus the b-roll, which may leave two pieces or none."""
    out = [window]
    for s, e in spans:
        nxt = []
        for a, b in out:
            if e <= a or s >= b:
                nxt.append((a, b))
                continue
            if a < s:
                nxt.append((a, s))
            if e < b:
                nxt.append((e, b))
        out = nxt
    return [(a, b) for a, b in out if b - a > 0.9]


def rounded(size, radius, fill):
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1],
                                         radius=radius, fill=fill)
    return im


def with_shadow(card, radius, blur=26, dy=10, alpha=46, s=1.0):
    """A soft drop shadow is what makes a flat card sit ON the wall instead of
    floating in front of the lens. It is the one depth cue the brand's no-
    gradient rule leaves available. Scales with the card: a 647px card wearing a
    360px card's shadow reads as a sticker."""
    blur, dy = max(2, round(blur * s)), round(dy * s)
    pad = blur * 2
    out = Image.new("RGBA", (card.width + 2 * pad, card.height + 2 * pad + dy),
                    (0, 0, 0, 0))
    sh = Image.new("RGBA", out.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle(
        [pad, pad + dy, pad + card.width - 1, pad + dy + card.height - 1],
        radius=radius, fill=(20, 24, 32, alpha))
    out.alpha_composite(sh.filter(ImageFilter.GaussianBlur(blur / 2)))
    out.alpha_composite(card, (pad, pad))
    return out, (pad, pad)


def fit(im, w=None, h=None):
    if w:
        h = round(im.height * w / im.width)
    else:
        w = round(im.width * h / im.height)
    return im.resize((max(1, w), max(1, h)), Image.LANCZOS)


def icon_mark(px):
    """The App Store icon, rounded the way the platform rounds it. This is the
    mark people will actually be looking for, so it is the one the ad shows."""
    im = fit(Image.open(ICON).convert("RGBA"), w=px)
    mask = Image.new("L", (px, px), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, px - 1, px - 1],
                                           radius=round(px * 0.225), fill=255)
    im.putalpha(mask)
    return im


def wordmark(px_w):
    return fit(Image.open(WORDMARK).convert("RGBA"), w=px_w)


def tracked(draw, xy, text, f, fill, track=0):
    """Letterspaced small caps. Pillow has no tracking, and a label set solid at
    this size reads as a lump."""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=f, fill=fill)
        x += draw.textlength(ch, font=f) + track
    return x - xy[0] - track


def text_w(f, s, track=0):
    im = Image.new("RGBA", (1, 1))
    d = ImageDraw.Draw(im)
    return sum(d.textlength(c, font=f) for c in s) + track * max(0, len(s) - 1)


# ---------------------------------------------------------------- card art


# Every landscape card below was drawn at BASE_W and every vertical badge at
# BASE_W_COMPACT. Both are now scaled to whatever the measured wall actually
# allows rather than being fixed, so "make the cards bigger" is a property of
# the geometry and not a set of numbers to re-tune by hand. Proportions are
# preserved exactly: one scale factor multiplies every dimension, including the
# type, the padding, the corner radius and the shadow.
BASE_W = 360
BASE_W_COMPACT = 380


def scale_for(w, compact=False):
    return w / (BASE_W_COMPACT if compact else BASE_W)


def card_lockup(w, compact=False):
    s = scale_for(w, compact)
    P = lambda n: max(1, round(n * s))
    if compact:
        h = P(92)
        card = rounded((w, h), P(20), WARM_WHITE + (255,))
        d = ImageDraw.Draw(card)
        ic = icon_mark(P(56))
        card.alpha_composite(ic, (P(20), (h - P(56)) // 2))
        wm = wordmark(P(126))
        card.alpha_composite(wm, (P(94), (h - wm.height) // 2 - P(2)))
        f = font("medium", P(19))
        d.text((P(232), (h - P(24)) // 2), "5 min", font=f, fill=MUTED)
        return card, P(20)

    h = P(140)
    card = rounded((w, h), P(24), WARM_WHITE + (255,))
    d = ImageDraw.Draw(card)
    ic = icon_mark(P(88))
    card.alpha_composite(ic, (P(26), P(26)))
    wm = wordmark(P(150))
    card.alpha_composite(wm, (P(138), P(40)))
    tracked(d, (P(140), P(88)), "FIVE MINUTES A DAY", font("demi", P(17)),
            MUTED, 1.6 * s)
    return card, P(24)


def card_stat(w, compact=False):
    s = scale_for(w, compact)
    P = lambda n: max(1, round(n * s))
    h = P(196)
    card = rounded((w, h), P(24), WARM_WHITE + (255,))
    d = ImageDraw.Draw(card)
    big = font("bold", P(118))
    d.text((P(30), P(18)), STAT_BIG, font=big, fill=NAVY)
    nx = P(30) + d.textlength(STAT_BIG, font=big) + P(14)
    d.text((nx, P(66)), STAT_UNIT, font=font("medium", P(44)), fill=NAVY)
    d.line([(P(30), P(152)), (w - P(30), P(152))], fill=CLAY + (255,), width=P(2))
    tracked(d, (P(30), P(164)), STAT_SUB, font("demi", P(17)),
            MUTED, 1.6 * s)
    return card, P(24)


def card_chip(w, label, compact=False):
    s = scale_for(w, compact)
    P = lambda n: max(1, round(n * s))
    h = P(68)
    card = rounded((w, h), P(34), CLAY + (255,))
    d = ImageDraw.Draw(card)
    f = font("demi", P(21))
    tw = text_w(f, label, 2.2 * s)
    tracked(d, ((w - tw) / 2, (h - P(28)) / 2), label, f, NAVY, 2.2 * s)
    return card, P(34)


def card_cta(w, compact=False):
    s = scale_for(w, compact)
    P = lambda n: max(1, round(n * s))
    if compact:
        h = P(120)
        card = rounded((w, h), P(22), WARM_WHITE + (255,))
        d = ImageDraw.Draw(card)
        ic = icon_mark(P(64))
        card.alpha_composite(ic, (P(20), P(28)))
        d.text((P(100), P(24)), CTA_HEAD, font=font("demi", P(32)), fill=NAVY)
        d.text((P(100), P(66)), f"{CTA_SUB.split()[0]} card · {CTA_URL}",
               font=font("medium", P(24)), fill=MUTED)
        return card, P(22)

    h = P(288)
    card = rounded((w, h), P(26), WARM_WHITE + (255,))
    d = ImageDraw.Draw(card)
    ic = icon_mark(P(72))
    card.alpha_composite(ic, (P(28), P(26)))
    wm = wordmark(P(120))
    card.alpha_composite(wm, (P(118), P(44)))
    d.line([(P(28), P(124)), (w - P(28), P(124))], fill=CLAY + (255,), width=P(2))
    d.text((P(28), P(142)), CTA_HEAD, font=font("bold", P(42)), fill=NAVY)
    d.text((P(28), P(196)), CTA_SUB, font=font("medium", P(24)), fill=MUTED)
    d.text((P(28), P(232)), CTA_URL, font=font("demi", P(34)), fill=NAVY)
    return card, P(26)


# ---------------------------------------------------------------- scheduling

# Which line earns which card. Keyed on the cut label so one plan serves the 15,
# the 30 and the 60, and so a recut carries the graphics with it.
PLAN = [
    ("25-years-nobody-taught-you", "chip", "25 YEARS IN TECH", 0.0),
    ("five-minutes-fixes-that", "stat", None, 0.7),
    ("app-i-built-called-jayla", "lockup", None, None),
    ("first-three-free", "cta", None, None),
    ("first-three-free-jaylaapp", "cta", None, None),
]
# A card with None for its tail runs until the next card takes over, or to the end.


def schedule(timeline, broll):
    starts = {}
    for row in timeline:
        starts.setdefault(row["label"], row)
    total = sum(r["dur"] for r in timeline)

    picked = []
    for label, kind, payload, tail in PLAN:
        row = starts.get(label)
        if row:
            picked.append({"t": row["out"], "kind": kind, "payload": payload,
                           "tail": tail, "dur": row["dur"]})
    picked.sort(key=lambda c: c["t"])

    cards = []
    for i, c in enumerate(picked):
        if c["tail"] is None:
            end = picked[i + 1]["t"] if i + 1 < len(picked) else total
        else:
            end = c["t"] + c["dur"] + c["tail"]
            if i + 1 < len(picked):
                end = min(end, picked[i + 1]["t"])
        for a, b in subtract((c["t"], end), broll):
            cards.append({"a": a, "b": b, "kind": c["kind"],
                          "payload": c["payload"]})
    return cards


CLEAR = 74        # px of wall left between her edge and the column
MARGIN_R = 48     # px between the column and the frame edge
CARD_TOP = 150    # cards hang from a common top across every beat

# 9:16 has no wall, so a card can only use headroom, and the headroom is not a
# constant: measured per cut it runs 241px on the widest down to 115px on the
# tightest (scripts/subject-bounds.py --band). A fixed badge therefore sits on
# her hair for five of the nineteen cuts in the 60, which it did until now.
PORT_TOP = 46          # headroom cards hang from here
PORT_GAP = 16          # air between the card's bottom and her hair
PORT_MIN_H = 76        # under this nothing legible fits, so the card is dropped
PORT_MAX_W = 0.86      # of frame width
PORT_MARGIN = 36
# The end card is the exception. It is the most important card in the ad and it
# lands on the tightest cuts of all ("see you in there" leaves 117px), so it
# does not compete for headroom: it sits in the lower third over her chest,
# above the caption band, which is where an end card belongs anyway.
# Placed from its BOTTOM, not its top: sized to the full width it is allowed,
# the end card stands 293px tall, so hanging it from a fixed top ran it straight
# through the burned caption band at 0.72.
PORT_CTA_BOTTOM = 0.66
# A 196px-tall stat card shrunk into 140px of headroom is 271px wide and its
# second line is unreadable. The same claim as a single-line chip is 780px wide
# and legible, so portrait swaps it rather than shipping type nobody can read.
PORT_SUBSTITUTE = {"stat": ("chip", "5 MINUTES A DAY")}
DESIGN_H = {"lockup": 92, "cta": 120, "chip": 68, "stat": 196}


def column_for(W, edge):
    """The wall column: everything to camera-right of her, minus clearance.

    Derived, never typed. The hand-measured 1512 was built off a 1438 close-up
    edge; measuring every cut (scripts/subject-edge.py) puts the tightest at
    1151, so the column starts at 1225 and cards are 647 wide instead of 360.
    One column sized to the TIGHTEST cut, not per-cut geometry: a card window
    routinely spans a wide, a mid and a close, and a card that resizes under
    itself mid-hold reads as a glitch.
    """
    x0 = edge + CLEAR
    return x0, max(180, W - x0 - MARGIN_R)


def read_tops(path):
    """Per-cut subject top for portrait, keyed on cut id."""
    tops = {}
    try:
        for line in open(path):
            if line.startswith("#") or not line.strip():
                continue
            f = line.rstrip("\n").split("\t")
            tops[f[0]] = int(f[3])
    except Exception:
        return {}
    return tops


def fit_portrait(cards, timeline, tops, W, H):
    """Place 9:16 cards in the headroom they actually have, per window.

    Splits each card window on cut boundaries, drops the pieces where she is too
    high for anything legible, and sizes what survives to the tightest piece it
    still covers. One size per window, so a card never resizes under itself
    mid-hold; different windows may differ, which is honest because the headroom
    genuinely differs.
    """
    out = []
    for c in cards:
        kind, payload = c["kind"], c["payload"]
        if kind in PORT_SUBSTITUTE:
            kind, payload = PORT_SUBSTITUTE[kind]

        if kind == "cta":
            w = round(W * PORT_MAX_W)
            h = round(DESIGN_H["cta"] * w / BASE_W_COMPACT)
            out.append({**c, "kind": kind, "payload": payload, "w": w,
                        "y": round(H * PORT_CTA_BOTTOM) - h})
            continue

        pieces, avails = [], []
        for r in timeline:
            a, b = max(c["a"], r["out"]), min(c["b"], r["out"] + r["dur"])
            if b - a <= 0.25:
                continue
            top = tops.get(r["id"])
            avail = (top - PORT_TOP - PORT_GAP) if top else PORT_MIN_H
            if avail < PORT_MIN_H:
                continue
            if pieces and abs(pieces[-1][1] - a) < 0.05:
                pieces[-1][1] = b
            else:
                pieces.append([a, b])
            avails.append(avail)
        if not pieces:
            continue

        h = min(avails)
        w = min(round(W * PORT_MAX_W),
                round(BASE_W_COMPACT * h / DESIGN_H.get(kind, 92)))
        for a, b in pieces:
            if b - a < 0.5:
                continue
            out.append({**c, "kind": kind, "payload": payload,
                        "a": a, "b": b, "w": w, "y": PORT_TOP})
    return out


def build(card_kind, payload, W, H, edge=None, w=None, y=None):
    vertical = H > W
    if vertical:
        w = w or BASE_W_COMPACT
        if card_kind == "lockup":
            art, r = card_lockup(w, compact=True)
        elif card_kind == "cta":
            art, r = card_cta(w, compact=True)
        elif card_kind == "chip":
            art, r = card_chip(w, payload, compact=True)
        else:
            art, r = card_stat(w, compact=True)
        return art, r, (W - w - PORT_MARGIN, y if y is not None else PORT_TOP), \
            scale_for(w, True)

    x0, w = column_for(W, edge)
    if card_kind == "lockup":
        art, r = card_lockup(w)
    elif card_kind == "cta":
        art, r = card_cta(w)
    elif card_kind == "chip":
        art, r = card_chip(w, payload)
    else:
        art, r = card_stat(w)
    return art, r, (x0, CARD_TOP), scale_for(w)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clip")
    ap.add_argument("outdir")
    ap.add_argument("--broll", help="b-roll plan; card windows are cut around it")
    ap.add_argument("--edge", type=int,
                    help="subject right edge in px; default reads subject-edge.tsv "
                         "beside the b-roll plan, else the old hand-measured 1438")
    ap.add_argument("--preview", type=float, metavar="T",
                    help="write one PNG at time T and stop")
    a = ap.parse_args()

    import subprocess
    def probe(fields):
        return subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", f"stream={fields}", "-of", "default=nw=1:nk=1", a.clip],
            capture_output=True, text=True).stdout.strip().split("\n")

    W, H = (int(v) for v in probe("width,height")[:2])
    num, _, den = probe("r_frame_rate")[0].partition("/")
    fps = float(num) / float(den or 1)
    frames = int(probe("nb_frames")[0])

    tl = os.path.splitext(a.clip)[0] + ".timeline.tsv"
    if not os.path.exists(tl):
        sys.exit(f"no timeline beside {a.clip}")
    here = os.path.dirname(os.path.abspath(a.broll or a.clip))
    edge = a.edge
    if edge is None and H < W:
        edge = read_edge_max(os.path.join(here, "subject-edge.tsv"))

    timeline = load_timeline(tl)
    cards = schedule(timeline, load_broll(a.broll))
    if H > W:
        tops = read_tops(os.path.join(here, "subject-top.tsv"))
        if not tops:
            print("    no subject-top.tsv; portrait cards fall back to the "
                  "fixed badge, which may sit on her hair", file=sys.stderr)
        cards = fit_portrait(cards, timeline, tops, W, H)
    for c in cards:
        c["art"], c["radius"], c["at"], s = build(
            c["kind"], c["payload"], W, H, edge, c.get("w"), c.get("y"))
        c["shadow"], c["off"] = with_shadow(c["art"], c["radius"], s=s)
    if H < W:
        x0, cw = column_for(W, edge)
        print(f"    column x{x0} w{cw} (subject edge {edge})")
    print(f"    {len(cards)} card window(s): "
          + ", ".join(f"{c['kind']} {c['a']:.1f}-{c['b']:.1f}s" for c in cards))

    os.makedirs(a.outdir, exist_ok=True)

    def frame_at(t):
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        drew = False
        for c in cards:
            if not (c["a"] - IN_T * 0 <= t < c["b"] + OUT_T):
                continue
            if t < c["b"]:
                p = ease_out((t - c["a"]) / IN_T)
                alpha, dy = p, (1 - p) * RISE
            else:
                p = ease_out((t - c["b"]) / OUT_T)
                alpha, dy = 1 - p, -p * (RISE * 0.5)
            if alpha <= 0.004:
                continue
            art = c["shadow"]
            if alpha < 0.999:
                art = art.copy()
                art.putalpha(art.getchannel("A").point(lambda v: int(v * alpha)))
            x = c["at"][0] - c["off"][0]
            y = c["at"][1] - c["off"][1] + round(dy)
            layer.alpha_composite(art, (x, y))
            drew = True
        return layer, drew

    if a.preview is not None:
        layer, _ = frame_at(a.preview)
        layer.save(os.path.join(a.outdir, "preview.png"))
        print(f"    wrote {a.outdir}/preview.png")
        return 0

    # Most frames of this layer are empty. Encode one transparent PNG and hard
    # link the rest to it, the same trick broll-screens.py uses: the sequence
    # still needs contiguous numbering, and nothing is gained by re-compressing
    # the same blank a thousand times.
    blank = os.path.join(a.outdir, "_blank.png")
    Image.new("RGBA", (W, H), (0, 0, 0, 0)).save(blank, compress_level=1)

    written = 0
    for i in range(frames):
        path = os.path.join(a.outdir, f"br{i:06d}.png")
        if os.path.exists(path):
            os.unlink(path)
        layer, drew = frame_at(i / fps)
        if not drew:
            os.link(blank, path)
            continue
        layer.save(path, optimize=False, compress_level=1)
        written += 1

    print(f"    graphics: {written}/{frames} frames carry a card")
    return 0


if __name__ == "__main__":
    sys.exit(main())
