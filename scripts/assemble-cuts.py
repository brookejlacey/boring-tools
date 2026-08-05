#!/usr/bin/env python3
"""assemble-cuts.py: build a jump-cut edit with per-cut framing.

assemble-spans.sh joins spans at one fixed scale. That is right for a rough
assembly and wrong for a finished edit, where the framing is what makes a jump
cut read as a cut rather than a glitch: hold the same frame across a join and
the subject twitches, change the size and the join reads as a second camera.

So each cut carries a framing level, and the crop for that level is computed
from where the face actually is in that cut rather than from the centre of the
frame. Punch-ins are LOCKED for the duration of a cut, never tracking, because a
crop that follows the face frame by frame reads as a bad auto-reframe.

  cuts.tsv:   start  end  frame  label      (frame: wide | mid | close)
  faces.tsv:  from scripts/face-track.py, keyed by the same span ids

  ~/.venvs/rvm/bin/python scripts/assemble-cuts.py SRC.MOV cuts.tsv faces.tsv OUT.mp4

Vertical output from a horizontal source is the default because that is the
shoot: 16:9 4K in, 9:16 for TikTok/Reels out. `wide` is then the full height of
the source, and there is no framing looser than that.
"""
import argparse
import os
import shutil
import struct
import subprocess
import sys
import tempfile

import numpy as np

HOP = 0.02

# Zoom relative to `wide`. The ladder is deliberately short: three sizes read as
# deliberate, more reads as restless. `close` stays modest so the crop is still
# near-native rather than visibly soft, and so the head is not guillotined.
LEVELS = {"wide": 1.00, "mid": 1.20, "close": 1.42}

# Frame on the EYES, a third down, which is how a talking head is framed and the
# only anchor that survives a punch-in. Anchoring on the detected box centre
# instead crops the forehead off, because the detector returns the inner face
# and the head carries a lot of hair above it.
EYES_AT = 0.33
# Eyes sit this far above the centre of the detected face box, in box heights.
EYE_OFFSET = 0.25


def probe_display_size(src):
    """DISPLAY dimensions, rotation applied. See face-track.py for why."""
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


def source_envelope(src):
    """Per-20ms dB envelope of the source audio, and the speech gate for it.

    Cut boundaries that come from a span list carry that list's padding, so two
    adjacent cuts join with the sum of both pads between them. On this edit that
    was up to 0.87s, which is a hole in the middle of what should be a jump cut.
    Re-measuring against the source lets every cut be snapped to its own speech.
    """
    raw = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", src, "-vn",
         "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
        capture_output=True).stdout
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    hop = int(16000 * HOP)
    n = len(x) // hop
    db = 20 * np.log10(np.sqrt((x[:n * hop].reshape(n, hop) ** 2).mean(axis=1)) + 1e-12)
    quiet, loud = np.percentile(db, 20), np.percentile(db, 92)
    return db, quiet + (loud - quiet) * 0.45


def snap(db, gate, start, end, pad_head, pad_tail):
    """Pull a cut's edges in to the speech actually inside it."""
    i0, i1 = int(start / HOP), min(len(db), int(end / HOP))
    if i1 <= i0:
        return start, end
    voiced = np.nonzero(db[i0:i1] > gate)[0]
    if not len(voiced):
        return start, end
    s = (i0 + voiced[0]) * HOP - pad_head
    e = (i0 + voiced[-1] + 1) * HOP + pad_tail
    # Never widen past what the caller asked for; this only ever tightens.
    return max(start, s), min(end, e)


def probe_fps(src):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate", "-of", "default=nw=1:nk=1", src],
        capture_output=True, text=True).stdout.strip().split("\n")[0]
    num, _, den = out.partition("/")
    return float(num) / float(den or 1)


def verify_sync(out, fps, tol_ms=1.0):
    """Assert the render is on the frame grid and sound matches picture.

    This exists because the drift that shipped on 2026-07-28 was caught by
    Brooke watching the video, not by this pipeline. Everything else about that
    render verified clean: transcript, joins, no dead air, framing, eyeline. The
    one property nobody checked was the only one that was broken. A render is
    not finished until it has proved this, so it is asserted here rather than
    left to whoever remembers to look.
    """
    def probe(stream, fields):
        return subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", stream,
             "-show_entries", f"stream={fields}", "-of", "default=nw=1:nk=1", out],
            capture_output=True, text=True).stdout.strip().split("\n")

    vdur = float(probe("v:0", "duration")[0])
    adur = float(probe("a:0", "duration")[0])
    pts = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "packet=pts_time", "-of", "csv=p=0", out],
        capture_output=True, text=True).stdout.split()
    t = [float(x) for x in pts if x.strip()]

    problems = []
    if t:
        drift = max(abs(t[i] - i / fps) for i in range(len(t))) * 1000
        if drift > tol_ms:
            problems.append(f"video timestamps drift {drift:.1f}ms off a {fps:g}fps grid")
    else:
        drift = 0.0
        problems.append("no video packets")
    if abs(vdur - adur) * 1000 > 50:
        problems.append(f"video {vdur:.3f}s vs audio {adur:.3f}s")

    if problems:
        print("SYNC CHECK FAILED: " + "; ".join(problems), file=sys.stderr)
        return False
    print(f"    sync ok: {len(t)} frames, {fps:g}fps, drift {drift:.3f}ms, "
          f"a/v {abs(vdur - adur) * 1000:.0f}ms apart")
    return True


