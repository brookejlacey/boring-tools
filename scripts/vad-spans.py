#!/usr/bin/env python3
"""vad-spans.py: speech spans from the audio envelope, not from whisper timings.

word-spans.py and sentence-spans.py both trust whisper's word timestamps and use
silencedetect only to refine the OUT point. That holds up on a shoot where the
speaker keeps talking. It fails on a take-after-take recording: across a long
silence whisper's DTW has nothing to align to, so it smears the next sentence's
words backwards across the gap. On IMG_4181 a 3.6s sentence was reported as
spanning 22.4s, and every boundary derived from it was wrong.

The envelope has no such failure mode. On a quiet room the floor and the speech
are tens of dB apart, so a gate placed in the valley between them is exact.

  ~/.venvs/rvm/bin/python scripts/vad-spans.py audio.wav [--floor auto] [--gap 0.6]

`--floor auto` reads the level histogram and puts the gate in the valley between
the room and the voice, which is more reliable than any fixed dB across shoots.
Emits "id start end dur" as TSV; transcribe each span to get text you can trust.
"""
import argparse
import sys
import wave

import numpy as np

HOP = 0.02


def envelope(path):
    w = wave.open(path)
    sr = w.getframerate()
    x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    if w.getnchannels() > 1:
        x = x.reshape(-1, w.getnchannels()).mean(axis=1)
    hop = int(sr * HOP)
    n = len(x) // hop
    frames = x[:n * hop].reshape(n, hop)
    db = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-12)
    return db, len(x) / sr


def auto_floor(db):
    """Put the gate in the valley between the room floor and the voice.

    A talking-head recording is strongly bimodal: most frames are either room
    tone or speech. Take the midpoint between those two modes rather than a
    fixed threshold, so the same script works on a loud room and a quiet one.
    """
    quiet = np.percentile(db, 20)      # solidly inside the room-tone mode
    loud = np.percentile(db, 92)       # solidly inside the speech mode
    if loud - quiet < 12:
        print(f"# WARNING: only {loud - quiet:.1f}dB between room and voice; "
              f"gate is a guess", file=sys.stderr)
    return quiet + (loud - quiet) * 0.45


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("--floor", default="auto", help="dB gate, or 'auto'")
    ap.add_argument("--gap", type=float, default=0.6,
                    help="silence shorter than this stays inside one span")
    ap.add_argument("--min", type=float, default=0.30, help="drop spans shorter than this")
    ap.add_argument("--pad-head", type=float, default=0.12)
    ap.add_argument("--pad-tail", type=float, default=0.15)
    a = ap.parse_args()

    db, dur = envelope(a.audio)
    gate = auto_floor(db) if a.floor == "auto" else float(a.floor)
    print(f"# gate {gate:.1f}dB  (room {np.percentile(db, 20):.1f}, "
          f"voice {np.percentile(db, 92):.1f})", file=sys.stderr)

    voiced = db > gate
    # Close short gaps first, so an intra-word stop does not split a span.
    runs = []
    i = 0
    while i < len(voiced):
        if not voiced[i]:
            i += 1
            continue
        j = i
        while j < len(voiced) and voiced[j]:
            j += 1
        runs.append([i * HOP, j * HOP])
        i = j

    merged = []
    for r in runs:
        if merged and r[0] - merged[-1][1] < a.gap:
            merged[-1][1] = r[1]
        else:
            merged.append(r)

    kept, total = 0, 0.0
    for s, e in merged:
        if e - s < a.min:
            continue
        s = max(0.0, s - a.pad_head)
        e = min(dur, e + a.pad_tail)
        kept += 1
        total += e - s
        print(f"{kept:03d}\t{s:.3f}\t{e:.3f}\t{e - s:.2f}")

    print(f"# {kept} spans, {total:.1f}s speech of {dur:.1f}s "
          f"({100 * total / dur:.0f}%)", file=sys.stderr)


if __name__ == "__main__":
    main()
