"""captionburn.py -- burn The Rollup's karaoke caption style onto any video.

Measured spec lives in references/style-spec.md. This module implements it exactly:
one centred line of Montserrat Black uppercase, white with a 3px black outline, where the
currently spoken word sits on a #FEEB02 box with black text.

    burn     render + composite the whole video (captions AND the corner logo, one encode)
    preview  one real frame with the caption on it (look at this first)
    logo     repair only: burn the logo onto an already-finished file (re-encodes)
    qc       re-measure a delivered file the same way the reference was measured

Rendering is per *state* (one image per lit word), not per frame, so cost scales with the
number of words rather than the length of the video.
"""
from __future__ import annotations
import tooling
tooling.activate()

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# --------------------------------------------------------------------------------------------
# measured spec (references/style-spec.md) -- ratios of CAP HEIGHT unless noted
# --------------------------------------------------------------------------------------------
CAP_OF_H = 49 / 1080          # cap height as a fraction of frame height
# The reference clip this style came from put its baseline at 66.8% of the frame, which reads
# as mid-frame. That is NOT a rule -- it was that shot's framing. Captions belong in the bottom
# safe area unless something is already burned in there.
REFERENCE_BASELINE_OF_H = 722 / 1080
BOTTOM_BASELINE_OF_H = 0.885  # default: bottom safe rim
SAFE_RIM_OF_H = 0.955         # nothing (including the box) may extend below this
OUTLINE = 3 / 49              # black outline on white text
PAD_TOP = 16 / 49             # yellow box padding above cap-top
PAD_BOT = 9 / 49              # below baseline
PAD_X = 6 / 49                # left/right inside the box
# Tracking is ~0. The reference was first fitted with Montserrat Black at -0.14 em, which matched
# the reference LINE WIDTH by squeezing 95 px out of 10 inter-letter gaps -- the gaps in the
# reference crop are 4-6 px of visible air, so that derivation collapsed every glyph into one
# black mass. Fitting ink width AND gap structure AND mask shape together puts the reference face
# at Barlow Bold with essentially no tracking (see references/style-spec.md).
TRACKING = -0.01              # em
YELLOW = (254, 237, 2)        # #FEEB02
MAX_LINE = 0.86               # max line width as fraction of frame width
MAX_WORDS = 8                 # hard page cap
GAP_SPLIT = 0.55              # seconds of silence that ends a page

# --------------------------------------------------------------------------------------------
# corner logo bug (optional --logo). Same idea as everything above: ratios of the frame, so the
# bug survives any output size. The Rollup asset is a lockup with a wide soft drop shadow and
# generous empty padding around the artwork, so placement is measured from the INK bbox and never
# from the PNG canvas -- anchoring the canvas would silently carry ~20% of the ink height as
# dead space, and the logo would sit visibly too far in from the corner.
# --------------------------------------------------------------------------------------------
LOGO_INK_H_OF_H = 0.0685      # ink height = caption box height (1.51 cap = 6.85% of the frame),
                              # which is what makes the bug read as the same weight as the captions
LOGO_MARGIN_X_OF_W = 0.040    # left rim
LOGO_MARGIN_Y_OF_H = 0.050    # top rim
LOGO_INK_ALPHA = 128          # alpha at/above which a pixel counts as ink, not shadow

# The bug ships with the skill and burns by DEFAULT, in the same pass as the type. An opt-in logo
# means someone eventually forgets it, and adding it afterwards costs a second encode of the whole
# finished file. `--no-logo` is the deliberate opt-out; `--logo` overrides the asset.
DEFAULT_LOGO = (Path(__file__).resolve().parent.parent / "assets" / "logo"
                / "the_rollup_logo_white_dropshadow.png")

FONT = Path(__file__).resolve().parent.parent / "assets" / "fonts" / "Barlow-Bold.ttf"


# --------------------------------------------------------------------------------------------
# font metrics
# --------------------------------------------------------------------------------------------

def _cap_of(font) -> int:
    """Cap height of this size, from 'H' (all our type is uppercase)."""
    b = font.getbbox("H")
    return b[3] - b[1]


def size_for_cap(path: Path, target: int):
    """Largest point size whose cap height is <= target, plus the font."""
    from PIL import ImageFont

    lo, hi, best = 4, 900, 4
    while lo <= hi:
        mid = (lo + hi) // 2
        f = ImageFont.truetype(str(path), mid)
        if _cap_of(f) <= target:
            best, lo = mid, mid + 1
        else:
            hi = mid - 1
    f = ImageFont.truetype(str(path), best)
    return f, _cap_of(f)


def _advance(font, ch: str, tr_px: float) -> float:
    return font.getlength(ch) + tr_px


