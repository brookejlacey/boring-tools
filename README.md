# boring tools

**Claude edits your video. You talk, it works.**

You don't cut anything. You don't open a timeline. You hand it a recording, tell it what you want the
way you'd tell a person and watch it happen in the terminal.

I shot one take on my phone and got twelve finished ads back. Three lengths, two aspect ratios,
captions burned in, brand cards on the wall beside my head. I haven't opened an editing app since
July.

You don't need to be technical for this. You need to know what you want.

## Do this

**A. Install the free parts.** This is a macOS tool. Apple Silicon is what the fast encode wants.

```
brew install ffmpeg whisper-cpp
pip3 install pillow numpy opencv-python mediapipe
mkdir -p ~/.cache/whisper
curl -L -o ~/.cache/whisper/ggml-small.en.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin
```

The last one is the speech model, about 500MB. It reads your words off the audio, so captions and
dead-air cutting both need it.

If pip refuses with `externally-managed-environment`, add `--break-system-packages`. That is
Homebrew guarding its own Python and these are ordinary libraries.

**B. Give your agent the instructions.**

```
git clone https://github.com/brookejlacey/boring-tools
mkdir -p your-project/scripts your-project/.claude/skills/boring-tools
cp -r boring-tools/scripts/. your-project/scripts/
cp boring-tools/verify.py your-project/scripts/
cp boring-tools/SKILL.md your-project/.claude/skills/boring-tools/SKILL.md
```

**C. Tell it what you want.**

```
here's my recording: ~/Movies/take.mov
cut the dead air, give me a 15, a 30 and a 60, vertical and landscape,
burn in captions and put my brand cards on the empty wall to my right
```

That's it. It reads the instructions, does the work and shows you every step as it goes.

## Things worth saying to it

Real notes that produced real changes. Say them like this.

- *cut the dead air*
- *the cards are too small*
- *put the phone beside my head instead of over my face*
- *take that white plug off the wall behind my head*
- *find my product's site and build the cards from whatever branding you find*
- *my eyes jump between cuts, fix it*
- *prove the audio still lines up*

That last one matters more than it looks. Ask it to prove its work and it will write itself a check
that fails loudly next time.

## Where this runs

Claude Code in a terminal. The desktop app is the same thing with a window around it, so that works
too. The browser version won't, because your footage isn't in it.

Everything runs on your own machine. Nothing gets uploaded, so your face and your voice stay where you
left them.

---

# Appendix: what's actually happening

You can stop reading at the line above and this still works. The rest is for when something breaks, or
when you want to know why it made a choice.

## The one idea

**The edit is text.** No timeline, no project file. Decisions live in a `cuts.tsv` holding a start, an
end, a framing and a name per cut. That compiles to a `timeline.tsv` snapped to the frame grid.

That is why a note works at all. An agent can read a text file, change one number and rebuild every
output. It can do none of that to a timeline blob, which is why every other approach ends with you
back in the app dragging rectangles.

You are not meant to edit those files by hand. They exist so the agent has something it can address.

## The chain

| Step | Tool | What it does |
|---|---|---|
| 1 | `vad-spans.py` | Finds speech off the audio envelope. Run **before** transcribing, or silence makes the transcriber hallucinate. |
| 2 | `speech-spans.py`, `word-spans.py` | Per-span transcription with word-level timing. |
| 3 | `take_cutlist.py` | Groups repeated attempts at the same line, flags the last as the keeper. Proposes, never cuts. |
| 4 | `match-beats.py` | Finds a scripted beat inside a long take. |
| 5 | `face-track.py` | Face per frame so crops follow. Detection runs on a square crop. |
| 6 | `eyeline.py` | Holds eyes on one line across cuts of different tightness. |
| 7 | `subject-bounds.py` | Measures the subject's real edge and top per cut. Replaced a typed constant that was 286px wrong. |
| 8 | `assemble-cuts.py` | `cuts.tsv` in, `timeline.tsv` out, renders, asserts sync. |
| 9 | `broll-screens.py` | Screen recordings. `--column` puts a phone in the wall beside the speaker. |
| 10 | `wall-graphics.py` | Brand cards, scheduled off cut **labels** so one plan serves every length and survives a recut. |
| 11 | `caption-burn.py` | One word at a time. Composites the other layers in the same pass so the job costs one encode generation. |
| 12 | `wall-clean.py` | Removes a fixed object from a wall across a whole take by borrowing clean wall from the same frame. |
| 13 | `verify.py` | Dimensions, frame count, audio against video, drift off the grid. Non-zero exit on failure. |

Extras: `blur-background.py` (wants `pip install torch`, which the rest of the chain does not),
`clip_rank.py`, `tighten.sh`, `cut-silence.sh`, `assemble-spans.sh`.

One `rebuild.sh` per shoot runs the chain in order. `example/rebuild.sh` is the real one from the
shoot this was built on, sitting next to its cut list, timeline and measured geometry. `verify.py`
you run over the finished renders yourself. It exits non-zero if any of them is wrong.

## Failure modes already paid for

Every tool carries its failed approaches in its docstring with the reason each one failed. An agent
handed a clean tool will re-derive the same wrong answers, so the wrong answers live next to the right
one.

- **Resolve phone rotation before any geometry.** Metadata and pixels disagree until you do.
- **Run face detection on a square crop**, not the full frame.
- **Transcribe per speech span.** A long silence makes a transcriber invent sentences.
- **Rasterize caption text with Pillow.** Most ffmpeg builds ship with no text renderer.
- **Never measure a subject edge against one wall reference.** A room falls off in brightness across the
  frame, about 15 levels in mine. A naive detector calls that gradient a person and pins the edge at
  the frame border on every cut. Compare each column against a rolling baseline of the wall beside it: a
  falloff is a drift, a person is a step.
- **You cannot recover what sits behind hair by measuring over time.** Median returns hair. Median over
  unsaturated pixels returns hair. Least-saturated returns a blown-out flyaway brighter than the wall.
  Borrow the patch from flat wall in the same frame instead, so it carries that frame's own grain and does
  not crawl.
- **HSV saturation calls a dark neutral colourful**, which protects an object's cast shadow and leaves it
  floating in mid-air. Use undivided chroma.
- **One-word captions strobe** unless each word holds until the next arrives when the gap is under 0.28s.
- **macOS ships bash 3.2**, where an empty array under `set -u` is unbound, so a plain `"${ARR[@]}"` aborts
  a rebuild on the first vertical cut. Use `${ARR[@]+"${ARR[@]}"}`.

## Standing decisions

Measured, not preferences.

- **Layout numbers are measured off rendered footage, never typed.**
- **Landscape puts screen recordings in the column beside the speaker.** Vertical keeps the full-frame
  cutaway, because there the speaker spans the whole width and there is no wall.
- **Cards scale by one factor** across type, padding, radius and shadow.
- **Captions: one word, white, black outline, 0.032 of frame height.**
- **Every claim on a card has to be checkable.** No install counts, no ratings, no invented numbers.
- **Hardware encode by default** (`h264_videotoolbox`), measured faster at no visible cost.

## License

MIT. Take it apart.
