#!/usr/bin/env bash
# assemble-spans.sh: cut and join an explicit list of time spans from one source.
#
#   scripts/assemble-spans.sh SRC.MOV spans.txt OUT.mp4 [scale]
#
# spans.txt: one span per line, "start end label" in seconds.
#
# Single pass off the source. The earlier approach cut at transcript cue
# boundaries and then ran a second silence pass over the result, which
# compounded two sets of boundary errors and clipped speech onsets. Spans come
# from scripts/speech-spans.py, which already pads the head generously.
#
# Re-encodes rather than stream-copying because stream copy snaps to keyframes,
# which on a 30fps phone recording can drift by up to two seconds.

set -euo pipefail
SRC="${1:?usage: assemble-spans.sh SRC spans.txt OUT [scale]}"
SPANS="${2:?}"
OUT="${3:?}"
SCALE="${4:-1920:1080}"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

i=0
: > "$WORK/list.txt"
while read -r start end label; do
  [[ -z "${start:-}" || "$start" == \#* ]] && continue
  i=$((i+1))
  f="$(printf 'p%03d' $i)"
  ffmpeg -nostdin -v error -y -ss "$start" -to "$end" -i "$SRC" \
    -vf "scale=${SCALE}" \
    -c:v hevc_videotoolbox -q:v 58 -tag:v hvc1 \
    -c:a aac -b:a 192k -ar 48000 -ac 2 \
    "$WORK/$f.mp4"
  echo "file '$f.mp4'" >> "$WORK/list.txt"
  printf '  %-16s %7.3f -> %7.3f\n' "${label:-$f}" "$start" "$end"
done < "$SPANS"

( cd "$WORK" && ffmpeg -nostdin -v error -y -f concat -safe 0 -i list.txt \
    -c:v copy -c:a aac -b:a 192k "$OUT" )

echo "==> $i spans, $(ffprobe -v error -show_entries format=duration \
      -of default=nk=1:nw=1 "$OUT")s -> $OUT"