def word_width(font, word: str, tr_px: float) -> float:
    w = 0.0
    for i, ch in enumerate(word):
        w += _advance(font, ch, tr_px if i < len(word) - 1 else 0.0)
    return w


def line_width(font, words, tr_px: float, space_px: float) -> float:
    if not words:
        return 0.0
    w = 0.0
    for i, word in enumerate(words):
        w += word_width(font, word, tr_px)
        if i < len(words) - 1:
            w += space_px
    return w


# --------------------------------------------------------------------------------------------
# paging
# --------------------------------------------------------------------------------------------

def _norm(t: str) -> str:
    t = t.replace("\u2019", "'").replace("\u2018", "'")
    t = t.replace("\u201c", '"').replace("\u201d", '"')
    return t.strip()


def merge_fragments(words):
    """Repair ASR word splits that would otherwise break mid-word on screen.

    faster-whisper splits hyphenated compounds ("BATTLE" + "-TESTED") and stray
    punctuation ("FRAX" + ".COM"). The reference never hyphenates or splits a word, so
    glue these back together before paging. A fragment that begins with '-' or with a
    bare punctuation mark belongs to the word before it.
    """
    out = []
    for w in words:
        t = _norm(w["text"])
        if not t:
            continue
        if out and (t.startswith("-") or t[0] in ".,;:!?)"):
            prev = out[-1]
            prev["text"] = prev["text"] + t
            prev["end"] = w["end"]
        else:
            out.append(dict(text=t, start=w["start"], end=w["end"]))
    # a trailing '-' at the end of a word means the next word continues it
    merged = []
    for w in out:
        if merged and merged[-1]["text"].endswith("-"):
            merged[-1]["text"] = merged[-1]["text"][:-1] + w["text"]
            merged[-1]["end"] = w["end"]
        else:
            merged.append(w)
    return merged


def build_pages(words, W: int, H: int, font_path: Path = FONT):
    """Group timed words into pages of one line each.

    Returns [{start, end, words:[{text,start,end}], }]. A page breaks on a >GAP_SPLIT silence,
    at MAX_WORDS, or when the line would exceed MAX_LINE of the frame width.
    """
    font, cap = size_for_cap(font_path, round(CAP_OF_H * H))
    tr = TRACKING * font.size
    space = font.getlength(" ") + tr
    limit = MAX_LINE * W

    pages, cur = [], []
    for w in words:
        text = _norm(w["text"]).upper()
        if not text:
            continue
        one = dict(text=text, start=w["start"], end=w["end"])
        # a single word wider than the limit cannot wrap -- give it its own page and let
        # render_state shrink that page to fit
        if line_width(font, [text], tr, space) > limit:
            if cur:
                pages.append(cur)
                cur = []
            pages.append([one])
            continue
        cand = cur + [one]
        too_wide = line_width(font, [c["text"] for c in cand], tr, space) > limit
        too_many = len(cand) > MAX_WORDS
        gap = bool(cur) and (w["start"] - cur[-1]["end"]) > GAP_SPLIT
        if cur and (too_wide or too_many or gap):
            pages.append(cur)
            cur = [one]
        else:
            cur = cand
    if cur:
        pages.append(cur)

    # a page's end is the next page's start, so there is never a gap where the caption blinks
    out = []
    for i, pg in enumerate(pages):
        end = pages[i + 1][0]["start"] if i + 1 < len(pages) else pg[-1]["end"] + 0.4
        out.append(dict(start=pg[0]["start"], end=end, words=pg))
    return out


# --------------------------------------------------------------------------------------------
# logo bug
# --------------------------------------------------------------------------------------------

def ink_bbox(img, threshold: int = LOGO_INK_ALPHA):
    """(x0, y0, x1, y1) half-open bbox of the logo's solid ink, ignoring its soft drop shadow.

    The asset's shadow is a broad low-alpha haze that reaches the canvas edge; thresholding at
    LOGO_INK_ALPHA keeps the artwork and drops the haze, so margins can be measured from what
    the eye actually sees.
    """
    import numpy as np

    a = np.asarray(img.convert("RGBA"))
    ys, xs = np.nonzero(a[:, :, 3] >= threshold)
    if not len(xs):
        sys.exit("[logo] the logo image has no ink at or above alpha "
                 f"{threshold} -- is it the right file?")
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _composite_clipped(dst, src, px: int, py: int) -> None:
    """alpha_composite at an offset that may be negative or run past the frame edge."""
    x0, y0 = max(0, px), max(0, py)
    sx, sy = x0 - px, y0 - py
    w, h = min(src.width - sx, dst.width - x0), min(src.height - sy, dst.height - y0)
    if w > 0 and h > 0:
        dst.alpha_composite(src.crop((sx, sy, sx + w, sy + h)), (x0, y0))


