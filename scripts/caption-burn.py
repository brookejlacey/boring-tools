#!/usr/bin/env python3
"""caption-burn.py: word-by-word burned-in captions, the CapCut look.

platforms/tiktok.md calls burned captions mandatory: 85% of the platform is
watched muted, so an uncaptioned talking-head ad is an ad most people never hear.

WHY THIS IS HAND-ROLLED, since "adopt before you build" says it should not be.
Every off-the-shelf caption burner (captacity, auto-subtitle, the CapCut-alikes)
ends in `ffmpeg -vf subtitles=` or `drawtext=`. This machine's ffmpeg is the
Homebrew 8.x build, which ships with neither, and Homebrew has slimmed the
formula so libass is not a dependency at any version. Every adopt candidate
fails on the same missing dependency, and a source build of ffmpeg is a long
CPU-pinning compile. What is left to build is exactly the renderer: Pillow draws
the frames, ffmpeg's `overlay` composites them, and both of those are present.

Timings come from the render's own .timeline.tsv rather than being re-derived,
so captions cannot drift out of step with the cut. Words are timed per cut, on
short dense audio, which is where whisper's word timestamps are trustworthy.

  ~/.venvs/rvm/bin/python scripts/caption-burn.py CLIP.mp4 SOURCE.MOV [--out OUT.mp4]

Reads CLIP's sibling .timeline.tsv. Writes OUT (default: CLIP with -cc suffix).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFont

FONT = "/System/Library/Fonts/Supplemental/Arial Black.ttf"
MODEL = os.path.expanduser("~/.cache/whisper/ggml-small.en.bin")

# Caption band sits above the platform furniture. TikTok's own chrome eats the
# bottom fifth, so nothing lands below 0.80 of frame height.
BASELINE = 0.72
MAX_LINE_FRAC = 0.86      # of frame width
WORDS_PER_GROUP = 1       # how many words are on screen at once
BRIDGE = 0.28             # gap under this and a word holds until the next one

# One word at a time, smaller, white with a black outline. Three words at 0.042
# of frame height took a band most of the way across the frame and sat on top of
# whatever was behind it; one word at 0.032 is a mark rather than a bar.
#
# The yellow went with it, and not only because it was asked for: the highlight
# existed to say WHICH of the three words is being spoken right now. At one word
# per group every word on screen is the live word, so the colour was carrying no
# information and was just the loudest thing in the frame.
TYPE_FRAC = 0.032         # of frame height
STROKE_FRAC = 0.12        # of type size
INK = (255, 255, 255, 255)
OUTLINE = (0, 0, 0, 255)


def probe(src, stream, fields):
    return subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", stream,
         "-show_entries", f"stream={fields}", "-of", "default=nw=1:nk=1", src],
        capture_output=True, text=True).stdout.strip().split("\n")


def load_timeline(path):
    rows = []
    for line in open(path):
        if line.startswith("#") or not line.strip():
            continue
        f = line.rstrip("\n").split("\t")
        rows.append({"out": float(f[1]), "src": float(f[2]),
                     "frames": int(f[3]), "dur": float(f[4])})
    return rows


def words_for(src, start, dur, work, tag):
    """Word timings for one cut, relative to the cut's own start.

    Transcribed per cut rather than across the whole take: whisper's word
    timestamps smear across long silences, and a take with three minutes of
    resets in it is exactly the case that breaks them. A cut is short and dense,
    which is where they hold.
    """
    # Fail loudly on a missing model. whisper-cli exits 3 and writes no json,
    # capture_output swallows the reason, and the empty word list that follows
    # renders as a clean video with no captions on it. A silent no-op is the
    # worst failure this tool has, so it is the one thing checked by hand.
    if not os.path.exists(MODEL):
        sys.exit(f"caption-burn: no whisper model at {MODEL}\n"
                 f"  mkdir -p {os.path.dirname(MODEL)}\n"
                 f"  curl -L -o {MODEL} \\\n"
                 f"    https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin")
    wav = os.path.join(work, f"{tag}.wav")
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{start:.6f}",
                    "-t", f"{dur:.6f}", "-i", src, "-vn", "-ac", "1", "-ar", "16000",
                    "-c:a", "pcm_s16le", wav], check=True)
    base = os.path.join(work, tag)
    subprocess.run(["whisper-cli", "-m", MODEL, "-f", wav, "-ml", "1", "-sow",
                    "-ojf", "-dtw", "small.en", "-of", base, "-np"],
                   capture_output=True)
    jf = base + ".json"
    if not os.path.exists(jf):
        return []
    out = []
    for s in json.load(open(jf))["transcription"]:
        raw = s["text"].strip()
        if not raw or raw.startswith("["):
            continue
        # Terminal punctuation decides where a caption group may break, then it
        # is dropped from the display. On-screen captions do not carry sentence
        # punctuation, but a period is the best sentence signal available.
        # Strip only what TRAILS: an internal dot is load-bearing, and a blanket
        # strip turns jayla.app into JAYLAAPP.
        brk = bool(re.search(r"[.!?]+[\"')\]]*$", raw))
        text = re.sub(r"[.!?,;:]+[\"')\]]*$", "", raw.strip())
        text = re.sub(r"[^\w'$%.\-]", "", text).upper()
        a, b = s["offsets"]["from"] / 1000.0, s["offsets"]["to"] / 1000.0
        if text:
            out.append({"t": text, "a": min(a, dur), "b": min(b, dur), "brk": brk})
    return out


def load_corrections(path):
    pairs = []
    if not path or not os.path.exists(path):
        return pairs
    for line in open(path):
        line = line.split("#")[0].rstrip("\n")
        if not line.strip() or "\t" not in line:
            continue
        wrong, right = [p.strip() for p in line.split("\t", 1)]
        w, r = wrong.upper().split(), right.upper().split()
        if not w or len(w) != len(r):
            print(f"# skipping uneven correction: {wrong!r} -> {right!r}",
                  file=sys.stderr)
            continue
        pairs.append((w, r))
    # Longest first, so "SMILE OR NOD BOYS" wins over "SMILE OR NOD".
    return sorted(pairs, key=lambda p: -len(p[0]))


def apply_corrections(words, pairs):
    """Rewrite known mishearings in place, keeping each word's own timing."""
    fixed = 0
    for wrong, right in pairs:
        n = len(wrong)
        for i in range(len(words) - n + 1):
            if [w["t"] for w in words[i:i + n]] == wrong:
                for w, new in zip(words[i:i + n], right):
                    w["t"] = new
                fixed += 1
    return fixed


