# The Rollup — karaoke caption burner

Burns The Rollup's short-form caption style onto **any** video: a single centred line of
heavy uppercase type, white with a thin black outline, where the **currently spoken word**
sits on a yellow box with black text. Word-accurate, one word lit at a time. The Rollup corner
logo burns into the top-left **by default**, in the same pass — see §"Logo bug".

Reference: `https://x.com/therollupco/status/2125221475476992326` (68 s, 1920x1080).
Every number in `references/style-spec.md` was measured off that render at pixel level.


## Setup (run this first)

The agent installs the toolchain itself into this folder. Do not ask the user to
install Python, uv, or ffmpeg.

```bash
# Windows
powershell -ExecutionPolicy Bypass -File .\setup.ps1

# macOS / Linux
./setup.sh
```

Setup is idempotent and stays inside `.tools/` and `.venv/`. It needs no admin
rights and does not change the system PATH. Involve the user only when something
strictly requires them, and say exactly why (an API key, an admin prompt if no
user-space option exists, or a license acceptance). After setup:

```bash
# Windows PowerShell
. .\.tools\env.ps1

# macOS / Linux
source .tools/env.sh
```

`scripts/tooling.py` also resolves ffmpeg and the venv automatically.

## Placement — the one rule that matters

**Captions go at the bottom.** The reference clip's baseline sat at 66.8% of the frame, but
that was *that shot's* framing, not a design rule — on these DJI clips it reads as mid-frame
and crosses the subject's chest. Default is the **bottom safe rim (88.5%)**.

`--placement auto` (default) samples 24 frames and asks whether something is already burned
into the bottom of the picture. If it finds a graphic there — a name strap, a ticker rule, a
logo lockup — it lifts the caption to the reference position (66.8%) so the two never collide.

Detection looks for a long, perfectly straight horizontal edge inside the band the caption
would occupy: at least one row where >35% of the frame width is a hard vertical step. Natural
scenery never produces that (its edges are short and broken); a strap does, on essentially
every frame. Validated both ways — 24/24 on a clip with a synthetic lower third, 0/24 on all
four clean DJI clips.

Override when the automatic call is wrong:

```
--placement bottom   always the bottom safe rim
--placement mid      always the reference 66.8% position
```

## What it produces

