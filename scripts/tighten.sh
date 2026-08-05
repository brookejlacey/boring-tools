#!/usr/bin/env bash
# tighten.sh: remove the dead air from an assembled cut, video and audio together.
#
#   scripts/tighten.sh IN.mp4 OUT.mp4 [silence_seconds] [noise_db] [pad_seconds]
#
# ffmpeg's silenceremove only touches audio, which desyncs the picture. This
# detects silence, builds the complementary list of speech spans, and cuts those
# from the video with a small pad so words are not clipped at the boundaries.
#
# Defaults: gaps over 0.55s below -34dB are removed, 0.12s of pad kept either side.
# That is tuned for a script-reading shoot, where the pauses are long and obvious.

set -euo pipefail
IN="${1:?usage: tighten.sh IN OUT [gap] [db] [pad]}"
OUT="${2:?}"
GAP="${3:-0.55}"
DB="${4:--28}"
PAD="${5:-0.12}"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

DUR=$(ffprobe -v error -show_entries format=duration -of default=nk=1:nw=1 "$IN")
echo "==> source ${DUR}s, detecting silence (>${GAP}s below ${DB}dB)"

ffmpeg -nostdin -hide_banner -i "$IN" -af "silencedetect=noise=${DB}dB:d=${GAP}" \
  -f null - 2> "$WORK/sil.txt"

python3 - "$WORK/sil.txt" "$DUR" "$PAD" > "$WORK/spans.txt" <<'PY'
import re, sys
raw, dur, pad = open(sys.argv[1]).read(), float(sys.argv[2]), float(sys.argv[3])
starts = [float(x) for x in re.findall(r"silence_start:\s*([\d.]+)", raw)]
ends   = [float(x) for x in re.findall(r"silence_end:\s*([\d.]+)", raw)]
if len(ends) < len(starts):      # trailing silence runs to end of file
    ends.append(dur)
spans, cur = [], 0.0
for s, e in zip(starts, ends):
    a, b = cur, min(s + pad, dur)
    if b - a > 0.25:             # drop slivers
        spans.append((a, b))
    cur = max(0.0, e - pad)
if dur - cur > 0.25:
    spans.append((cur, dur))
for a, b in spans:
    print(f"{a:.3f} {b:.3f}")
PY

N=$(wc -l < "$WORK/spans.txt" | tr -d ' ')
KEPT=$(awk '{s+=$2-$1} END {printf "%.1f", s}' "$WORK/spans.txt")
echo "==> ${N} speech spans, ${KEPT}s of ${DUR}s kept"

i=0
: > "$WORK/list.txt"
while read -r a b; do
  i=$((i+1))
  ffmpeg -nostdin -v error -y -ss "$a" -to "$b" -i "$IN" \
    -c:v hevc_videotoolbox -q:v 55 -tag:v hvc1 -c:a aac -b:a 192k \
    "$WORK/s$(printf '%03d' $i).mp4"
  echo "file 's$(printf '%03d' $i).mp4'" >> "$WORK/list.txt"
done < "$WORK/spans.txt"

( cd "$WORK" && ffmpeg -nostdin -v error -y -f concat -safe 0 -i list.txt -c copy "$OUT" )
echo "==> $(ffprobe -v error -show_entries format=duration -of default=nk=1:nw=1 "$OUT")s -> $OUT"