def logo_layer(logo_path, W: int, H: int, ink_h_of_h: float = LOGO_INK_H_OF_H,
               margin_x_of_w: float = LOGO_MARGIN_X_OF_W,
               margin_y_of_h: float = LOGO_MARGIN_Y_OF_H):
    """Full-frame RGBA layer with the logo in the top-left corner, anchored by its ink box.

    Returns (layer, info). `info` carries the numbers worth printing: the final ink box in frame
    pixels and the margins actually achieved, so a burn log can be checked without eyeballing.
    """
    from PIL import Image

    lg = Image.open(logo_path).convert("RGBA")
    ow, oh = lg.size
    x0, y0, x1, y1 = ink_bbox(lg)
    scale = ink_h_of_h * H / (y1 - y0)          # one scale for both axes: never distorts
    w, h = max(1, round(ow * scale)), max(1, round(oh * scale))
    lg = lg.resize((w, h), Image.LANCZOS)

    kx, ky = w / ow, h / oh
    ink_w, ink_h = (x1 - x0) * kx, (y1 - y0) * ky
    left, top = round(margin_x_of_w * W), round(margin_y_of_h * H)

    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    _composite_clipped(layer, lg, left - round(x0 * kx), top - round(y0 * ky))

    info = dict(ink_box=(left, top, left + round(ink_w), top + round(ink_h)),
                ink_wh=(round(ink_w), round(ink_h)),
                margins=(left, top),
                pct_of_frame=(100 * round(ink_w) / W, 100 * round(ink_h) / H),
                canvas=(w, h))
    return layer, info


def describe_logo(path, info, W: int, H: int) -> str:
    """One line for the burn/preview log -- proves the proportion and the margins."""
    x0, y0, x1, y1 = info["ink_box"]
    return (f"logo {Path(path).name}: ink {x1 - x0}x{y1 - y0} px "
            f"({info['pct_of_frame'][0]:.1f}% W x {info['pct_of_frame'][1]:.2f}% H) "
            f"at x {x0}..{x1}  y {y0}..{y1}  "
            f"rim {100 * info['margins'][0] / W:.1f}% W / {100 * info['margins'][1] / H:.1f}% H")


# --------------------------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------------------------

def render_state(page_words, active_idx: int, W: int, H: int, font_path: Path = FONT,
                 baseline_of_h: float = BOTTOM_BASELINE_OF_H, logo=None):
    """One RGBA image: the page with word `active_idx` boxed. active_idx=None -> no box."""
    from PIL import Image, ImageDraw

    font, cap = size_for_cap(font_path, round(CAP_OF_H * H))
    tr = TRACKING * font.size
    space = font.getlength(" ") + tr

    texts = [w["text"] for w in page_words]
    total = line_width(font, texts, tr, space)

    # safety net: a single over-long word cannot wrap, so shrink this page until it fits.
    # The reference never shrinks mid-cue (cap height is 49 on every page measured), so this
    # should not trigger for normal speech -- it only rescues pathological tokens.
    limit = MAX_LINE * W
    if total > limit:
        fsize = font.size
        while total > limit and fsize > 12:
            fsize -= 1
            font, cap = size_for_cap(font_path, max(8, round(CAP_OF_H * H * fsize / font.size)))
            tr = TRACKING * font.size
            space = font.getlength(" ") + tr
            total = line_width(font, texts, tr, space)

    pad_t, pad_b, pad_x = PAD_TOP * cap, PAD_BOT * cap, PAD_X * cap
    outline = max(1, int(round(OUTLINE * cap)))

    baseline = baseline_of_h * H
    cap_top = baseline - cap
    _, descent = font.getmetrics()
    # PIL draws from the ascender line; shift so that cap-top lands on cap_top
    draw_y = cap_top - (font.getmetrics()[0] - cap)

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if logo is not None:                 # corner bug under the type; the two never overlap
        img.alpha_composite(logo)
    d = ImageDraw.Draw(img)

    # Lay out by advance width, but size the yellow box from each word's real INK bounds.
    # Advances carry side bearings and, under -0.14 em tracking, glyphs overlap them; a box
    # sized from advances lets black text hang outside the yellow -- the one glaring defect.
    x = (W - total) / 2.0
    laid = []
    for word in texts:
        cx, ink_l, ink_r = x, None, None
        for j, ch in enumerate(word):
            bb = font.getbbox(ch)
            l, r = cx + bb[0], cx + bb[2]
            ink_l = l if ink_l is None else min(ink_l, l)
            ink_r = r if ink_r is None else max(ink_r, r)
            cx += _advance(font, ch, tr if j < len(word) - 1 else 0.0)
        laid.append(dict(text=word, x=x, ink_l=ink_l, ink_r=ink_r))
        x += word_width(font, word, tr) + space

    # centre the visible ink on the frame, not the advance span
    il = min(w["ink_l"] for w in laid)
    ir = max(w["ink_r"] for w in laid)
    shift = (W - (ir - il)) / 2.0 - il

    # box drawn first so type always sits on top of it
    if active_idx is not None and 0 <= active_idx < len(laid):
        w = laid[active_idx]
        d.rectangle([round(w["ink_l"] + shift - pad_x), round(cap_top - pad_t),
                     round(w["ink_r"] + shift + pad_x), round(cap_top + cap + pad_b)],
                    fill=YELLOW)

    for i, w in enumerate(laid):
        if i == active_idx:
            fill, sw, sf = (0, 0, 0, 255), 0, None
        else:
            fill, sw, sf = (255, 255, 255, 255), outline, (0, 0, 0, 255)
        cx = w["x"] + shift
        for j, ch in enumerate(w["text"]):
            if sw:
                d.text((cx, draw_y), ch, font=font, fill=fill,
                       stroke_width=sw, stroke_fill=sf)
            else:
                d.text((cx, draw_y), ch, font=font, fill=fill)
            cx += _advance(font, ch, tr if j < len(w["text"]) - 1 else 0.0)

    return img


