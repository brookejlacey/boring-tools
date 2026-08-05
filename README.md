# boring tools

Agent-driven video editing with ffmpeg, whisper, and python. No timeline, no project file,
no subscription, nothing uploaded anywhere.

The whole idea is one sentence: **the edit is text.** Every cut, every caption, every graphic
placement is a number in a file your agent can read, diff, and re-render. You give direction in
plain language ("cards bigger", "put the phone beside my head", "remove the white plug behind my
head"), the agent edits numbers in files, and one command rebuilds the entire set.

I shot a 23 minute 4K one-take, and this pipeline cut it into twelve finished ad renders in three
lengths and two aspect ratios, captioned, with brand graphics composited into the empty wall beside
me, and every render verified frame-exact against its audio. I have never opened an editor.

    $ python3 verify.py
    jayla-ad-60-16x9.mp4     1920x1080   1615   53.833   53.833   0.000ms  PASS
    ... 12 files, all PASS

## Why it works better than a video tool

A timeline is a binary blob. An agent cannot read it, cannot diff it, cannot tell you what changed,
and cannot re-render it after a note. So every round of feedback goes back through a human dragging
clips.

Text does not have that problem. `cuts.tsv` holds nineteen lines that say which moments to keep and
what to call them. `timeline.tsv` is what those lines compiled to, quantized to the frame.
`subject-edge.tsv` holds the measured pixel column where the wall behind me starts. When I say the
cards are too small, the fix is one file, one command, and every one of the twelve renders inherits
it. Nothing is hand-tuned twice.

## The chain

Each of these is boring on purpose. There is no model in the loop doing the actual video work.

| Step | Tool | What it does |
|---|---|---|
| 1 | `vad-spans.py` | Finds where speech is, off the audio envelope. Run this **before** transcribing, or long silences make the transcriber hallucinate. |
| 2 | `speech-spans.py`, `word-spans.py` | Per-span transcription with word-level timestamps. Words are what captions and beat matching both need. |
| 3 | `take_cutlist.py`, `take-cutlist.sh` | One long recording usually holds many attempts at the same line. Groups repeats, flags the last attempt as the keeper, proposes rather than cuts. |
| 4 | `match-beats.py` | Locates a scripted beat inside a long take, so a written outline can address footage. |
| 5 | `face-track.py` | Where the face is per frame, so a crop can follow it. Run detection on a square crop, not the full frame. |
| 6 | `eyeline.py` | Keeps eyes on the same horizontal line across cuts of different framings, which is the difference between a cut and a jolt. |
| 7 | `subject-bounds.py` | Measures the subject's real edge and top per cut. This is the tool that replaced a hand-typed constant that was 286 pixels wrong. |
| 8 | `assemble-cuts.py` | Reads `cuts.tsv`, emits `timeline.tsv`, renders, and asserts sync before it will call the render finished. |
| 9 | `broll-screens.py` | Composites screen recordings. `--column` puts a phone in the wall beside the speaker instead of taking the whole frame. |
| 10 | `wall-graphics.py` | Brand cards scheduled off cut **labels**, not timestamps, so one plan serves the 15, the 30, and the 60, and survives a recut. |
| 11 | `caption-burn.py` | One word at a time, white with a black outline, rasterized with Pillow. Composites b-roll and graphics in the same pass so the job costs one encode generation. |
| 12 | `wall-clean.py` | Removes a fixed object from a wall across every frame by borrowing clean wall from the same frame. Nothing is synthesised. |

Extras: `blur-background.py` (background matting, span aware so it only mattes kept frames),
`clip_rank.py` (scores windows of a long recording for clip potential), `tighten.sh`,
`cut-silence.sh`, `assemble-spans.sh`.

## Install

macOS, Apple Silicon. Everything is local.

    brew install ffmpeg whisper-cpp
    pip install pillow numpy

Then point your agent at `SKILL.md`. That file is the part that matters: it is the operating
instructions the agent reads, including the failure modes it must not re-derive.

    git clone https://github.com/brookejlacey/boring-tools
    cp -r boring-tools/scripts your-project/scripts/
    cp boring-tools/SKILL.md your-project/.claude/skills/boring-tools/SKILL.md

## The one command

Every shoot gets a `rebuild.sh` that is the entire chain for that footage, in dependency order,
with the reasoning in comments. See `example/rebuild.sh`. Reading it tells you exactly how a set
was built, and running it rebuilds all twelve files from source. That script is the project file.

## What is recorded in here, and why that is the valuable part

Every one of these tools carries its failed approaches in its docstring, with the reason each one
failed. That is deliberate. An agent handed a clean tool will cheerfully re-derive the same three
wrong answers, so the wrong answers are written down next to the right one.

A few of them, so you can see the shape:

- **Resolve phone rotation before doing any geometry.** A vertical recording carries rotation
  metadata, so the pixels and the numbers disagree until you settle it.
- **Never measure a wall against a single reference column.** A room falls off in brightness across
  frame, roughly 15 levels in mine, and a naive detector reports that gradient as the subject and
  puts the edge at the frame border on every cut. Compare against a rolling baseline of the wall
  beside it instead: a falloff is a drift, and a person is a step.
- **You cannot recover what sits behind hair by measuring over time.** Median returns hair.
  Median over unsaturated pixels returns hair. Least saturated returns a blown-out flyaway and
  comes back brighter than the wall. Borrow the patch from flat wall in the same frame instead,
  because borrowed pixels carry that frame's own grain and exposure and therefore do not crawl.
- **HSV saturation calls a dark neutral colourful**, which protects a fixture's cast shadow and
  leaves it hanging in mid-air. Undivided chroma does not.
- **Rasterize caption text with Pillow.** Homebrew ffmpeg often ships with no text renderer.
- **One-word captions blink.** A single word disappears in every inter-word gap and 30 to 80ms
  intra-phrase gaps strobe, so hold each word until the next arrives when the gap is under 0.28s.
  A real pause still clears the frame.
- **Assert sync, do not eyeball it.** `assemble-cuts.py` refuses to call a render finished until
  video timestamps sit on the frame grid and audio matches picture. That check exists because a
  drift once shipped in a render where everything else verified clean, and the only broken property
  was the one nobody had checked.

## Standing decisions

Not preferences. These were each measured, and re-litigating them costs a rebuild.

- **Layout numbers are measured off rendered footage, never typed.** The constant that
  `subject-bounds.py` replaced was 286 pixels wrong, which is why every graphic had been small.
- **16:9 puts screen recordings in the column beside the speaker**, not full frame. 9:16 keeps the
  full-frame cutaway, because in vertical the speaker spans the whole width and there is no wall.
- **Cards scale by one factor** covering type, padding, radius, and shadow, so card size is a
  property of the measured geometry rather than a pile of numbers.
- **Captions: one word, white, black outline, no yellow**, at 0.032 of frame height.
- **Every claim on a card has to be true and checkable.** The product's own price and access terms.
  No install counts, no ratings, no invented numbers.
- **Hardware encode by default** (`h264_videotoolbox`), measured faster with no visible cost.

## What this is not

It is not a model that edits video for you, and it is not a hosted service. There is no upload
step, so your footage and your voice stay on your own machine. That was the point.

## License

MIT. Take it apart.
