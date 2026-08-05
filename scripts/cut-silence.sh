#!/usr/bin/env bash
# cut-silence.sh: drop every gap from a recording in a single decode pass.
#
#   scripts/cut-silence.sh SRC.MOV spans.txt OUT.mp4 [scale]
#
# spans.txt: "start end label" per line, from scripts/speech-spans.py.
#
# assemble-spans.sh encodes each span separately and concats, which is right for
# a handful of hand-picked pieces. At 148 spans that is 148 process spawns and
# 148 encoder warmups. This builds one select/aselect filter instead: one decode,
# one encode, no concat seams.
#
# setpts/asetpts restamp the surviving frames so the output has continuous
# timestamps rather than the holes the select leaves behind.

set -euo pipefail
SRC="${1:?usage: cut-silence.sh SRC spans.txt OUT [scale]}"
SPANS="${2:?}"
OUT="${3:?}"
SCALE="${4:-}"

CHUNK="${CHUNK:-25}"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT

# One filter with 148 between() terms fails filter-graph allocation, so batch
# them. Still 6 process spawns instead of 148, and each batch is one decode.
grep -vE '^\s*(#|$)' "$SPANS" > "$WORK/all.txt"
split -l "$CHUNK" "$WORK/all.txt" "$WORK/part-"

TOTAL=$(wc -l < "$WORK/all.txt" | tr -d ' ')
echo "==> $TOTAL spans in $(ls "$WORK"/part-* | wc -l | tr -d ' ') batches"

: > "$WORK/list.txt"
n=0
for part in "$WORK"/part-*; do
  n=$((n+1))
  V=""
  while read -r a b _; do
    [[ -z "${a:-}" ]] && continue
    V+="${V:+ + }between(t\,${a}\,${b})"
  done < "$part"
  VF="select='${V}',setpts=N/FRAME_RATE/TB"
  [[ -n "$SCALE" ]] && VF+=",scale=${SCALE}"
  out="$WORK/$(printf 'c%03d' $n).mp4"
  ffmpeg -nostdin -hide_banner -v error -y -i "$SRC" \
    -vf "$VF" -af "aselect='${V}',asetpts=N/SR/TB" \
    -c:v hevc_videotoolbox -q:v 60 -tag:v hvc1 \
    -c:a aac -b:a 192k -ar 48000 -ac 2 "$out"
  echo "file '$(basename "$out")'" >> "$WORK/list.txt"
  printf '    batch %d done\n' "$n"
done

( cd "$WORK" && ffmpeg -nostdin -hide_banner -v error -y -f concat -safe 0 \
    -i list.txt -c:v copy -c:a aac -b:a 192k "$OUT" )

echo "==> $(ffprobe -v error -show_entries format=duration -of default=nk=1:nw=1 "$OUT")s -> $OUT"
