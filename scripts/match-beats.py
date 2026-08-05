#!/usr/bin/env python3
"""match-beats.py: locate each scripted beat inside a long multi-take recording.

The silence-based cut list finds where takes START and STOP. It cannot tell you
which take is which line, because a 98-second stretch with no long pause can hold
five scripted beats. This does the other half: given the script we actually wrote,
it finds where each beat was spoken and returns exact in/out points.

  python3 scripts/match-beats.py transcript.srt beats.json

beats.json: {"asset": [{"id": "open", "text": "the words as written"}, ...], ...}

Scoring slides a variable-length window of transcript cues and takes the best
token-overlap match. Where several attempts score close, the LAST is preferred,
because a reshoot inside one continuous recording supersedes what came before.
"""
import json
import re
import sys
from difflib import SequenceMatcher

TS = re.compile(r"(\d+):(\d+):(\d+)[.,](\d+)\s*-->\s*(\d+):(\d+):(\d+)[.,](\d+)")
ABORT = re.compile(r"\b(hold on|wait|no|sorry|what the hell|nope|scratch that|again)\b", re.I)


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
        text = " ".join(buf).strip()
        if text:
            cues.append({"start": secs(*g[0:4]), "end": secs(*g[4:8]), "text": text})
        i = j
    return cues


def norm(s):
    return re.sub(r"[^a-z0-9 ]", " ", s.lower()).split()


def score(a, b):
    return SequenceMatcher(None, " ".join(norm(a)), " ".join(norm(b))).ratio()


def find(cues, target, max_cues=14):
    """Best-scoring window of consecutive cues, preferring later attempts on a tie."""
    best = None
    for i in range(len(cues)):
        for n in range(1, min(max_cues, len(cues) - i) + 1):
            win = cues[i:i + n]
            txt = " ".join(c["text"] for c in win)
            # Stop growing the window once it is much longer than the target.
            if len(txt) > len(target) * 1.9 and n > 1:
                break
            sc = score(target, txt)
            # >= keeps the LAST of equally good attempts.
            if best is None or sc >= best["score"]:
                best = {"score": sc, "start": win[0]["start"], "end": win[-1]["end"],
                        "text": txt, "i": i, "n": n}
    return best


def main():
    cues = load_srt(sys.argv[1])
    beats = json.load(open(sys.argv[2]))
    out = {}
    for asset, items in beats.items():
        print(f"\n=== {asset} ===")
        found = []
        for b in items:
            m = find(cues, b["text"])
            flag = ""
            if m["score"] < 0.45:
                flag = "  << LOW CONFIDENCE, eyeball this"
            elif ABORT.search(m["text"]):
                flag = "  << contains an abort word, check the boundary"
            print(f"{b['id']:<14} {m['score']:.2f}  {hms(m['start'])} - {hms(m['end'])}{flag}")
            print(f"               heard: {m['text'][:150]}")
            found.append({"id": b["id"], "start": round(m["start"], 2),
                          "end": round(m["end"], 2), "score": round(m["score"], 3),
                          "heard": m["text"]})
        out[asset] = found
    json.dump(out, open(sys.argv[3], "w") if len(sys.argv) > 3 else sys.stdout, indent=2)


if __name__ == "__main__":
    main()