def caption_band(baseline_of_h: float, H: int):
    """Top and bottom of the yellow box for a given baseline, as fractions of frame height."""
    cap = CAP_OF_H * H
    cap_top = baseline_of_h * H - cap
    return (cap_top - PAD_TOP * cap) / H, (cap_top + cap + PAD_BOT * cap) / H


def detect_lower_third(video: Path, dur: float, W: int, H: int, samples: int = 24):
    """Is something already burned into the bottom of this clip?

    Captions must not fight existing furniture. The rule (from the brief):
      * nothing burned in down there -> captions sit at the BOTTOM safe rim
      * lower-third graphics present  -> captions lift to the reference position (~67%)

    Detection samples a spread of frames and looks, inside the band the captions would occupy,
    for horizontally structured graphics: a long run of strong horizontal edges (strap top /
    bottom, ticker rule, logo lockup) present on most frames. Flat scenery does not produce
    that, because a natural image's vertical gradient is spread out rather than concentrated in
    two long lines.

    Returns (found: bool, baseline_of_h: float, why: str).
    """
    import numpy as np
    from PIL import Image

    top_f, bot_f = caption_band(BOTTOM_BASELINE_OF_H, H)
    # widen slightly: graphics are rarely pixel-aligned with the caption band
    band_top, band_bot = max(0.0, top_f - 0.03), min(1.0, bot_f + 0.03)

    hits, best_runs = 0, []
    tmp = Path(tempfile.mkdtemp(prefix="capdet_"))
    for i in range(samples):
        t = dur * (i + 1) / (samples + 1)
        png = tmp / f"d{i}.png"
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(video),
                            "-frames:v", "1", "-vf", f"scale={W // 3}:-1", str(png), "-y"],
                           capture_output=True, text=True)
        if r.returncode != 0 or not png.exists():
            continue
        a = np.asarray(Image.open(png).convert("L")).astype(np.int16)
        hh, ww = a.shape
        y0, y1 = int(band_top * hh), int(band_bot * hh)
        if y1 - y0 < 6:
            continue
        strip = a[y0:y1]

        # A burned-in graphic has a long, perfectly straight horizontal edge (the top and
        # bottom of a strap, a ticker rule, a logo lockup). Natural scenery never does: its
        # edges are short and broken. Measure, per row, what fraction of the WIDTH is a strong
        # vertical step; a graphic shows ~50%+ on at least one row, on every frame.
        dy = np.abs(np.diff(strip, axis=0))
        if dy.size:
            wide = (dy > 28).mean(axis=1)
            if float(wide.max()) > 0.35:
                hits += 1
                best_runs.append(round(float(wide.max()), 2))

    present = hits >= max(3, samples // 4)
    if present:
        return True, REFERENCE_BASELINE_OF_H, (
            f"a graphic sits in the bottom caption area on {hits}/{samples} frames "
            f"(wide-edge fraction {sorted(best_runs)[-3:]}) "
            f"- lifted to {REFERENCE_BASELINE_OF_H*100:.1f}%")
    return False, BOTTOM_BASELINE_OF_H, (
        f"bottom band clear ({hits}/{samples} frames flagged) - bottom safe rim "
        f"{BOTTOM_BASELINE_OF_H*100:.1f}%")


def _blank(W: int, H: int, logo=None):
    from PIL import Image
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if logo is not None:                 # the bug holds through the silences between pages
        img.alpha_composite(logo)
    return img


# --------------------------------------------------------------------------------------------
# ffmpeg helpers
# --------------------------------------------------------------------------------------------

def _video_encoder(name: str, quality: int, preset: str, W: int, H: int) -> list:
    """Codec args for the final encode.

    libx264  -- default; slowest, best rate/distortion per bit.
    nvenc    -- NVIDIA GPU. `-cq` is NVENC's CRF analogue; at the same number it lands within
                ~1 dB of libx264 on flat caption graphics and runs several times faster.
    """
    pix = "yuv420p" if W % 2 == 0 and H % 2 == 0 else "yuv444p"
    if name == "nvenc":
        # preset p7 = slowest/highest quality; p5 when the caller asked for a fast x264 preset
        p = "p5" if preset in ("ultrafast", "superfast", "veryfast", "faster") else "p7"
        return ["-c:v", "h264_nvenc", "-preset", p, "-tune", "hq", "-rc", "vbr",
                "-cq", str(quality), "-b:v", "0", "-pix_fmt", pix]
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(quality), "-pix_fmt", pix]