def load_faces(path):
    faces = {}
    for line in open(path):
        if line.startswith("#") or not line.strip():
            continue
        f = line.rstrip("\n").split("\t")
        faces[f[0]] = tuple(float(x) for x in f[1:5])
    return faces


def load_cuts(path):
    cuts = []
    for line in open(path):
        if line.startswith("#") or not line.strip():
            continue
        f = line.rstrip("\n").split("\t")
        cuts.append({"start": float(f[0]), "end": float(f[1]),
                     "frame": f[2].strip(), "label": f[3] if len(f) > 3 else "",
                     "face": f[4].strip() if len(f) > 4 else ""})
    return cuts


def crop_rect(level, face, W, H, out_w, out_h):
    """Crop rectangle in source pixels for one cut.

    Everything is anchored to the face box so the subject lands in the same
    place at every size; a punch-in centred on the frame instead would drift
    off her, since she does not sit dead centre.
    """
    fcx, fcy, _, fh = face
    ar = out_w / out_h
    base_h = min(H, W / ar)            # the tallest crop of the output aspect
    ch = base_h / LEVELS[level]
    cw = ch * ar

    eyes = (fcy - EYE_OFFSET * fh) * H
    cx = fcx * W
    cy = eyes + (0.5 - EYES_AT) * ch

    x = max(0.0, min(W - cw, cx - cw / 2))
    y = max(0.0, min(H - ch, cy - ch / 2))
    # ffmpeg wants even numbers for yuv420p.
    return (int(cw) // 2 * 2, int(ch) // 2 * 2, int(x) // 2 * 2, int(y) // 2 * 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("cuts")
    ap.add_argument("faces")
    ap.add_argument("out")
    ap.add_argument("--size", default="1080x1920")
    ap.add_argument("--crf", type=int, default=18)
    ap.add_argument("--preset", default="medium")
    ap.add_argument("--x264", action="store_true",
                    help="encode on the CPU instead of the media engine. Only "
                         "worth it for an archival master; see --vt-q")
    ap.add_argument("--vt-q", type=int, default=65,
                    help="videotoolbox quality, 1-100 (default 65)")
    ap.add_argument("--soft-decode", action="store_true",
                    help="decode in software; only needed if a source refuses "
                         "hardware decode (unusual codec, corrupt stream)")
    ap.add_argument("--no-snap", action="store_true",
                    help="keep the cutlist's boundaries verbatim")
    ap.add_argument("--pad-head", type=float, default=0.05)
    ap.add_argument("--pad-tail", type=float, default=0.12)
    a = ap.parse_args()

    out_w, out_h = (int(x) for x in a.size.split("x"))
    W, H = probe_display_size(a.src)
    faces = load_faces(a.faces)
    cuts = load_cuts(a.cuts)
    if not cuts:
        print("no cuts", file=sys.stderr)
        return 1

    median_face = tuple(
        sorted(f[i] for f in faces.values())[len(faces) // 2] for i in range(4)
    ) if faces else (0.5, 0.5, 0.2, 0.2)

    if not a.no_snap:
        db, gate = source_envelope(a.src)
        for c in cuts:
            s, e = snap(db, gate, c["start"], c["end"], a.pad_head, a.pad_tail)
            c["trim"] = (s - c["start"]) + (c["end"] - e)
            c["start"], c["end"] = s, e

    # Decode on the media engine too, not just encode. A 4K HEVC source is
    # expensive to unpack in software and the cut list decodes it once per cut.
    # Measured on IMG_4199: 34s of CPU at 1420% falls to 8s at 440% for the same
    # segment, and the decoded frames are pixel-identical, SSIM 1.000000 against
    # the software path once both are normalised to yuv420p. This is the single
    # biggest reason the laptop got hot, and it costs nothing.
    hwdec = [] if a.soft_decode else ["-hwaccel", "videotoolbox"]

    if a.x264:
        vcodec = ["-c:v", "libx264", "-crf", str(a.crf), "-preset", a.preset]
    else:
        # The media engine is the default because it is free and, measured on
        # this footage, not worse. On a close-up cut videotoolbox q65 scored
        # SSIM 0.9789 against an ffv1 reference at 137 MB/min; libx264 crf 20
        # scored 0.9780 at 134 MB/min. Same quality, same size, without pinning
        # every performance core. Quality target rather than a fixed bitrate,
        # so the tight cuts are not starved by the wide ones.
        vcodec = ["-c:v", "h264_videotoolbox", "-q:v", str(a.vt_q), "-realtime", "0"]

    # Quantise every cut to a whole number of frames. This is what keeps sound
    # locked to picture, and getting it wrong is not subtle: cutting at
    # arbitrary times gives each segment a video track rounded UP to the next
    # frame and an audio track trimmed to the exact request, so every join
    # leaked 3-23ms of extra picture. Nineteen of those put the audio about
    # 0.43s ahead by the end, which is audible from a few seconds in and reads
    # as the whole edit slipping.
    fps = probe_fps(a.src)
    for c in cuts:
        n = max(1, round((c["end"] - c["start"]) * fps))
        c["frames"] = n
        c["end"] = c["start"] + n / fps

    work = tempfile.mkdtemp(prefix="cuts-")
    try:
        # VIDEO: one file per cut, exactly c["frames"] frames each, then a
        # stream copy concat. The timescale is pinned to a multiple of the frame
        # rate so a segment's duration is representable exactly; on the default
        # millisecond timescale the concat demuxer inherits each container's
        # rounded duration and inserts a whole dropped frame at some joins.
        listing = os.path.join(work, "list.txt")
        with open(listing, "w") as lf:
            for i, c in enumerate(cuts, 1):
                face = faces.get(c["face"], median_face)
                cw, ch, x, y = crop_rect(c["frame"], face, W, H, out_w, out_h)
                seg = os.path.join(work, f"c{i:03d}.mp4")
                subprocess.run(
                    ["ffmpeg", "-nostdin", "-v", "error", "-y",
                     *hwdec,
                     "-ss", f"{c['start']:.6f}", "-t", f"{c['frames'] / fps:.6f}",
                     "-i", a.src, "-an",
                     "-vf", f"crop={cw}:{ch}:{x}:{y},scale={out_w}:{out_h}:"
                            f"flags=lanczos,setsar=1,fps={fps}",
                     "-frames:v", str(c["frames"]),
                     *vcodec,
                     "-pix_fmt", "yuv420p", "-profile:v", "high",
                     "-video_track_timescale", str(int(round(fps * 1000))),
                     seg], check=True)
                lf.write(f"file '{seg}'\n")
                snapped = f" -{c['trim']:.2f}s" if c.get("trim", 0) > 0.005 else ""
                print(f"  {i:02d} {c['frame']:>5}  {c['start']:7.2f}->{c['end']:7.2f} "
                      f"({c['frames'] / fps:4.2f}s, {c['frames']}f)  "
                      f"crop {cw}x{ch}+{x}+{y}  {c['label']}{snapped}")

        video = os.path.join(work, "video.mp4")
        subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "concat", "-safe", "0",
             "-i", listing, "-c", "copy", video], check=True)

        # AUDIO: built in ONE pass over the same frame-quantised ranges, so it
        # never crosses a segment boundary and cannot accumulate an offset. It
        # also means the audio is compressed exactly once, which is what removed
        # the encoder priming that used to click at every join. -vn keeps this
        # cheap: no video is decoded here.
        chain, maps = [], []
        for i, c in enumerate(cuts):
            end = c["start"] + c["frames"] / fps
            chain.append(f"[0:a]atrim={c['start']:.6f}:{end:.6f},"
                         f"asetpts=PTS-STARTPTS[a{i}];")
            maps.append(f"[a{i}]")
        graph = "".join(chain) + "".join(maps) + f"concat=n={len(cuts)}:v=0:a=1[outa]"
        audio = os.path.join(work, "audio.wav")
        subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-y", "-vn", "-i", a.src,
             "-filter_complex", graph, "-map", "[outa]",
             "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", audio], check=True)

        subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", video, "-i", audio,
             "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
             "-movflags", "+faststart", a.out], check=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    # Emit the resolved timeline. Anything that has to line up with the finished
    # cut (captions above all) needs to map source time to output time, and
    # re-deriving that by re-running the snap and the quantiser is a drift
    # waiting to happen. The render states it once, as text.
    tl = os.path.splitext(a.out)[0] + ".timeline.tsv"
    with open(tl, "w") as f:
        f.write("# id\tout_start\tsrc_start\tframes\tdur\tframe\tlabel\n")
        t = 0.0
        for i, c in enumerate(cuts, 1):
            d = c["frames"] / fps
            f.write(f"{i:03d}\t{t:.6f}\t{c['start']:.6f}\t{c['frames']}\t{d:.6f}"
                    f"\t{c['frame']}\t{c['label']}\n")
            t += d

    ok = verify_sync(a.out, fps)

    dur = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nk=1:nw=1", a.out],
        capture_output=True, text=True).stdout.strip()
    print(f"==> {len(cuts)} cuts, {float(dur):.2f}s, {out_w}x{out_h} -> {a.out}")
    # Non-zero on a failed sync check so a batch render stops instead of
    # quietly writing a file that looks finished and is not.
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
