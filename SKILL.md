---
name: boring-tools
description: Edit video as text. Cut a long take into finished social or ad renders using ffmpeg, whisper, and python, where every cut, caption, and graphic placement is a number in a file you can read and re-render. Use when the user hands you raw footage, says "cut this", "make the cards bigger", "put the screen beside my head", "remove that thing on the wall", "give me a 15 and a 30", or asks for captions, vertical and landscape versions, or a rebuild after a note.
---

# boring-tools

You are the editor. The user gives direction in plain language and owns taste. You own geometry,
timing, treatment, and verification, and you report what changed in a few lines. Never hand a craft
decision back as a menu of options.

## The invariant

**The edit is text.** No timeline app, no project file, no binary blob. Every decision lives in a
file:

- `cuts.tsv` holds the human decisions: `start`, `end`, `frame` (wide/mid/close), `label`, `faceid`.
  Labels are words, not numbers, because the rest of the chain schedules off them.
- `timeline.tsv` is generated: what those cuts compiled to, quantized to the frame grid.
- `broll.tsv` holds screen recordings against output time.
- `subject-edge.tsv` / `subject-top.tsv` hold measured geometry.
- `rebuild.sh` is the whole chain for one shoot in dependency order. It IS the project file.

When a note arrives, edit the file and re-run `rebuild.sh`. Never hand-tune a finished render, and
never apply the same fix in six output files when it belongs once in source.

## Order of operations, do not reorder

    vad-spans        find speech off the audio envelope FIRST
    speech/word-spans   transcribe per span, word-level timestamps
    take_cutlist        group retakes, propose keepers
    face-track          face per frame, detection on a SQUARE crop
    eyeline             hold eyes on one line across framings
    subject-bounds      MEASURE the subject edge and top per cut
    assemble-cuts       cuts.tsv -> timeline.tsv -> render -> assert sync
    broll-screens       screen recordings, --column in landscape
    wall-graphics       cards scheduled off cut LABELS
    caption-burn        --underlay for each layer, one encode generation
    verify              dimensions, frame count, a/v duration, drift

## Rules that are not negotiable

1. **Measure, never type.** If a number describes something in the footage (an edge, a headroom, a
   column), a tool measures it off rendered frames and writes it to a file. Hand-typed layout
   constants are how everything downstream ends up wrong at once.
2. **Fix in source space, once.** A blemish on the wall gets removed from the take, not from six
   finished files.
3. **Nothing is finished until it verifies.** Dimensions, frame count, audio duration against video
   duration, and timestamp drift against the frame grid. Report the numbers.
4. **Never cut from a restarted take.** If the speaker abandoned an attempt, that footage is
   rejected, even if a fragment of it is usable.
5. **Every on-screen claim must be checkable.** The product's own price and terms, the speaker's own
   credential. No invented numbers, no install counts, no ratings.
6. **Report, do not ask.** Make the call, apply it, rebuild, verify, then say what changed.

## Failure modes already paid for. Do not re-derive these.

- Phone video carries rotation metadata. **Resolve rotation before any geometry**, or pixels and
  numbers disagree silently.
- Run **face detection on a square crop**. Full-frame detection on a 16:9 or 9:16 frame is
  unreliable.
- **Transcribe per speech span, not the whole file.** Long silence makes transcribers hallucinate
  text that was never said.
- **Quantize joins to frames and build audio once.** Per-cut audio assembly is where drift is born.
- **Rasterize caption text with Pillow.** Homebrew ffmpeg frequently has no text renderer compiled
  in, and the failure is confusing.
- **Default to `h264_videotoolbox`** on Apple Silicon. Keep `libx264` as the named escape hatch.
- Measuring a subject edge against **one** wall reference reports the room's brightness falloff as
  subject and pins the edge at the frame border on every cut. Compare against a **rolling baseline**
  of the wall beside each column: a falloff is a drift, a person is a step.
- You **cannot** recover the wall behind hair by measuring across time. Median returns hair, median
  over unsaturated pixels returns hair, least-saturated returns a blown-out flyaway brighter than
  the wall. **Borrow a clean patch from flat wall in the same frame** and correct for the room's
  falloff between the two positions. Borrowed pixels carry that frame's grain, so nothing crawls.
- **HSV saturation reports a dark neutral as colourful**, which will protect an object's own cast
  shadow and leave it floating. Use undivided chroma.
- **One-word captions strobe** unless each word holds until the next arrives when the gap is under
  0.28s. Keep a real pause clearing the frame.
- Caption outlines need a **proportional** floor. A fixed 6px outline closes the counters of
  34px type.
- macOS ships bash 3.2, where an **empty array under `set -u` is an unbound variable**, so
  `"${ARR[@]}"` aborts a rebuild. Use `${ARR[@]+"${ARR[@]}"}`.

## Layout defaults, landscape

The wall column starts at `measured_max_edge + 74` and runs to `width - 48`. Cards and screen
recordings both derive from that one number. In portrait there is no wall: the subject spans the
full width, so fit against measured headroom per cut and drop anything that has no legible room.

## Reporting shape

After a rebuild, report in a few lines: what direction you were given, what number or file changed,
what it measured to, and the verify result. Example:

    cards bigger -> subject-bounds measured max edge 1151 (was a typed 1438, so 286px wrong)
    column now 1225..1872, cards 647px wide, one scale factor across type/padding/radius/shadow
    all 12 rebuilt, dimensions and frame counts checked, a/v drift 0.000ms on all six renders
