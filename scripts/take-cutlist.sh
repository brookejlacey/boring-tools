#!/usr/bin/env bash
# take-cutlist.sh: turn one long multi-take recording into a reviewable cut list.
#
#   scripts/take-cutlist.sh <video> [outdir]
#
# Produces, in outdir (default: streams/<date>-takes/):
#   audio.wav        16k mono, whisper input
#   transcript.txt   whisper.cpp output with timestamps
#   silences.txt     ffmpeg silencedetect boundaries (take gaps)
#   cutlist.md       proposed segments: repeated attempts grouped, last one marked KEEP
#
# It does NOT cut anything. It proposes. Brooke eyeballs cutlist.md, then
# scripts/clip_rank.py --cut or a plain ffmpeg pass does the actual extraction.
#
# Why last-attempt-wins: when someone reshoots a line in one continuous recording,
# the final attempt is nearly always the keeper. Flagged, never assumed.

set -uo pipefail
VIDEO="${1:-}"
[[ -z "$VIDEO" || ! -f "$VIDEO" ]] && { echo "usage: $0 <video> [outdir]" >&2; exit 2; }
OUT="${2:-streams/$(date +%Y-%m-%d)-takes}"
mkdir -p "$OUT"

echo "==> extracting audio"
ffmpeg -nostdin -loglevel error -y -i "$VIDEO" -ac 1 -ar 16000 -c:a pcm_s16le "$OUT/audio.wav"

echo "==> detecting take boundaries (silence >= 1.2s)"
ffmpeg -nostdin -hide_banner -i "$OUT/audio.wav" \
  -af silencedetect=noise=-32dB:d=1.2 -f null - 2> "$OUT/silences.raw"
grep -E "silence_(start|end)" "$OUT/silences.raw" > "$OUT/silences.txt" || true
echo "    $(grep -c silence_start "$OUT/silences.txt" 2>/dev/null || echo 0) gaps found"

echo "==> transcribing (whisper.cpp)"
MODEL="${WHISPER_MODEL:-$HOME/.config/whisper-models/ggml-base.en.bin}"
[[ -f "$MODEL" ]] || MODEL="$HOME/.cache/whisper/ggml-small.en.bin"
if [[ -z "${MODEL:-}" || ! -f "$MODEL" ]]; then
  echo "    no whisper model found; set WHISPER_MODEL" >&2; exit 3
fi
whisper-cli -m "$MODEL" -f "$OUT/audio.wav" -otxt -of "$OUT/transcript" --print-progress false >/dev/null 2>&1
[[ -f "$OUT/transcript.txt" ]] || whisper-cli -m "$MODEL" -f "$OUT/audio.wav" > "$OUT/transcript.txt" 2>/dev/null

echo "==> building cut list"
python3 "$(dirname "$0")/take_cutlist.py" "$OUT/transcript.txt" "$OUT/silences.txt" > "$OUT/cutlist.md"
echo "    wrote $OUT/cutlist.md"
echo
echo "Review $OUT/cutlist.md before anything is cut. Nothing has been deleted."
