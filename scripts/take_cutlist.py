#!/usr/bin/env python3
"""Group a one-take-everything recording into proposed segments.

Reads a whisper.cpp timestamped transcript plus ffmpeg silencedetect output and
emits a markdown cut list. Detects three things mechanically:

  1. RESTARTS   - near-duplicate opening phrases = the same line shot repeatedly.
                  The LAST attempt is marked KEEP; earlier ones are marked cut.
  2. STUMBLES   - explicit verbal aborts ("sorry", "start over", "again", "wait").
  3. DEAD AIR   - silence gaps, used as segment boundaries.

It cannot judge delivery. Where two attempts are both clean it flags BOTH and
says so, because picking between good takes is taste, not detection.
"""
import re
import sys
from difflib import SequenceMatcher

TS = re.compile(r"\[?(\d+):(\d+):(\d+)[.,](\d+)\s*-->\s*(\d+):(\d+):(\d+)[.,](\d+)\]?\s*(.*)")
ABORT = re.compile(
    r"\b(sorry|start over|starting over|let me|again|wait|hold on|scratch that|ugh|"
    r"one more time|do that again|nope|shoot|dang it)\b", re.I)
TAKE = re.compile(r"\b(take|body|hook|register|episode)\s+(one|two|three|four|five|six|\d+)\b", re.I)


def secs(h, m, s, ms):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def hms(t):
    return f"{int(t // 60):02d}:{t % 60:05.2f}"


def load(path):
    """Handles whisper stdout ([hh:mm:ss.mmm --> ...] text) and SRT (timestamp line,
    then text on following lines)."""
    out = []
    lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
    i = 0
    while i < len(lines):
        m = TS.match(lines[i].strip())
        if not m:
            i += 1
            continue
        g = m.groups()
        text = (g[8] or "").strip()
        if not text:  # SRT: text sits on the next line(s)
            j = i + 1
            buf = []
            while j < len(lines) and lines[j].strip() and not TS.match(lines[j].strip()):
                if not lines[j].strip().isdigit():
                    buf.append(lines[j].strip())
                j += 1
            text = " ".join(buf).strip()
            i = j
        else:
            i += 1
        if text:
            out.append((secs(*g[0:4]), secs(*g[4:8]), text))
    return out


def load_gaps(path):
    starts, gaps = [], []
    try:
        for line in open(path, encoding="utf-8", errors="replace"):
            a = re.search(r"silence_start:\s*([\d.]+)", line)
            b = re.search(r"silence_end:\s*([\d.]+)", line)
            if a:
                starts.append(float(a.group(1)))
            elif b and starts:
                gaps.append((starts.pop(), float(b.group(1))))
    except FileNotFoundError:
        pass
    return gaps


def sim(a, b):
    return SequenceMatcher(None, a.lower()[:70], b.lower()[:70]).ratio()


def main():
    cues = load(sys.argv[1])
    gaps = load_gaps(sys.argv[2]) if len(sys.argv) > 2 else []
    if not cues:
        print("# Cut list\n\nNo timestamped transcript found. Re-run whisper with timestamps.")
        return

    # Segment on silence gaps >= 1.2s
    bounds = sorted(g[1] for g in gaps)
    segs, cur = [], []
    bi = 0
    for c in cues:
        while bi < len(bounds) and c[0] > bounds[bi]:
            if cur:
                segs.append(cur)
                cur = []
            bi += 1
        cur.append(c)
    if cur:
        segs.append(cur)

    blocks = []
    for s in segs:
        text = " ".join(c[2] for c in s).strip()
        blocks.append({"start": s[0][0], "end": s[-1][1], "text": text,
                       "abort": bool(ABORT.search(text)), "slate": TAKE.search(text)})

    # Group near-duplicate openings as repeated attempts of the same line.
    for b in blocks:
        b["dupe_of"] = None
    for i, b in enumerate(blocks):
        if b["dupe_of"] is not None or len(b["text"]) < 25:
            continue
        for j in range(i + 1, len(blocks)):
            if len(blocks[j]["text"]) < 25:
                continue
            if sim(b["text"], blocks[j]["text"]) > 0.62:
                blocks[j]["dupe_of"] = i

    groups = {}
    for i, b in enumerate(blocks):
        root = b["dupe_of"] if b["dupe_of"] is not None else i
        groups.setdefault(root, []).append(i)

    print("# Proposed cut list\n")
    print(f"{len(blocks)} segments detected across {hms(blocks[-1]['end'])}. "
          "Nothing has been cut. Mark anything wrong and I will redo it.\n")
    print("**KEEP** = last clean attempt of a line. **CUT** = earlier attempt or an abort. "
          "**PICK** = two clean attempts, your call.\n")

    for root in sorted(groups):
        idxs = groups[root]
        clean = [i for i in idxs if not blocks[i]["abort"]]
        print(f"\n## Line {sorted(groups).index(root) + 1} "
              f"({len(idxs)} attempt{'s' if len(idxs) != 1 else ''})\n")
        for n, i in enumerate(idxs):
            b = blocks[i]
            last_clean = clean and i == clean[-1]
            if b["abort"]:
                tag = "CUT (abort)"
            elif last_clean and len(clean) > 1:
                tag = "PICK / leaning KEEP"
            elif last_clean:
                tag = "KEEP"
            else:
                tag = "CUT (earlier attempt)"
            slate = f"  _slate: {b['slate'].group(0)}_" if b["slate"] else ""
            print(f"- `{hms(b['start'])} - {hms(b['end'])}` **{tag}**{slate}")
            print(f"  > {b['text'][:220]}{'...' if len(b['text']) > 220 else ''}")
        if len(clean) > 1:
            print(f"\n  _{len(clean)} clean attempts here. I cannot judge delivery, so pick one._")

    print("\n---\n\n## ffmpeg extraction (after you approve)\n")
    print("```bash")
    for root in sorted(groups):
        idxs = [i for i in groups[root] if not blocks[i]["abort"]]
        if not idxs:
            continue
        b = blocks[idxs[-1]]
        n = sorted(groups).index(root) + 1
        print(f"ffmpeg -nostdin -i INPUT -ss {b['start']:.2f} -to {b['end']:.2f} "
              f"-c copy line-{n:02d}.mp4")
    print("```")


if __name__ == "__main__":
    main()
