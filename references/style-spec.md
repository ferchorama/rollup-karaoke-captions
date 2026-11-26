# Rollup karaoke captions — measured style spec

All numbers measured from `https://x.com/therollupco/status/2125221475476992326`
(The Rollup, 68.074 s, 1920x1080, 30 fps, H.264 + AAC) at native resolution.
Source of truth for `scripts/captionburn.py`. If the code and this file disagree, the file wins
and the code gets fixed.

## Frame

| | |
|---|---|
| Resolution | 1920x1080 |
| Caption band | **y 674–722** (cap height 49 px) |
| Baseline | y 722 |
| Vertical centre of cap band | y 698 |
| Horizontal | single line, centred on frame centre (measured median line centre **956**, i.e. 4 px left of 960 — sub-pixel centring of glyph ink; treat as centred) |

Cap height is **4.537 %** of frame height. Everything below is expressed both in 1080p pixels
and as a ratio of cap height so it scales to any output size.

## Type

| Property | 1080p | ratio of cap |
|---|---|---|
| Font | **Barlow** Bold (700) | — |
| Case | UPPERCASE only | — |
| Cap height | 49 px | 1.00 |
| Outline (inactive) | black, **3 px** | 0.061 |
| Fill (inactive) | `#FFFFFF` | — |
| Fill (active) | `#000000` on yellow | — |
| Tracking | **−0.01 em** (i.e. essentially none) | — |
| Shadow / glow / scrim | **none** | — |
| Animation | **none** — type is static, only the box moves | — |

### On the font — corrected fit

The face and the tracking were first fitted by matching the reference **line width** only, and
that produced Montserrat Black at **−0.14 em**. Line width is not enough evidence: it cannot
tell "narrow glyphs at normal spacing" from "wide glyphs with the gaps squeezed out", and the
second reading is what got shipped. At −0.14 em, Montserrat's ~95 px of excess width comes out
of the 10 inter-letter gaps, whose natural width is only 2–6 px, so every gap goes negative and
the word rasterises as **one continuous black mass** instead of 11 letters.

Measured on the reference crops (`_analysis/ref_challengers.png`, `ref_ensure.png`), the
"CHALLENGERS" box is 478×74 with **11 separate glyph blocks, 4–6 px of air between each**, ink
454 px wide (9.27 w/cap). Refitting on all three signals at once — ink width, per-glyph width
pattern, and mask IoU at equal cap height — gives:

| Font | size @ cap 49 | natural ink w | w/cap | tracking needed | mask IoU |
|---|---|---|---|---|---|
| **Barlow Bold** | 69 | **454** | **9.27** | **0.00 em** | **0.850** |
| Barlow *(same file)* | 69 | 454 | 9.27 | 0.00 em | 0.850 |
| Sarabun Bold | 71 | 474 | 9.67 | −0.041 em | 0.653 |
| Titillium / Titillium Web Black | 74 | 467 | 9.53 | −0.027 em | 0.562 |
| Anek Latin Bold | 76 | 453 | 9.24 | +0.002 em | 0.510 |
| Kanit Bold | 77 | 513 | 10.47 | −0.120 em | 0.499 |
| Montserrat Black | 70 | 549 | 11.20 | −0.194 em | 0.470 |
| Montserrat ExtraBold | 69 | 537 | 10.96 | −0.169 em | 0.465 |
| Roboto | 69 | 464 | 9.47 | −0.020 em | 0.453 |
| Chivo | 71 | 489 | 9.98 | −0.071 em | 0.427 |

Barlow Bold is the only candidate that lands on the reference ink width **at zero tracking**,
which is the tell: its natural glyph widths and side bearings are the reference's. Verified on a
second, independent reference word — "ABILITIES." (ink 312 px, 10 blocks, gaps 4–7 px): Barlow
Bold at −0.01 em gives ink 309 px, 10 blocks, IoU 0.798; Montserrat Black never exceeds 0.542 at
any tracking.

The earlier sweep did contain Barlow — but only the **Condensed** cut (7.02 w/cap), which is
indeed far too narrow and was correctly rejected. The regular width was never tried.

A residual ~1 px per glyph and one extra px of gap remain versus the reference; that is
rasteriser anti-aliasing at 49 px cap, not a design difference.


## Yellow highlight box

| Property | 1080p | ratio of cap |
|---|---|---|
| Fill | **`#FEEB02`** — measured `(254, 237, 2)` | — |
| Height | **74 px** | 1.51 |
| Padding top | **16 px** | 0.327 |
| Padding bottom | **9 px** | 0.184 |
| Padding left/right | **6 px** | 0.122 |
| Corners | square, no radius | — |
| Own outline | **none** | — |
| Text inside | black `#000000`, no outline | — |

