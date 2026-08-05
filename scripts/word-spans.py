#!/usr/bin/env python3
"""word-spans.py: speech spans cut to the word, not to an amplitude threshold.

Amplitude detection alone cannot place a cut where a word ends: silencedetect
fires when the waveform crosses a threshold, which for a trailing consonant is
audibly late, and any tail pad on top of that leaves dead air on every join.

Word timings alone cannot do it either: whisper stretches the segment before a
pause to cover the pause, so the last word of a phrase reports an end far past
where it was spoken.

So use each for what it is good at.
  IN  point  <- word start (precise, and the onset is what clipping ruins)
  OUT point  <- silence onset when one falls inside the final word, else word end

  whisper-cli -m MODEL -f audio.wav -ml 1 -sow -ojf -dtw base.en -of words
  python3 scripts/word-spans.py words.json audio.wav [--gap 0.45] [--db -28]
"""
import argparse
import json
import re
import subprocess
import sys

HEAD_PAD = 0.08   # onset is the audible failure, but word starts are already tight
TAIL_PAD = 0.05   # just enough to keep a plosive from being sheared


def hms(t):
    return f"{int(t // 60):02d}:{t % 60:06.3f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("words_json")
    ap.add_argument("audio")
    ap.add_argument("--gap", type=float, default=0.45, help="pause that separates phrases")
    ap.add_argument("--db", type=float, default=-28.0)
    a = ap.parse_args()

    words = []
    for s in json.load(open(a.words_json))["transcription"]:
        t = s["text"].strip()
        if not t:
            continue
        words.append({"t": t, "a": s["offsets"]["from"] / 1000.0,
                      "b": s["offsets"]["to"] / 1000.0})

    raw = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-i", a.audio,
         "-af", f"silencedetect=noise={a.db}dB:d=0.20", "-f", "null", "-"],
        capture_output=True, text=True).stderr
    sil_starts = sorted(float(x) for x in re.findall(r"silence_start:\s*(-?[\d.]+)", raw))

    def true_end(w):
        """A word never really lasts a second. If silence begins inside this word,
        that is where the speech actually stopped."""
        inside = [s for s in sil_starts if w["a"] < s < w["b"]]
        if inside:
            return inside[0]
        return w["b"]

    # A phrase breaks where the gap to the next word exceeds --gap, measured from
    # the TRUE end rather than the stretched one.
    runs, cur = [], [words[0]]
    for prev, nxt in zip(words, words[1:]):
        if nxt["a"] - true_end(prev) > a.gap:
            runs.append(cur)
            cur = [nxt]
        else:
            cur.append(nxt)
    runs.append(cur)

    total = 0.0
    for i, run in enumerate(runs, 1):
        start = max(0.0, run[0]["a"] - HEAD_PAD)
        end = true_end(run[-1]) + TAIL_PAD
        if end - start < 0.25:
            continue
        total += end - start
        text = " ".join(w["t"] for w in run)
        print(f"{i:03d}\t{start:.3f}\t{end:.3f}\t{end - start:5.2f}s\t{text[:120]}")

    print(f"# {len(runs)} phrases, {total:.1f}s of speech", file=sys.stderr)


if __name__ == "__main__":
    main()
