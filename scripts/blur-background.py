#!/usr/bin/env python3
"""blur-background.py: real background blur for talking-head video.

Uses RobustVideoMatting (PeterL1n/RobustVideoMatting) rather than per-frame
segmentation. RVM carries recurrent state between frames, so the matte is
temporally stable and edges do not crawl. Per-frame segmenters flicker around
hair and that flicker reads as cheap, which is the whole reason not to use them.

The background is a blurred copy of the ACTUAL background, not a flat colour,
so it reads as depth of field instead of a green-screen replacement.

  ~/.venvs/rvm/bin/python scripts/blur-background.py IN.MOV OUT.mp4 \
      [--start 12.5] [--end 41.0] [--blur 45] [--downsample 0.25]

Runs on Apple Silicon via MPS when available. Audio is copied from the source.
"""
import argparse
import re
import subprocess
import sys

import numpy as np
import torch


def probe(path):
    """Return the dimensions ffmpeg ACTUALLY decodes, not the stored ones.

    Phone video carries rotation metadata. ffprobe's stream width/height are the
    stored values; ffmpeg auto-applies the rotation on decode, so a clip stored
    2160x3840 with rotation=-90 arrives as 3840x2160. Reading the stored numbers
    reshapes the raw buffer at the wrong stride and produces a smeared frame,
    which is a silent corruption rather than an error. Ask showinfo instead.
    """
    fps_out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, check=True).stdout.strip()
    num, den = fps_out.split("/")
    fps = float(num) / float(den)

    info = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "info", "-i", path,
         "-frames:v", "1", "-vf", "showinfo", "-f", "null", "-"],
        capture_output=True, text=True).stderr
    m = re.search(r"\ss:(\d+)x(\d+)", info)
    if not m:
        raise RuntimeError("could not determine decoded frame size")
    return int(m.group(1)), int(m.group(2)), fps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--start", type=float, default=None)
    ap.add_argument("--end", type=float, default=None)
    ap.add_argument("--blur", type=float, default=36.0,
                    help="background blur radius in pixels at source resolution")
    ap.add_argument("--downsample", type=float, default=None,
                    help="RVM inference scale; auto from width if omitted")
    ap.add_argument("--model", default="mobilenetv3", choices=["mobilenetv3", "resnet50"])
    ap.add_argument("--spans", help="span file; only these ranges are kept and matted")
    ap.add_argument("--audio", help="pre-cut audio track to mux (must match the spans)")
    args = ap.parse_args()

    W, H, FPS = probe(args.src)
    # RVM wants the inference side around 512px. 4K vertical -> ~0.25.
    ds = args.downsample or max(0.1, min(1.0, 512 / max(W, H)))
    dev = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"{W}x{H} @ {FPS:.2f}fps | device={dev} | downsample={ds:.3f}", file=sys.stderr)

    model = torch.hub.load("PeterL1n/RobustVideoMatting", args.model,
                           trust_repo=True).to(dev).eval()

    spans = []
    if args.spans:
        for line in open(args.spans):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            p0 = line.split()
            spans.append((float(p0[0]), float(p0[1])))
        spans.sort()
        print(f"spans: {len(spans)}, {sum(b - a for a, b in spans):.1f}s kept",
              file=sys.stderr)

    rd = ["ffmpeg", "-nostdin", "-v", "error"]
    if args.start is not None:
        rd += ["-ss", str(args.start)]
    if args.end is not None:
        rd += ["-to", str(args.end)]
    rd += ["-i", args.src, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]

    wr = ["ffmpeg", "-nostdin", "-v", "error", "-y",
          "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-"]
    # Pull audio from the same time range so it stays in sync.
    if args.start is not None:
        wr += ["-ss", str(args.start)]
    if args.end is not None:
        wr += ["-to", str(args.end)]
    wr += ["-i", args.audio if args.audio else args.src, "-map", "0:v", "-map", "1:a?",
           "-c:v", "hevc_videotoolbox", "-q:v", "60", "-tag:v", "hvc1",
           "-c:a", "aac", "-b:a", "192k", "-shortest", args.dst]

    src = subprocess.Popen(rd, stdout=subprocess.PIPE)
    dst = subprocess.Popen(wr, stdin=subprocess.PIPE)
    fsize = W * H * 3

    # Heavy blur via downscale -> small gaussian -> upscale. A sigma-36 kernel at
    # 4K would be 200+ taps per axis; shrinking first is visually identical for a
    # defocus effect and roughly two orders of magnitude cheaper.
    SCALE = 8
    sigma = max(1.0, args.blur / SCALE)
    k = int(sigma * 6) | 1
    x = torch.arange(k, dtype=torch.float32, device=dev) - k // 2
    g = torch.exp(-(x ** 2) / (2 * sigma ** 2))
    g = (g / g.sum()).view(1, 1, 1, k)
    F = torch.nn.functional

    def blur(t):
        c = t.shape[1]
        small = F.interpolate(t, scale_factor=1 / SCALE, mode="area")
        small = F.conv2d(small, g.expand(c, 1, 1, k), padding=(0, k // 2), groups=c)
        small = F.conv2d(small, g.transpose(2, 3).expand(c, 1, k, 1),
                         padding=(k // 2, 0), groups=c)
        return F.interpolate(small, size=t.shape[-2:], mode="bilinear", align_corners=False)

    rec = [None] * 4
    n = 0          # frames read from the source
    kept = 0       # frames matted and written
    si = 0         # index into spans
    with torch.no_grad():
        while True:
            raw = src.stdout.read(fsize)
            if len(raw) < fsize:
                break
            t = n / FPS
            n += 1
            if spans:
                while si < len(spans) and t > spans[si][1]:
                    si += 1
                    rec = [None] * 4     # new shot; drop the recurrent state
                if si >= len(spans):
                    break
                if t < spans[si][0]:
                    continue             # decoded but not matted, which is the saving
            frame = torch.from_numpy(
                np.frombuffer(raw, np.uint8).reshape(H, W, 3).copy()
            ).to(dev).permute(2, 0, 1).unsqueeze(0).float() / 255.0

            fgr, pha, *rec = model(frame, *rec, downsample_ratio=ds)
            out = fgr * pha + blur(frame) * (1 - pha)

            dst.stdin.write(
                (out.clamp(0, 1)[0].permute(1, 2, 0) * 255)
                .to(torch.uint8).cpu().numpy().tobytes())
            kept += 1
            if kept % 300 == 0:
                print(f"  {kept} matted / {n} read ({kept / FPS:.0f}s out)", file=sys.stderr)

    src.stdout.close()
    dst.stdin.close()
    dst.wait()
    print(f"done: {kept} frames ({kept / FPS:.1f}s) -> {args.dst}", file=sys.stderr)


if __name__ == "__main__":
    main()