The box is anchored to the **cap band**, not the baseline: it starts 16 px above cap-top and
ends 9 px below the baseline. That asymmetry is what makes it look tight above and airy below.
Box height = 49 + 16 + 9 = **74 px** ✓ measured on every cue sampled.

Measured consistency across six different words — height 74 and pad top 16 / pad bottom 9 on
every single one:

```
CHALLENGERS     h=74  padT=16 padB=9   (w=478)
CAPABILITIES.   h=74  padT=16 padB=9   (w=450)
REALITY         h=74  padT=16 padB=9   (w=264)
THIS            h=74  padT=16 padB=9   (w=162)
GUARD.          h=74  padT=16 padB=9   (w=194)
```

## Timing behaviour

The highlight is **true karaoke**: exactly one word is boxed at a time, and it advances on the
spoken word. Measured at 8 fps over 21–31 s — the box x-range steps monotonically through the
line, word by word, e.g.:

```
21.00  x 818..990    ("OFF")
21.75  x 990..1092   ("GUARD.")
22.00  x 432..640    ← new page begins
22.25  x 641..763
22.38  x 764..997
```

So: box position is a pure function of which word is being spoken. No easing between words,
no interpolation — it cuts.

### Pages

One line per page. Between pages the whole line is replaced and the box goes with it. A page
ends when:

- a silence of **> 0.55 s** occurs between words, or
- the line would exceed **86 %** of frame width (the widest observed cue,
  `AND THEN YOU'RE GONNA SEE THIS STUFF COMING.`, is 82.6 %), or
- a hard cap of **8 words** is reached.

Wrapping happens at a word boundary. A word is never split or hyphenated.

### No auto-shrink

Cap height stayed **49 px on every cue measured**, including the longest
(`AND THEN YOU'RE GONNA SEE THIS STUFF COMING.`, 82.6 % wide) and the shortest. The renderer
does not shrink long lines — it wraps them onto a new page. Confirmed by measuring strict-white
cap height on five cues: 49, 48, 49, 49, 49.

## Rejected approaches

### `ffmpeg` + libass (`ass` / `subtitles` filter)

libass draws `BorderStyle=3` (opaque box) around the **font em box**, not the cap height.
Calibrated at font size 70 (cap 49) the smallest achievable box was **82–90 px tall** with
`padT=26..34` (target 74 / 16) — the em box includes ascender and descender space that is
empty for an all-caps line. No combination of `Outline`, `Shadow` or `Spacing` changes this,
because the box is derived from font metrics.

Also: an ASS box cannot have different top and bottom padding, which is the defining feature of
this look.

**Conclusion:** exact geometry requires drawing the rectangles ourselves. Pillow + ffmpeg
`overlay` is used instead. Cost is ~120 rendered images for a 60 s clip (one per lit word, plus
one transparent image per inter-page gap), not one per frame.

For reference, the ASS invocation that worked (before rejection) needed Windows paths escaped
inside the filtergraph — drive colon as `\:`, and forward slashes:

```
-vf "ass=filename='H\:/dir/f.ass':fontsdir='H\:/dir/fonts'"
```

Without the escape you get `No option name near '...'` / `Error parsing a filter description`.

### Measuring the whole frame

The venue backdrop in the reference footage carries large white "ETHERIUM ALLIANCE" artwork in
**the same rows as the caption** (y ~640–740), plus a yellow diagonal rule. Thresholding the
full frame measures the banner and reports garbage — e.g. caption "line width" of 3 px on one
frame and 1383 px on the next. Every measurement here is restricted to the caption band
(y 640–790) intersected with a centre window (x 380–1520), and to *strict* white
(`>238` all channels) which the off-white banner art fails.

This cost several wasted passes during authoring. `qc` in `scripts/captionburn.py` implements
the restricted window so the trap is not re-entered.

## Scaling

All geometry is a ratio of **cap height**, which is `0.04537 × frame height`. So for a target
`H`:

```
cap      = 0.04537 * H
baseline = 0.66852 * H      (722/1080)
outline  = 0.0612  * cap
pad_top  = 0.3265  * cap
pad_bot  = 0.1837  * cap
pad_x    = 0.1224  * cap
max_line = 0.86    * W
```

Portrait (1080x1920) works with the same ratios; only the wrap width in words needs to drop,
which the 86 %-of-width rule already handles.
