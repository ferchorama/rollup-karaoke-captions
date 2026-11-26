"""Smoke test for rollup-karaoke-captions. No machine-specific paths."""
from __future__ import annotations

import compileall
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
SCRIPTS = SKILL / "scripts"
sys.path.insert(0, str(SCRIPTS))
import tooling

tooling.activate()
fails = 0


def ok(name: str, passed: bool, detail: str = "") -> None:
    global fails
    fails += 0 if passed else 1
    print(f"{'PASS' if passed else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))


ok("python compile", compileall.compile_dir(str(SCRIPTS), quiet=1, force=True), str(SCRIPTS))

try:
    import numpy  # noqa: F401
    from PIL import Image, ImageFont  # noqa: F401
    ok("import numpy + Pillow", True)
except Exception as e:
    ok("import numpy + Pillow", False, str(e))

try:
    ff = tooling.ffmpeg()
    fp = tooling.ffprobe()
    r = subprocess.run([ff, "-version"], capture_output=True, text=True)
    ok("ffmpeg", r.returncode == 0, ff)
    r = subprocess.run([fp, "-version"], capture_output=True, text=True)
    ok("ffprobe", r.returncode == 0, fp)
except FileNotFoundError as e:
    ok("ffmpeg/ffprobe", False, str(e))

font = SKILL / "assets" / "fonts" / "Barlow-Bold.ttf"
logo = SKILL / "assets" / "logo" / "the_rollup_logo_white_dropshadow.png"
ok("bundled font", font.is_file(), str(font))
ok("bundled logo", logo.is_file(), str(logo))

r = subprocess.run([sys.executable, str(SCRIPTS / "captionburn.py"), "-h"],
                   cwd=str(SKILL), capture_output=True, text=True)
ok("captionburn.py -h", r.returncode == 0, f"exit={r.returncode}")

print()
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