def group(words, n):
    """Short phrases that break where the speech breaks.

    Chunking every n words regardless of the sentence puts "an app I" on screen
    and then "built called Jayla", which reads as a machine cutting a transcript
    rather than as someone talking. Break on sentence ends and real pauses
    first, then split anything still too long into BALANCED chunks, so a seven
    word sentence goes 3+2+2 rather than 3+3+1.
    """
    sentences, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        gap = words[i + 1]["a"] - w["b"] if i + 1 < len(words) else 0
        if w["brk"] or gap > 0.45 or i == len(words) - 1:
            sentences.append(cur)
            cur = []

    out = []
    for s in sentences:
        if len(s) <= n:
            out.append(s)
            continue
        parts = -(-len(s) // n)                 # ceil, the fewest chunks that fit
        base, extra = divmod(len(s), parts)
        i = 0
        for k in range(parts):
            size = base + (1 if k < extra else 0)
            out.append(s[i:i + size])
            i += size
    return out


def render_frames(groups, W, H, fps, total_frames, work):
    """Pre-lay every group, then draw. Layout is fixed per group, not per frame.

    Groups are shrunk to fit before they are allowed to wrap. A three-word
    phrase broken across two lines because it was four pixels too wide looks
    like a bug; the same phrase at 94% size looks intentional, and at these
    sizes the difference is invisible. Only when shrinking runs out does it
    wrap.
    """
    nominal = int(H * TYPE_FRAC)
    floor = int(nominal * 0.72)
    scratch = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    fonts = {s: ImageFont.truetype(FONT, s) for s in range(floor, nominal + 1)}
    limit = W * MAX_LINE_FRAC

    def measure(row, font):
        widths = [scratch.textlength(w["t"], font=font) for w in row]
        return widths, sum(widths) + int(font.size * 0.30) * (len(row) - 1)

    def fit(row):
        for s in range(nominal, floor - 1, -1):
            widths, total = measure(row, fonts[s])
            if total <= limit:
                return s, widths, total
        widths, total = measure(row, fonts[floor])
        return floor, widths, total

    laid = []
    for g in groups:
        size, widths, total = fit(g)
        if total > limit and len(g) > 1:
            half = (len(g) + 1) // 2
            rows = [g[:half], g[half:]]
        else:
            rows = [g]
        plan = []
        for row in rows:
            s, widths, total = fit(row)
            plan.append((row, s, widths, total))
        laid.append(plan)

    # One word per group blinks off in every gap between words, and the gaps
    # inside a phrase are 30-80ms, which strobes. Hold each word until the next
    # one arrives when the gap is short, so the caption SWAPS instead of
    # flashing; a real pause still clears the frame.
    spans = []
    for gi, g in enumerate(groups):
        end = g[-1]["b"] + 0.10
        if gi + 1 < len(groups):
            nxt = groups[gi + 1][0]["a"]
            end = nxt if nxt - g[-1]["b"] < BRIDGE else min(end, nxt)
        spans.append((g[0]["a"], end))

    blank_path = os.path.join(work, "_ccblank.png")
    Image.new("RGBA", (W, H), (0, 0, 0, 0)).save(blank_path, compress_level=1)

    written = 0
    for fi in range(total_frames):
        t = fi / fps
        active = None
        for gi, (gs, ge) in enumerate(spans):
            if gs <= t < ge:
                active = gi
                break
        path = os.path.join(work, f"cc{fi:06d}.png")
        if active is None:
            os.link(blank_path, path)
            continue
        im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        plan = laid[active]
        line_h = max(p[1] for p in plan) * 1.22
        y0 = H * BASELINE - line_h * (len(plan) - 1) / 2
        for ri, (row, size, widths, total) in enumerate(plan):
            font = fonts[size]
            pad = int(size * 0.30)
            stroke = max(3, round(size * STROKE_FRAC))
            x = (W - total) / 2
            y = y0 + ri * line_h
            for w, wd in zip(row, widths):
                d.text((x, y), w["t"], font=font, fill=INK,
                       stroke_width=stroke, stroke_fill=OUTLINE, anchor="lt")
                x += wd + pad
        im.save(path, compress_level=1)
        written += 1
    return written


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clip")
    ap.add_argument("source")
    ap.add_argument("--out")
    ap.add_argument("--underlay", action="append", default=[],
                    help="PNG sequence (br%%06d.png) composited BENEATH the "
                         "captions in the same pass, for app b-roll. Doing it "
                         "here rather than as its own render keeps the whole "
                         "job to one extra encode generation instead of two. "
                         "Repeatable; layers composite in the order given, so "
                         "adding the wall graphics costs no further generation.")
    ap.add_argument("--baseline", type=float, default=BASELINE)
    ap.add_argument("--corrections",
                    default=os.path.join(os.path.dirname(os.path.dirname(
                        os.path.abspath(__file__))), "voice", "caption-corrections.tsv"))
    # Higher than the assembler's 65 on purpose. This pass re-encodes an already
    # compressed picture, so it pays a second generation; measured on the 15,
    # q65 holds SSIM 0.9918 against the uncaptioned original, q72 holds 0.9945
    # for 9MB more, and q80 only reaches 0.9959 for another 12MB.
    ap.add_argument("--vt-q", type=int, default=72)
    a = ap.parse_args()

    globals()["BASELINE"] = a.baseline
    tl_path = os.path.splitext(a.clip)[0] + ".timeline.tsv"
    if not os.path.exists(tl_path):
        print(f"no timeline beside {a.clip}; re-render with assemble-cuts.py",
              file=sys.stderr)
        return 1
    timeline = load_timeline(tl_path)

    W, H = (int(x) for x in probe(a.clip, "v:0", "width,height")[:2])
    num, _, den = probe(a.clip, "v:0", "r_frame_rate")[0].partition("/")
    fps = float(num) / float(den or 1)
    total_frames = int(probe(a.clip, "v:0", "nb_frames")[0])
    out = a.out or (os.path.splitext(a.clip)[0] + "-cc.mp4")

    work = tempfile.mkdtemp(prefix="cc-")
    try:
        words = []
        for i, row in enumerate(timeline, 1):
            for w in words_for(a.source, row["src"], row["dur"], work, f"w{i:03d}"):
                words.append({"t": w["t"], "brk": w["brk"],
                              "a": row["out"] + w["a"],
                              "b": row["out"] + w["b"]})
            # A cut boundary is always a caption boundary: the next cut is a
            # different moment in the take and often a different sentence.
            if words:
                words[-1]["brk"] = True
        if not words:
            print("no words timed", file=sys.stderr)
            return 1
        fixed = apply_corrections(words, load_corrections(a.corrections))
        if fixed:
            print(f"    {fixed} known mishearing(s) corrected")
        groups = group(words, WORDS_PER_GROUP)
        print(f"    {len(words)} words in {len(groups)} groups")

        shown = render_frames(groups, W, H, fps, total_frames, work)
        print(f"    {shown}/{total_frames} frames carry a caption")

        cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", a.clip]
        for d in a.underlay:
            cmd += ["-framerate", f"{fps}", "-i", os.path.join(d, "br%06d.png")]
        cmd += ["-framerate", f"{fps}", "-i", os.path.join(work, "cc%06d.png")]
        chain, prev = [], "[0:v]"
        for i in range(1, len(a.underlay) + 2):
            tag = "" if i == len(a.underlay) + 1 else f"[l{i}]"
            chain.append(f"{prev}[{i}:v]overlay=0:0:eof_action=pass:format=auto{tag}")
            prev = tag
        graph = ";".join(chain)
        cmd += ["-filter_complex", graph,
                "-c:v", "h264_videotoolbox", "-q:v", str(a.vt_q), "-realtime", "0",
                "-pix_fmt", "yuv420p",
                "-video_track_timescale", str(int(round(fps * 1000))),
                "-c:a", "copy", "-movflags", "+faststart", out]
        subprocess.run(cmd, check=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print(f"==> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