| Property | Value at 1920x1080 |
|---|---|
| Face | Barlow **Bold** (700), uppercase |
| Cap height | **49 px** (4.537% of frame height) |
| Baseline / cap-top | cap band **y 674–722** at the reference position (see Placement) |
| Inactive word | `#FFFFFF`, fill only, **3 px** black outline |
| Active word | black text on **`#FEEB02`** yellow box |
| Box | **74 px** tall, padding **16 top / 9 bottom / 6 x**, square corners, no outline |
| Layout | one line, **centred**, no shadow, no scrim, no animation |
| Baseline | **bottom safe rim, 88.5%** of frame height (not the reference's 66.8% — see Placement) |
| Max line width | ~86% of frame width, wraps at a word boundary beyond that |
| Tracking | **−0.01 em** (essentially none — see `references/style-spec.md` §"On the font") |
| Corner logo | The Rollup lockup, top-left, **18.8% W × 6.85% H** — burned by default, same pass |

Type is **static per page**. The only thing that moves is the yellow box hopping word to
word. Do not add punch-ins, rises or fades; the reference has none.

## Placement

`--placement auto` (default) samples the clip's lower band for burned-in furniture. No lower
thirds → captions sit at the **bottom safe rim** (baseline 88.5% of frame height). A strap or
ticker down there → they lift to the reference position (66.8%, mid-frame). `bottom` / `mid`
force either. The reference clip's own mid-frame baseline was its shot's framing, not a rule.

## Logo bug (on by default)

The bug ships with the skill (`assets/logo/`) and burns **in the same pass as the type** — one
encode, never a second one. It is on unless you pass `--no-logo`; `--logo FILE` swaps the asset.
It must not be opt-in: an optional logo means someone forgets it, and adding it to a finished file
afterwards costs a full re-encode of the delivery.

```bash
python scripts/captionburn.py burn --src in.mp4 --words words.json --out final.mp4            # logo ON
python scripts/captionburn.py burn --src in.mp4 --words words.json --out final.mp4 --no-logo  # no bug
```

The bug holds through the gaps between caption pages, and is sized and placed from the artwork's
**ink** bbox, not the PNG canvas — these assets carry a wide soft drop shadow and empty padding,
so anchoring the canvas would add ~20% of the ink height of dead space to both rims.

| Property | Default | At 3840x2160 |
|---|---|---|
| Ink height | 6.85% of frame height (= the caption box height) | 148 px |
| Left rim | 4.0% of frame width | 154 px |
| Top rim | 5.0% of frame height | 108 px |
| Aspect | preserved from the ink box, never stretched | 723 px wide |

Tying the logo's ink height to the caption box height (74 px at 1080p) is what makes the bug
read as the same weight as the caption line. Override with `--logo-ink-h`, `--logo-mx`,
`--logo-my` (all fractions of the frame).

The only reason to use the separate `logo` subcommand is repair: a file was delivered without the
bug. That path re-encodes the master, which is exactly the cost the default above avoids.

## Why not `ffmpeg ass=`

libass `BorderStyle=3` draws the box around the **em box** (90 px at this size), not the cap
height (49 px). It cannot produce a 74 px box with 16/9 asymmetric padding. So the type is
rendered with Pillow for exact geometry and composited with ffmpeg. Tested and rejected —
see `references/style-spec.md` §"Rejected approaches".

## Requirements

Run setup first. It provides a project-local Python 3.12, Pillow, numpy, and a
portable ffmpeg/ffprobe. The bundled font is `assets/fonts/Barlow-Bold.ttf`
(OFL — see `assets/fonts/OFL-Barlow.txt`).

## Inputs

| Input | Required | Notes |
|---|---|---|
| `--src` video | yes | any resolution; all metrics scale off frame height |
| `--words` word-timed JSON | yes* | see schema below — **word timings are what make the karaoke work** |
| `--srt` | fallback | cue-level only; word times are interpolated evenly, so the highlight drifts on long cues |
| `--out` | yes | delivered MP4 |

\* Word-level timing is strongly preferred. `--srt` works but is visibly worse.

### Word-timing JSON

The schema this skill and `rollup-intro` both consume (`scripts/transcribe.py` emits it):

```json
{"cues": [
  {"id": 1, "start": 0.0, "end": 4.2, "text": "...",
   "words": [{"start": 0.10, "end": 0.32, "text": "Virtually"}, ...]}
]}
```

To produce it, reuse the transcription engine from the Rollup intro skill if that skill is also present, or any other word-timed transcript that matches the schema above.

## Transcript, names and terms (do this before the burn)

`--words` is literally what appears on screen, and ASR mangles names and domain terms far more
often than ordinary speech. A wrong brand or person's name is the most visible defect this
deliverable can carry. Full workflow and worked examples: `references/transcript-edit.md`.

The three rules:

1. **Verify, don't guess.** A word changes only when you have evidence. Plausibility is not
   evidence.
2. **Everything else stays verbatim** — `um`, stutters, doubled words, spoken grammar. Captions
   transcribe; they do not rewrite the speaker.
3. **Report the pass** — every change with the evidence that settled it, plus every unresolved
   line as `time — on screen now — probably meant to be`. Say explicitly that nothing else moved.

Verify against, in this order: **the footage itself** (lower thirds, step-and-repeat, decks — the
client's own graphic, and free), **the same speaker elsewhere in the shoot**, **public record**
(press, filings, exchange notices) for companies and people, then **canonical brand spelling**.
Never against your own best guess.

```bash
python scripts/edit_words.py words.json words_final.json --spec spec.json
```

`merge` (N tokens → 1) fixes splits like `ZK`+`Sync` that the one-word-at-a-time highlight must not
separate; `phrase` (N → M, times split) handles `a era` → `an era` without lighting two words at
once; `spell` is one-for-one with punctuation preserved. Keep the raw `words.json` and burn from
`words_final.json` so the decision stays auditable.

## Usage

```bash
# burn
python scripts/captionburn.py burn --src input.mp4 --words words.json --out final.mp4

# burn only the logo onto a master that is already captioned or graded (one overlay pass,
# no caption re-render) — use this when a delivered file is missing just the bug
python scripts/captionburn.py logo --src final.mp4 --logo bug.png --out final_logo.mp4

# check one frame before committing to a full render (fast)
python scripts/captionburn.py preview --src input.mp4 --words words.json \
    --at 33.5 --out check.png

# measure the delivered file the same way the reference was measured
python scripts/captionburn.py qc --src final.mp4
```

Both `burn` and `logo` take `--encoder nvenc` for GPU encoding (several times faster than
libx264 at the same quality number; see `_video_encoder`).

`preview` writes a PNG of the real frame with the caption composited. **Always look at a
preview before a full burn** — it catches a bad word grouping in one second instead of
after a five-minute encode.

## How it works

0. **Edit the transcript** (`scripts/edit_words.py`) — names and terms only, evidence required, see
   the section above. Everything not touched stays exactly as spoken.
1. **Group words into pages.** Break on a >0.55 s gap, >8 words, or a line that will not fit
   inside 86% of the frame width (wraps at a word boundary, never mid-word).
2. **Render one PNG per state** — one image per lit word, plus one transparent image for
   every gap between pages. The corner logo is composited into each of these images, so the bug
   costs no extra pass. A 60 s clip is typically ~120 images, not 1800 frames.
3. **Build the overlay** with ffmpeg's concat demuxer, holding each image for its real
   duration, encoded to `qtrle` (lossless RGBA).
4. **Composite** `overlay` onto the source, `-c:a copy`.

Rendering per *state* rather than per *frame* is what keeps this fast: cost scales with the
number of words, not with the length of the video.

## Verification (do this, don't eyeball)

`qc` re-measures the output and prints the cap band, box height and colours. It should
report, for a 1080p source:

```
cap band   674..722  (h=49)   PASS
box height 74                  PASS
yellow     (254,237,2)         PASS
```

## Traps worth knowing

- **Measure the caption band, not the frame.** A venue banner has big white logos in the
  same rows as the caption. Thresholding the whole frame silently measures the banner. This
  cost real time during authoring — `qc` restricts to a band and a centre window.
- **Do not use ASS `BorderStyle=3`.** It boxes the em square, not the cap height.
- **Windows + ffmpeg filter paths.** Inside `-vf` a path needs its drive colon escaped
  (`H\:/dir/f.ass`) and forward slashes. Irrelevant here because we never pass a subtitle
  file to ffmpeg, but it bites if you extend this.
- **The `--words` text is what goes on screen.** Run the names/terms pass before burning — ASR
  regularly mangles names and acronyms (`DCCC` for DTCC, `The Roll` for The Rollup). See
  `references/transcript-edit.md`.
- **Apostrophes**: ASR often emits the typographic `’`. The reference uses a straight `'`
  rendered heavy. Both render, but be consistent.

## Files

```
SKILL.md
setup.ps1 / setup.sh          no-admin toolchain install
scripts/bootstrap.py          cross-platform setup entry
scripts/tooling.py            local ffmpeg / venv resolver
scripts/test_smoke.py         compile and tool check
scripts/captionburn.py        renderer + burn + qc (+ corner logo bug, + nvenc)
scripts/edit_words.py         transcript pass: merge / phrase / spell, spec-driven
references/style-spec.md      every measured number, and what was rejected
references/transcript-edit.md names and terms: verification ladder, recurring ASR errors, the tool
assets/logo/the_rollup_logo_white_dropshadow.png   the corner bug, burned by default
assets/fonts/Barlow-Bold.ttf  the reference face
assets/fonts/OFL-Barlow.txt
assets/fonts/Montserrat-Black.ttf   kept for reference only — see §"On the font" in the spec
assets/fonts/OFL.txt
```