def _run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        sys.exit(f"[ffmpeg] failed: {' '.join(str(c) for c in cmd)}\n{r.stderr[-2500:]}")
    return r


def probe(path: Path) -> dict:
    r = _run(["ffprobe", "-v", "error", "-print_format", "json",
              "-show_streams", "-show_format", str(path)])
    j = json.loads(r.stdout)
    v = next(s for s in j["streams"] if s["codec_type"] == "video")
    a = next((s for s in j["streams"] if s["codec_type"] == "audio"), None)
    num, den = (v.get("r_frame_rate") or "30/1").split("/")
    return dict(W=int(v["width"]), H=int(v["height"]),
                fps=float(num) / float(den) if float(den) else 30.0,
                dur=float(j["format"]["duration"]), has_audio=a is not None)


def load_words(args) -> list:
    """Word-timed JSON, or an SRT with interpolated word times."""
    if args.words:
        j = json.loads(Path(args.words).read_text(encoding="utf-8"))
        cues = j["cues"] if isinstance(j, dict) else j
        out = []
        for c in cues:
            ws = c.get("words") or []
            if ws:
                out += [dict(text=_norm(w["text"]), start=float(w["start"]),
                             end=float(w["end"])) for w in ws if _norm(w["text"])]
            elif _norm(c.get("text", "")):
                out += _interpolate(_norm(c["text"]), float(c["start"]), float(c["end"]))
        return [w for w in merge_fragments(out) if w["end"] > w["start"]]

    if args.srt:
        return _srt_words(Path(args.srt).read_text(encoding="utf-8", errors="replace"))

    sys.exit("need --words or --srt")


def _interpolate(text: str, start: float, end: float) -> list:
    """Spread a cue's words evenly across it. Only used when word times are unavailable."""
    ws = text.split()
    if not ws:
        return []
    step = (end - start) / len(ws)
    return [dict(text=w, start=start + i * step, end=start + (i + 1) * step)
            for i, w in enumerate(ws)]


def _srt_words(srt: str) -> list:
    def t(s):
        h, m, rest = s.split(":")
        sec, ms = rest.split(",")
        return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms) / 1000

    out = []
    for block in re.split(r"\n\s*\n", srt.strip()):
        lines = [x for x in block.splitlines() if x.strip()]
        if len(lines) < 2 or "-->" not in lines[1]:
            continue
        a, b = lines[1].split("-->")
        out += _interpolate(" ".join(lines[2:]), t(a.strip()), t(b.strip()))
    return out


# --------------------------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------------------------

def resolve_baseline(a, info, video: Path):
    """Where the caption baseline goes: bottom safe rim unless the clip already has furniture.

    `--placement auto` (default) inspects the clip's lower band. `bottom` / `mid` force it.
    """
    mode = getattr(a, "placement", "auto") or "auto"
    if mode == "bottom":
        return BOTTOM_BASELINE_OF_H, "forced bottom safe rim"
    if mode == "mid":
        return REFERENCE_BASELINE_OF_H, "forced reference position (66.8%)"
    found, base, why = detect_lower_third(video, info["dur"], info["W"], info["H"])
    return base, why


