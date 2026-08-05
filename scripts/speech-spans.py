#!/usr/bin/env python3
"""speech-spans.py: list every span of actual speech in a recording, with its words.

Replaces the two-pass approach (cut at transcript cue boundaries, then strip
silence) which compounds two sets of boundary errors: whisper cue starts lag the
true speech onset, so cutting exactly on them clips the first syllable, and a
second silence pass then trims again from an already-wrong edge.

This detects silence ONCE on the source, inverts it to speech spans, pads
generously on the lead-in (speech onset is the easy thing to clip and the most
audible when you get it wrong), and labels each span with the transcript text
that overlaps it. Feed the chosen span ids to assemble-spans.sh.

  python3 scripts/speech-spans.py SRC.MOV transcript.srt [--db -28] [--gap 0.45]
"""
import argparse
import re
import subprocess
import sys

TS = re.compile(r"(\d+):(\d+):(\d+)[.,](\d+)\s*-->\s*(\d+):(\d+):(\d+)[.,](\d+)")

# Speech onset is clipped far more noticeably than a tail, so the head pad is
# deliberately larger than the tail pad.
HEAD_PAD = 0.12   # word onsets are already tight; this only guards the attack
TAIL_PAD = 0.05   # cut where the word ends, not a fifth of a second later


def secs(h, m, s, ms):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def hms(t):
    return f"{int(t // 60):02d}:{t % 60:06.3f}"


def load_srt(path):
    cues, lines = [], open(path, encoding="utf-8", errors="replace").read().splitlines()
    i = 0
    while i < len(lines):
        m = TS.search(lines[i])
        if not m:
            i += 1
            continue
        g = m.groups()
        j, buf = i + 1, []
        while j < len(lines) and lines[j].strip() and not TS.search(lines[j]):
            if not lines[j].strip().isdigit():
                buf.append(lines[j].strip())
            j += 1
        if buf:
            cues.append((secs(*g[0:4]), secs(*g[4:8]), " ".join(buf)))
        i = j
    return cues


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("srt")
    ap.add_argument("--db", type=float, default=-28.0)
    ap.add_argument("--gap", type=float, default=0.45)
    a = ap.parse_args()

    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nk=1:nw=1", a.src],
        capture_output=True, text=True, check=True).stdout.strip())

    # silencedetect logs at info level, so -v error would swallow it.
    raw = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-i", a.src,
         "-af", f"silencedetect=noise={a.db}dB:d={a.gap}", "-f", "null", "-"],
        capture_output=True, text=True).stderr

    starts = [float(x) for x in re.findall(r"silence_start:\s*(-?[\d.]+)", raw)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*([\d.]+)", raw)]
    if len(ends) < len(starts):
        ends.append(dur)

    spans, cur = [], 0.0
    for s, e in zip(starts, ends):
        lo, hi = max(0.0, cur - HEAD_PAD), min(s + TAIL_PAD, dur)
        if hi - lo > 0.30:
            spans.append([lo, hi])
        cur = e
    if dur - cur > 0.30:
        spans.append([max(0.0, cur - HEAD_PAD), dur])

    cues = load_srt(a.srt)
    print(f"# {len(spans)} speech spans, "
          f"{sum(b - x for x, b in spans):.1f}s of {dur:.1f}s\n", file=sys.stderr)
    for i, (lo, hi) in enumerate(spans, 1):
        words = " ".join(t for cs, ce, t in cues if ce > lo and cs < hi).strip()
        print(f"{i:03d}\t{lo:.3f}\t{hi:.3f}\t{hi - lo:5.2f}s\t{words[:130]}")


if __name__ == "__main__":
    main()
