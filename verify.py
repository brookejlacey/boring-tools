#!/usr/bin/env python3
"""verify.py -- prove a set of renders is finished.

    python3 verify.py out/*.mp4
    python3 verify.py ~/Movies/shoots/2026-07-28-jayla/clips --fps 30

Checks, per file: dimensions, frame count, video duration, audio duration, and the
maximum drift of video timestamps off the frame grid. Exits non-zero if anything fails.

This exists because a sync drift once shipped in a render where every other property
verified clean: transcript, joins, no dead air, framing, eyeline. The one property
nobody had checked was the only one that was broken. So it is asserted here rather
than left to whoever remembers to look at it.

Tolerances: 1ms of timestamp drift, 50ms between audio and video duration. Both are
generous; a correct render measures 0.000ms.
"""
import argparse
import glob
import os
import subprocess
import sys


def probe(path, stream, fields):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", stream,
         "-show_entries", f"stream={fields}", "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True).stdout.strip().split("\n")
    return [x for x in out if x != ""]


def packet_times(path):
    """Presentation timestamps, in presentation order.

    ffprobe lists packets in DECODE order, and any encoder with B-frames emits
    them reordered, so the nth packet is not the nth frame on screen. Comparing
    that sequence against an n/fps grid reports a correct libx264 render as
    100ms adrift and fails it. videotoolbox emits no B-frames, which is why the
    default path never showed it. Sort before measuring: the grid is a property
    of presentation time, not of decode order.
    """
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "packet=pts_time", "-of", "csv=p=0", path],
        capture_output=True, text=True).stdout.split()
    return sorted(float(x) for x in out if x.strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+", help="files, globs, or a directory")
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--tol-ms", type=float, default=1.0)
    ap.add_argument("--av-tol-ms", type=float, default=50.0)
    a = ap.parse_args()

    files = []
    for p in a.paths:
        p = os.path.expanduser(p)
        if os.path.isdir(p):
            files += sorted(glob.glob(os.path.join(p, "*.mp4")))
        else:
            files += sorted(glob.glob(p)) or [p]
    files = [f for f in files if os.path.isfile(f)]
    if not files:
        print("no files matched", file=sys.stderr)
        return 2

    print(f"{'file':38} {'WxH':>10} {'frames':>7} {'video':>8} {'audio':>8} {'drift':>9}  check")
    all_ok = True
    for f in files:
        wh = probe(f, "v:0", "width,height")
        nb = (probe(f, "v:0", "nb_frames") or ["?"])[0]
        vd = float((probe(f, "v:0", "duration") or [0])[0])
        adur = probe(f, "a:0", "duration")
        ad = float(adur[0]) if adur else float("nan")
        t = packet_times(f)
        drift = max(abs(t[i] - i / a.fps) for i in range(len(t))) * 1000 if t else float("nan")

        problems = []
        if not t:
            problems.append("no video packets")
        elif drift > a.tol_ms:
            problems.append(f"drift {drift:.1f}ms off a {a.fps:g}fps grid")
        if adur and abs(vd - ad) * 1000 > a.av_tol_ms:
            problems.append(f"video {vd:.3f}s vs audio {ad:.3f}s")
        if not adur:
            problems.append("no audio stream")

        ok = not problems
        all_ok &= ok
        size = "x".join(wh) if len(wh) == 2 else "?"
        print(f"{os.path.basename(f):38} {size:>10} {nb:>7} {vd:8.3f} {ad:8.3f} "
              f"{drift:8.3f}ms  {'PASS' if ok else 'FAIL: ' + '; '.join(problems)}")

    print()
    print(f"{len(files)} files, {'all PASS' if all_ok else 'FAILURES'} : "
          f"every render on the {a.fps:g}fps grid, audio within {a.av_tol_ms:g}ms of picture")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