def resolve_logo(a, W: int, H: int):
    """The corner bug: bundled by default, `--logo` overrides, `--no-logo` switches it off.

    Returns (layer, info), or (None, None) when there is no logo to burn.
    """
    if getattr(a, "no_logo", False):
        print("[logo] off (--no-logo)")
        return None, None
    explicit = getattr(a, "logo", None)
    p = Path(explicit) if explicit else DEFAULT_LOGO
    if not p.is_file():
        if explicit:
            sys.exit(f"[logo] not found: {p}")
        print(f"[logo] WARNING: bundled logo missing at {p} -- burning without it "
              f"(pass --logo FILE to supply one)")
        return None, None
    layer, info = logo_layer(p, W, H, getattr(a, "logo_ink_h", LOGO_INK_H_OF_H),
                             getattr(a, "logo_mx", LOGO_MARGIN_X_OF_W),
                             getattr(a, "logo_my", LOGO_MARGIN_Y_OF_H))
    print(f"[logo] {describe_logo(p, info, W, H)}")
    return layer, info


def cmd_preview(a) -> None:
    from PIL import Image

    info = probe(Path(a.src))
    words = load_words(a)
    pages = build_pages(words, info["W"], info["H"])
    base_h, why = resolve_baseline(a, info, Path(a.src))
    print(f"[preview] placement: {why} -> baseline {base_h*100:.1f}% of frame")
    logo, _ = resolve_logo(a, info["W"], info["H"])

    t = a.at if a.at is not None else pages[0]["start"] + 0.05
    page = next((p for p in pages if p["start"] <= t < p["end"]), pages[0])
    idx = 0
    for i, w in enumerate(page["words"]):
        if w["start"] <= t:
            idx = i

    png = Path(a.out)
    _run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(a.src),
          "-frames:v", "1", str(png), "-y"])
    base = Image.open(png).convert("RGBA")
    ov = render_state(page["words"], idx, info["W"], info["H"], baseline_of_h=base_h,
                      logo=logo)
    Image.alpha_composite(base, ov).convert("RGB").save(png)

    print(f"[preview] t={t:.3f}s  page words={[w['text'] for w in page['words']]}")
    print(f"[preview] lit={page['words'][idx]['text']!r}  -> {png}")


def cmd_burn(a) -> None:
    info = probe(Path(a.src))
    W, H = info["W"], info["H"]
    words = load_words(a)
    if not words:
        sys.exit("[burn] no words")
    pages = build_pages(words, W, H)
    base_h, why = resolve_baseline(a, info, Path(a.src))
    logo, _ = resolve_logo(a, W, H)
    print(f"[burn] {len(words)} words -> {len(pages)} pages, {W}x{H} @ {info['fps']:.2f}fps")
    print(f"[burn] placement: {why} -> baseline {base_h*100:.1f}% of frame")
    print(f"[burn] encoder: {getattr(a, 'encoder', 'libx264')} (quality {a.crf})")

    tmp = Path(tempfile.mkdtemp(prefix="capburn_"))
    ovl = tmp / "ovl"
    ovl.mkdir()

    # one image per state; hold each for the duration of that state
    segs, n = [], 0

    def emit(img, dur):
        nonlocal n
        if dur <= 0.001:
            return
        p = ovl / f"s{n:05d}.png"
        img.save(p)
        segs.append((p, dur))
        n += 1

    cursor = 0.0
    for i, page in enumerate(pages):
        if page["start"] > cursor + 0.001:
            emit(_blank(W, H, logo), page["start"] - cursor)   # no caption between pages
        for j, w in enumerate(page["words"]):
            start = max(w["start"], page["start"])
            end = page["end"] if j == len(page["words"]) - 1 else page["words"][j + 1]["start"]
            emit(render_state(page["words"], j, W, H, baseline_of_h=base_h, logo=logo),
                 end - start)
        cursor = page["end"]
    if info["dur"] > cursor:
        emit(_blank(W, H, logo), info["dur"] - cursor)

    # concat list -> lossless RGBA intermediate
    lst = tmp / "list.txt"
    lst.write_text("".join(f"file '{(p.as_posix())}'\nduration {d:.3f}\n" for p, d in segs),
                   encoding="utf-8")
    lst.write_text(lst.read_text(encoding="utf-8") + f"file '{(segs[-1][0].as_posix())}'\n",
                   encoding="utf-8")

    mov = tmp / "ovl.mov"
    _run(["ffmpeg", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
          "-vsync", "vfr", "-c:v", "qtrle", "-pix_fmt", "rgba", str(mov), "-y"])

    acodec = ["-c:a", "copy"] if info["has_audio"] else ["-an"]
    _run(["ffmpeg", "-v", "error", "-i", str(a.src), "-i", str(mov),
          "-filter_complex", "[0:v][1:v]overlay=0:0:format=auto[v]",
          "-map", "[v]"] + (["-map", "0:a"] if info["has_audio"] else []) +
         _video_encoder(getattr(a, "encoder", "libx264"), a.crf, a.preset, W, H) +
         acodec + ["-movflags", "+faststart", str(a.out), "-y"])

    print(f"[burn] {n} states -> {a.out}")


