# The Rollup — karaoke caption burner

Burns The Rollup's short-form caption style onto any video: one centred line of
heavy uppercase type, with the currently spoken word on a yellow box, plus the
Rollup corner logo in the same pass.

Start with `SKILL.md`.

## Requirements

You do not need to install Python or ffmpeg yourself. From this folder, the agent
(or you) runs setup, which installs a portable toolchain inside the skill folder
with no admin rights and no system PATH changes:

```bash
# Windows
powershell -ExecutionPolicy Bypass -File .\setup.ps1

# macOS / Linux
chmod +x setup.sh && ./setup.sh
```

Setup is idempotent. It installs `uv`, a managed Python 3.12, Pillow and numpy in
`.venv/`, and a portable `ffmpeg` / `ffprobe` in `.tools/ffmpeg`.

Then load the local tools:

```bash
# Windows PowerShell
. .\.tools\env.ps1

# macOS / Linux
source .tools/env.sh
```

`scripts/tooling.py` resolves the same paths automatically.

Copy `.env.example` to `.env` only when a job will call ElevenLabs. Setup itself
does not need a key.

## Quick start

```bash
python scripts/test_smoke.py
python scripts/captionburn.py preview --src input.mp4 --words words.json --at 33.5 --out check.png
python scripts/captionburn.py burn --src input.mp4 --words words.json --out final.mp4
python scripts/captionburn.py qc --src final.mp4
```

Word-timed JSON is required for accurate karaoke. See `SKILL.md` for the schema
and the names-and-terms pass (`scripts/edit_words.py`).

## Contents

| Path | What |
|---|---|
| `SKILL.md` | procedure, style rules, verification |
| `setup.ps1`, `setup.sh` | no-admin toolchain install |
| `scripts/bootstrap.py` | cross-platform setup entry |
| `scripts/captionburn.py` | renderer, burn, preview, QC, logo pass |
| `scripts/edit_words.py` | transcript pass: merge / phrase / spell |
| `references/` | measured style spec and transcript-edit guide |
| `assets/fonts/`, `assets/logo/` | Barlow Bold (OFL) and the corner bug |
| `pyproject.toml`, `uv.lock` | pinned Python deps |
| `.env.example` | optional API keys |
