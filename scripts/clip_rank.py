#!/usr/bin/env python3
"""Rank clip-worthy moments in a livestream transcript.

Input: whisper-cli JSON transcript (-oj) + the source video.
Output: clips.html (ranked candidates with ffmpeg cut commands + copy buttons),
optionally auto-cuts the top N candidates with ffmpeg via --cut N.

Usage:
  clip_rank.py <transcript.json> <video> <outdir> [--cut N] [--min-sec 15] [--max-sec 75] [--top 12]
"""
import argparse
import html
import json
import re
import subprocess
import sys
from pathlib import Path

# Discovery-led live: the clip unit is the re-narrated realization. These cues
# score the moments where Brooke turns to camera and tells the friend version.
CUE_WEIGHTS = {
    r"\bjust (discovered|realized|figured|found)\b": 5.0,
    r"\b(look|watch) (at )?this\b": 4.0,
    r"\bwait\b": 2.5,
    r"\bwhat\b\s*[.!?]": 2.0,
    r"\boh my (god|gosh)\b": 3.5,
    r"\bholy\b": 3.5,
    r"\bno way\b": 3.0,
    r"\b(insane|wild|nuts|crazy)\b": 2.5,
    r"\bi('m| am) not touching\b": 4.0,
    r"\bit just (did|wrote|built|fixed|shipped)\b": 4.0,
    r"\bdid you (see|catch) (that|this)\b": 3.5,
    r"\byou can\b": 1.5,
    r"\byour (job|boss|work)\b": 2.0,
    r"\bhere'?s the (thing|part)\b": 2.0,
    r"\bnobody (tells|teaches|shows)\b": 3.0,
    r"\b\d[\d,.]*\b": 0.5,          # concrete numbers
    r"[!?]": 0.4,                    # exclamation/question density
    r"\bokay so\b": 1.5,
    r"\bcheck this out\b": 4.0,
}


def load_segments(transcript_path):
    data = json.loads(Path(transcript_path).read_text())
    segs = []
    for s in data.get("transcription", []):
        text = s.get("text", "").strip()
        if not text:
            continue
        segs.append({
            "start": s["offsets"]["from"] / 1000.0,
            "end": s["offsets"]["to"] / 1000.0,
            "text": text,
        })
    return segs


def score_text(text):
    t = text.lower()
    score, hits = 0.0, []
    for pattern, weight in CUE_WEIGHTS.items():
        n = len(re.findall(pattern, t))
        if n:
            score += weight * n
            hits.append(re.sub(r"\\b|\(.*?\)|\[.*?\]|\\", "", pattern).strip() or pattern)
    return score, hits


def build_candidates(segs, min_sec, max_sec):
    candidates = []
    for i in range(len(segs)):
        for j in range(i, len(segs)):
            dur = segs[j]["end"] - segs[i]["start"]
            if dur > max_sec:
                break
            if dur < min_sec:
                continue
            text = " ".join(s["text"] for s in segs[i:j + 1])
            score, hits = score_text(text)
            # favor tighter clips: per-second density matters more than raw sum
            density = score / max(dur, 1.0)
            candidates.append({
                "start": segs[i]["start"],
                "end": segs[j]["end"],
                "dur": dur,
                "score": round(score + density * 10, 2),
                "hits": sorted(set(hits)),
                "text": text,
            })
    return candidates


def dedupe(candidates, top):
    """Greedy pick best-scoring, skip anything overlapping an already-picked clip."""
    picked = []
    for c in sorted(candidates, key=lambda c: -c["score"]):
        if any(not (c["end"] <= p["start"] or c["start"] >= p["end"]) for p in picked):
            continue
        picked.append(c)
        if len(picked) >= top:
            break
    return sorted(picked, key=lambda c: c["start"])


