#!/usr/bin/env bash
# Rebuild a whole ad set end to end. This is the real script from the shoot the
# example TSVs came from, with the paths turned into variables.
#
#   SHOOT=~/Movies/shoots/2026-08-20 TAKE=IMG_1234 example/rebuild.sh
#
# Runs against the CLEANED take (the switch plate on the wall behind her head is
# gone; see scripts/wall-clean.py), and lays the brand cards into the empty wall
# to camera-right before the captions go on. Layer order is b-roll, then
# graphics, then captions, all composited in caption-burn's single pass so the
# whole job still costs one extra encode generation.
#
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SHOOT="${SHOOT:-$HOME/Movies/shoots/YOUR-SHOOT}"
TAKE="${TAKE:-YOUR-TAKE}"        # basename of your source recording, no extension
ST="${ST:-$REPO/example}"
SRC="${SRC:-$SHOOT/work/$TAKE-clean.mov}"
CLIPS="$SHOOT/clips"
LAYERS="$SHOOT/work/layers"

[ -f "$SRC" ] || {
  echo "cleaned take missing; run:"
  echo "  python3 $REPO/scripts/wall-clean.py \\"
  echo "    $SHOOT/source/$TAKE.MOV $SRC --rect X,Y,W,H"
  exit 1
}

# The wall column is measured off a rendered landscape cut, once, and both the
# cards and the app screens are placed from it. Cheap to recompute and it means
# a reframe at the next shoot does not need anyone eyeballing pixel columns.
EDGE_FILE="$ST/subject-edge.tsv"

for LEN in 15 30 60; do
  for PAIR in vertical:1080x1920 16x9:1920x1080; do
    NAME="${PAIR%%:*}"; SIZE="${PAIR##*:}"
    W="${SIZE%%x*}"; H="${SIZE##*x}"
    CLIP="$CLIPS/ad-$LEN-$NAME.mp4"
    echo "== $LEN $NAME"

    python3 "$REPO/scripts/assemble-cuts.py" "$SRC" \
      "$ST/cuts-$LEN.tsv" "$ST/faces.tsv" "$CLIP" --size "$SIZE"

    FRAMES=$(ffprobe -v error -select_streams v:0 -show_entries stream=nb_frames \
             -of default=nw=1:nk=1 "$CLIP")

    COLARG=()
    if [ "$W" -gt "$H" ]; then
      [ -f "$EDGE_FILE" ] || python3 "$REPO/scripts/subject-edge.py" "$CLIP" \
        --out "$EDGE_FILE"
      EDGE=$(awk -F'\t' '/^#max/{print $2}' "$EDGE_FILE")
      X0=$(( EDGE + 74 )); CW=$(( W - X0 - 48 ))
      COLARG=(--column "$X0,$CW")
    fi

    rm -rf "$LAYERS/broll-$LEN-$NAME" "$LAYERS/gfx-$LEN-$NAME"
    # ${a[@]+"${a[@]}"}: macOS ships bash 3.2, where an EMPTY array under
    # `set -u` is an unbound variable, so the plain "${COLARG[@]}" aborts the
    # whole rebuild on the first vertical cut.
    python3 "$REPO/scripts/broll-screens.py" "$ST/broll-$LEN.tsv" \
      "$W" "$H" 30 "$FRAMES" "$LAYERS/broll-$LEN-$NAME" \
      ${COLARG[@]+"${COLARG[@]}"}
    python3 "$REPO/scripts/wall-graphics.py" "$CLIP" "$LAYERS/gfx-$LEN-$NAME" \
      --broll "$ST/broll-$LEN.tsv"

    python3 "$REPO/scripts/caption-burn.py" "$CLIP" "$SRC" \
      --underlay "$LAYERS/broll-$LEN-$NAME" \
      --underlay "$LAYERS/gfx-$LEN-$NAME"
  done
done

echo "==> $CLIPS"