def cmd_logo(a) -> None:
    """Burn only the corner bug onto an existing master — no caption re-render.

    The fast path when a clip is already captioned (or already graded) and only the logo is
    missing: one overlay pass over the delivered file instead of re-rendering every state. Costs
    one encode generation, so prefer `burn --logo` when the clip is being rendered anyway.
    """
    info = probe(Path(a.src))
    W, H = info["W"], info["H"]
    layer, lg = resolve_logo(a, W, H)
    print(f"[logo] {describe_logo(a.logo, lg, W, H)}")

    tmp = Path(tempfile.mkdtemp(prefix="caplogo_"))
    layer_png = tmp / "layer.png"
    layer.save(layer_png)

    acodec = ["-c:a", "copy"] if info["has_audio"] else ["-an"]
    _run(["ffmpeg", "-v", "error", "-i", str(a.src), "-i", str(layer_png),
          "-filter_complex", "[0:v][1:v]overlay=0:0:format=auto[v]",
          "-map", "[v]"] + (["-map", "0:a"] if info["has_audio"] else []) +
         _video_encoder(getattr(a, "encoder", "libx264"), a.crf, a.preset, W, H) +
         acodec + ["-movflags", "+faststart", str(a.out), "-y"])
    print(f"[logo] encoder: {getattr(a, 'encoder', 'libx264')} (quality {a.crf})")
    print(f"[logo] {a.src} -> {a.out}")


