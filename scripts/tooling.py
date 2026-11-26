"""Resolve skill-local tools (ffmpeg, ffprobe, node, npx, hyperframes, venv Python).

Scripts should call tooling.activate() early so subprocess calls can use short names
like 'ffmpeg' and 'node'. Nothing here assumes a global install.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

_ACTIVATED = False


def skill_root() -> Path:
    return Path(__file__).resolve().parent.parent


def tools_dir() -> Path:
    return skill_root() / ".tools"


def _env_file() -> Path:
    return tools_dir() / "env.json"


def _load_dotenv() -> None:
    for candidate in (skill_root() / ".env", skill_root() / ".env.local"):
        if not candidate.is_file():
            continue
        for raw in candidate.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip("'").strip('"')
            if key and key not in os.environ:
                os.environ[key] = val


def _apply_env_json() -> dict:
    path = _env_file()
    data = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
    for key, val in (data.get("env") or {}).items():
        if val and key not in os.environ:
            os.environ[key] = str(val)
    return data


def _bin_candidates() -> list[Path]:
    root = skill_root()
    tools = tools_dir()
    home = Path(os.environ.get("ROLLUP_ENV_HOME") or "")
    extras: list[Path] = []
    if home:
        extras.extend([
            home / "venv" / ("Scripts" if os.name == "nt" else "bin"),
            home / "tools" / "ffmpeg" / "bin",
            home / "uv",
        ])
    return [
        tools / "ffmpeg" / "bin",
        tools / "node",
        tools / "node" / "bin",
        tools / "uv",
        root / ".venv" / ("Scripts" if os.name == "nt" else "bin"),
        root / ".rollup-intro-env" / "venv" / ("Scripts" if os.name == "nt" else "bin"),
        root / ".rollup-intro-env" / "tools" / "ffmpeg" / "bin",
        root / "node_modules" / ".bin",
        *extras,
    ]


def activate() -> None:
    """Prepend skill-local tool bins to PATH and load .env / .tools/env.json."""
    global _ACTIVATED
    _load_dotenv()
    _apply_env_json()
    bins = [str(p) for p in _bin_candidates() if p.is_dir()]
    if bins:
        os.environ["PATH"] = os.pathsep.join(bins + [os.environ.get("PATH", "")])
    os.environ.setdefault("HYPERFRAMES_NO_TELEMETRY", "1")
    os.environ.setdefault("HYPERFRAMES_SKIP_SKILLS", "1")
    os.environ.setdefault("HYPERFRAMES_NO_UPDATE_CHECK", "1")
    pw = tools_dir() / "playwright"
    if pw.is_dir():
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(pw))
    cache = tools_dir() / "cache"
    if cache.is_dir():
        os.environ.setdefault("XDG_CACHE_HOME", str(cache))
    _ACTIVATED = True


def _which(name: str, env_keys: tuple[str, ...] = ()) -> str | None:
    if not _ACTIVATED:
        activate()
    for key in env_keys:
        val = os.environ.get(key)
        if val and Path(val).is_file():
            return val
    found = shutil.which(name)
    if found:
        return found
    exe = name if name.lower().endswith(".exe") else (name + (".exe" if os.name == "nt" else ""))
    for folder in _bin_candidates():
        cand = folder / exe
        if cand.is_file():
            return str(cand)
        # Unix ffmpeg sometimes has no extension already handled
        cand2 = folder / name
        if cand2.is_file():
            return str(cand2)
    return None


def ffmpeg() -> str:
    path = _which("ffmpeg", ("FFMPEG_BINARY", "INTROKIT_FFMPEG", "FFMPEG"))
    if not path:
        raise FileNotFoundError(
            "ffmpeg not found. Run setup.ps1 or ./setup.sh from the skill folder "
            "(it installs a portable copy into .tools/ffmpeg)."
        )
    return path


def ffprobe() -> str:
    path = _which("ffprobe", ("FFPROBE_BINARY", "INTROKIT_FFPROBE", "FFPROBE"))
    if not path:
        # same directory as ffmpeg, if we have that
        try:
            sib = Path(ffmpeg()).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
            if sib.is_file():
                return str(sib)
        except FileNotFoundError:
            pass
        raise FileNotFoundError(
            "ffprobe not found. Run setup.ps1 or ./setup.sh from the skill folder."
        )
    return path


def node() -> str:
    path = _which("node", ("NODE_BINARY", "NODE"))
    if not path:
        raise FileNotFoundError(
            "Node.js not found. Run setup.ps1 or ./setup.sh from the skill folder "
            "(it installs a portable Node into .tools/node)."
        )
    return path


def npm() -> str:
    name = "npm.cmd" if os.name == "nt" else "npm"
    path = _which(name, ("NPM_BINARY",)) or _which("npm")
    if not path:
        raise FileNotFoundError("npm not found. Run setup from the skill folder.")
    return path


def npx() -> str:
    name = "npx.cmd" if os.name == "nt" else "npx"
    path = _which(name, ("NPX_BINARY",)) or _which("npx")
    if not path:
        raise FileNotFoundError("npx not found. Run setup from the skill folder.")
    return path


def venv_python() -> Path:
    if not _ACTIVATED:
        activate()
    root = skill_root()
    cands = [
        root / ".venv" / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python"),
        root / ".rollup-intro-env" / "venv" / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python"),
    ]
    for c in cands:
        if c.is_file():
            return c
    return Path(sys.executable)


def hyperframes_cmd() -> list[str]:
    """Local HyperFrames CLI (pinned in package.json). Never calls a global npx package."""
    if not _ACTIVATED:
        activate()
    root = skill_root()
    if os.name == "nt":
        shim = root / "node_modules" / ".bin" / "hyperframes.cmd"
    else:
        shim = root / "node_modules" / ".bin" / "hyperframes"
    if shim.is_file():
        return [str(shim)]
    # package entry
    pkg = root / "node_modules" / "hyperframes" / "package.json"
    if pkg.is_file():
        data = json.loads(pkg.read_text(encoding="utf-8"))
        bin_field = data.get("bin")
        rel = None
        if isinstance(bin_field, str):
            rel = bin_field
        elif isinstance(bin_field, dict):
            rel = bin_field.get("hyperframes") or next(iter(bin_field.values()), None)
        if rel:
            entry = (root / "node_modules" / "hyperframes" / rel).resolve()
            if entry.is_file():
                return [node(), str(entry)]
    raise FileNotFoundError(
        "hyperframes is not installed in this skill's node_modules. "
        "Run setup.ps1 or ./setup.sh from the skill folder."
    )


def run_ffmpeg(args: list[str], **kwargs):
    import subprocess
    return subprocess.run([ffmpeg(), *args], **kwargs)


def run_ffprobe(args: list[str], **kwargs):
    import subprocess
    return subprocess.run([ffprobe(), *args], **kwargs)