def hms(t):
    h, rem = divmod(int(t), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def ffmpeg_cmd(video, c, out):
    return (f"ffmpeg -y -ss {c['start']:.1f} -to {c['end']:.1f} -i '{video}' "
            f"-c:v libx264 -c:a aac -movflags +faststart '{out}'")


STYLE = """
body{background:#F4EFE4;color:#0E0E0C;font-family:Inter,system-ui,sans-serif;
margin:0;padding:32px 16px;max-width:860px;margin-inline:auto;line-height:1.55}
h1,h2{font-family:Newsreader,Georgia,serif;font-weight:600}
h1{font-size:2rem;margin-bottom:4px}
.sub{color:#5a5648;margin-bottom:28px}
.clip{border:1px solid #d8d1bf;border-radius:2px;background:#faf7ee;padding:18px 20px;margin-bottom:18px}
.meta{font-family:'JetBrains Mono',monospace;font-size:.8rem;color:#8E3B20;margin-bottom:8px}
.hits{font-size:.78rem;color:#5a5648;margin:6px 0 10px}
.txt{font-size:.95rem;margin-bottom:12px}
pre{background:#0E0E0C;color:#F4EFE4;padding:10px 12px;border-radius:2px;overflow-x:auto;
font-family:'JetBrains Mono',monospace;font-size:.74rem;white-space:pre-wrap;word-break:break-all}
button{background:#8E3B20;color:#F4EFE4;border:0;border-radius:2px;padding:6px 14px;
font-family:Inter,sans-serif;font-size:.8rem;cursor:pointer}
button:active{opacity:.7}
"""

JS = """
function cp(btn,id){navigator.clipboard.writeText(document.getElementById(id).textContent)
.then(()=>{btn.textContent='copied';setTimeout(()=>btn.textContent='copy ffmpeg cmd',1200)})}
"""


def write_html(video, picked, outdir):
    rows = []
    for n, c in enumerate(picked, 1):
        out = Path(outdir) / "clips" / f"clip-{n:02d}.mp4"
        cmd = ffmpeg_cmd(video, c, out)
        rows.append(f"""
<div class="clip">
  <div class="meta">#{n} · {hms(c['start'])} → {hms(c['end'])} · {c['dur']:.0f}s · score {c['score']}</div>
  <div class="hits">cues: {html.escape(', '.join(c['hits'][:8]) or 'density only')}</div>
  <div class="txt">{html.escape(c['text'])}</div>
  <pre id="cmd{n}">{html.escape(cmd)}</pre>
  <button onclick="cp(this,'cmd{n}')">copy ffmpeg cmd</button>
</div>""")
    doc = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex, nofollow"><title>Clip candidates</title>
<style>{STYLE}</style><script>{JS}</script></head><body>
<h1>Clip candidates</h1>
<div class="sub">{html.escape(str(video))} · {len(picked)} candidates, ranked by discovery-cue density</div>
{''.join(rows)}
</body></html>"""
    path = Path(outdir) / "clips.html"
    path.write_text(doc)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("transcript")
    ap.add_argument("video")
    ap.add_argument("outdir")
    ap.add_argument("--cut", type=int, default=0, help="auto-cut top N clips")
    ap.add_argument("--min-sec", type=float, default=15)
    ap.add_argument("--max-sec", type=float, default=75)
    ap.add_argument("--top", type=int, default=12)
    args = ap.parse_args()

    segs = load_segments(args.transcript)
    if not segs:
        sys.exit("no transcript segments found")
    total = segs[-1]["end"]
    # short videos: relax the floor so a 60s test file still yields candidates
    min_sec = min(args.min_sec, max(total * 0.25, 5))
    picked = dedupe(build_candidates(segs, min_sec, args.max_sec), args.top)
    path = write_html(args.video, picked, args.outdir)
    print(f"{len(picked)} candidates -> {path}")

    if args.cut:
        (Path(args.outdir) / "clips").mkdir(exist_ok=True)
        for n, c in enumerate(picked[:args.cut], 1):
            out = Path(args.outdir) / "clips" / f"clip-{n:02d}.mp4"
            subprocess.run(ffmpeg_cmd(args.video, c, out), shell=True, check=True,
                           capture_output=True)
            print(f"cut {out}")


if __name__ == "__main__":
    main()