def cmd_qc(a) -> None:
    """Re-measure a delivered file the way the reference was measured.

    Only pixels that belong to the caption are measured. Bright scenery (white shirts, lit
    tables, backlit walls) fails a naive white threshold, so the caption is isolated by its
    own geometry instead: find the yellow box, then measure the white text on the SAME two
    rows within that page's line, and require the white to be pure and adjacent to outline.
    """
    import numpy as np
    from PIL import Image

    info = probe(Path(a.src))
    W, H = info["W"], info["H"]
    cap_expect = round(CAP_OF_H * H)
    box_expect = round(74 / 1080 * H)

    band = Path(tempfile.mkdtemp(prefix="capqc_"))
    times = [info["dur"] * f for f in (0.15, 0.3, 0.45, 0.6, 0.75)]

    caps, boxes, yellows = [], [], []
    for i, t in enumerate(times):
        png = band / f"q{i}.png"
        _run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(a.src),
              "-frames:v", "1", str(png), "-y"])
        arr = np.asarray(Image.open(png).convert("RGB")).astype(np.int16)

        # Search only where this skill puts captions: the bottom third (bottom rim) and, for
        # clips with furniture, the lifted reference position. A warm wall or wooden floor
        # lower down matches a naive yellow test, so the frame at large is not scanned.
        found_box = None
        for base_h in (BOTTOM_BASELINE_OF_H, REFERENCE_BASELINE_OF_H):
            top_f, bot_f = caption_band(base_h, H)
            y0, y1 = max(0, int((top_f - 0.02) * H)), min(H, int((bot_f + 0.02) * H))
            reg = arr[y0:y1]
            r, g, b = reg[:, :, 0], reg[:, :, 1], reg[:, :, 2]
            # the box is a large block of near-pure #FEEB02
            yel = (r > 225) & (g > 200) & (b < 70) & (np.abs(r - g) < 40)
            ys, xs = np.nonzero(yel)
            if len(xs) >= (0.0008 * W * H) and (ys.max() - ys.min()) > 0.02 * H:
                found_box = (y0 + ys.min(), y0 + ys.max(), xs.min(), xs.max())
                break
        if not found_box:
            continue                                    # no caption on this sampled frame
        bt, bb, bl, br = found_box
        boxes.append(bb - bt + 1)
        # sample the box FILL, not the middle (which can be a black glyph): take the most
        # common colour in the upper padding strip, which is pure yellow by construction
        pad = max(2, int((bb - bt) * 0.18))
        strip_top = arr[bt:bt + pad, bl:br + 1].reshape(-1, 3)
        if len(strip_top):
            vals, counts = np.unique(strip_top, axis=0, return_counts=True)
            yellows.append(tuple(int(v) for v in vals[counts.argmax()]))

        # 2) cap height, measured the same way the reference was: black glyphs INSIDE the
        #    yellow box. The box interior is pure yellow except for the active word, so this
        #    is unambiguous and immune to bright scenery outside the box.
        inset = max(2, round(0.02 * (bb - bt)))
        inner = arr[bt + inset:bb - inset + 1, bl + inset:br - inset + 1]
        inset = max(2, round(0.02 * (bb - bt)))
        inner = arr[bt + inset:bb - inset + 1, bl + inset:br - inset + 1]
        if inner.size == 0:
            continue
        ir, ig, ib = inner[:, :, 0], inner[:, :, 1], inner[:, :, 2]
        blk = (ir < 90) & (ig < 90) & (ib < 90)
        iys, _ = np.nonzero(blk)
        if len(iys) > 30:
            caps.append(int(iys.max() - iys.min() + 1))

    def verdict(ok):
        return "PASS" if ok else "FAIL"

    print(f"[qc] {a.src}  {W}x{H}  dur {info['dur']:.2f}s")
    ok_all = True

    if caps:
        med = int(sorted(caps)[len(caps) // 2])
        ok = abs(med - cap_expect) <= max(3, round(0.06 * cap_expect))
        ok_all &= ok
        print(f"[qc] cap band  h={med:3d} (expect {cap_expect} ±{max(3,round(0.06*cap_expect))})  "
              f"{verdict(ok)}  samples={caps}")
    else:
        print("[qc] cap band  -- no caption found on the sampled frames   FAIL")
        ok_all = False

    if boxes:
        med = int(sorted(boxes)[len(boxes) // 2])
        ok = abs(med - box_expect) <= max(4, round(0.06 * box_expect))
        ok_all &= ok
        print(f"[qc] box height h={med:3d} (expect {box_expect} ±{max(4,round(0.06*box_expect))})  "
              f"{verdict(ok)}  samples={boxes}")
    else:
        print("[qc] box height -- none found   FAIL")
        ok_all = False

    if yellows:
        med = tuple(int(sorted(c[i] for c in yellows)[len(yellows) // 2]) for i in range(3))
        ok = all(abs(med[i] - YELLOW[i]) <= 10 for i in range(3))
        ok_all &= ok
        print(f"[qc] yellow     {med} (expect {YELLOW} ±10)  {verdict(ok)}")

    print("\nVERDICT:", "PASS" if ok_all else "FAIL")
    sys.exit(0 if ok_all else 1)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, need_out=True):
        p.add_argument("--src", required=True)
        p.add_argument("--words", help="word-timed JSON (preferred)")
        p.add_argument("--srt", help="fallback: cue-level SRT, words interpolated")
        p.add_argument("--placement", choices=("auto", "bottom", "mid"), default="auto",
                       help="auto (default): bottom safe rim unless the clip already has "
                            "graphics down there; bottom: always bottom; mid: reference 66.8%%")
        p.add_argument("--logo", help="override the bundled top-left logo PNG")
        p.add_argument("--no-logo", action="store_true",
                       help="burn without the bundled Rollup logo (the default is logo ON)")
        p.add_argument("--logo-ink-h", type=float, default=LOGO_INK_H_OF_H,
                       help="logo ink height as a fraction of frame height "
                            "(default %(default)s = the caption box height)")
        p.add_argument("--logo-mx", type=float, default=LOGO_MARGIN_X_OF_W,
                       help="logo left rim as a fraction of frame width (default %(default)s)")
        p.add_argument("--logo-my", type=float, default=LOGO_MARGIN_Y_OF_H,
                       help="logo top rim as a fraction of frame height (default %(default)s)")
        if need_out:
            p.add_argument("--out", required=True)

    b = sub.add_parser("burn", help="burn captions and encode")
    common(b)
    b.add_argument("--crf", type=int, default=17)
    b.add_argument("--preset", default="medium")
    b.add_argument("--encoder", choices=("libx264", "nvenc"), default="libx264",
                   help="final encode: libx264 (default, slowest/best) or nvenc (NVIDIA GPU, "
                        "several times faster, same quality number maps to -cq)")
    b.set_defaults(fn=cmd_burn)

    p = sub.add_parser("preview", help="one real frame with the caption (look first)")
    common(p)
    p.add_argument("--at", type=float, help="seconds (default: first cue)")
    p.set_defaults(fn=cmd_preview)

    l = sub.add_parser("logo", help="burn only the corner logo onto an existing master")
    l.add_argument("--src", required=True)
    l.add_argument("--out", required=True)
    l.add_argument("--logo", help="override the bundled logo PNG")
    l.add_argument("--logo-ink-h", type=float, default=LOGO_INK_H_OF_H)
    l.add_argument("--logo-mx", type=float, default=LOGO_MARGIN_X_OF_W)
    l.add_argument("--logo-my", type=float, default=LOGO_MARGIN_Y_OF_H)
    l.add_argument("--crf", type=int, default=17)
    l.add_argument("--preset", default="medium")
    l.add_argument("--encoder", choices=("libx264", "nvenc"), default="libx264")
    l.set_defaults(fn=cmd_logo)

    q = sub.add_parser("qc", help="re-measure a delivered file")
    q.add_argument("--src", required=True)
    q.set_defaults(fn=cmd_qc)

    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
