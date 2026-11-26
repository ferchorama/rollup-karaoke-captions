"""Idempotent, no-admin toolchain bootstrap for a Rollup skill.

Installs, inside this skill folder only:
  - uv (if missing) into .tools/uv
  - a uv-managed Python and a project-local .venv (from pyproject.toml / uv.lock)
  - portable Node (official zip/tarball) into .tools/node, when package.json exists
  - npm ci (or npm install on first lock) and Playwright Chromium into .tools/playwright
  - portable ffmpeg/ffprobe into .tools/ffmpeg

Never edits the system PATH, the registry, or shell rc files. Never uses sudo, winget,
brew, or apt. Re-running is safe. Writes .tools/env.json, env.ps1 and env.sh so later
commands resolve the local tools.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

NODE_VERSION = "22.23.5"  # Node.js 22 LTS (Jod), pinned
PYTHON_VERSION = "3.12"
UV_INSTALL_URL_PS1 = "https://astral.sh/uv/install.ps1"
UV_INSTALL_URL_SH = "https://astral.sh/uv/install.sh"

FFMPEG_URLS = {
    "Windows-AMD64": "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
    "Windows-x86_64": "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
    "Linux-x86_64": "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz",
    "Linux-AMD64": "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz",
    "Linux-aarch64": "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linuxarm64-gpl.tar.xz",
    "Linux-arm64": "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linuxarm64-gpl.tar.xz",
    # evermeet builds are Intel; arm64 uses eugeneware static binaries.
    "Darwin-x86_64": "evermeet",
    "Darwin-arm64": "eugeneware-arm64",
}

EVERMEET_FFMPEG = "https://evermeet.cx/ffmpeg/getrelease/ffmpeg/zip"
EVERMEET_FFPROBE = "https://evermeet.cx/ffmpeg/getrelease/ffprobe/zip"
# pinned-ish static builds used when evermeet is the wrong arch
EUGENE_FFMPEG = "https://github.com/eugeneware/ffmpeg-static/releases/download/b6.1.1/ffmpeg-darwin-arm64.gz"
EUGENE_FFPROBE = "https://github.com/eugeneware/ffprobe-static/releases/download/b6.0.0/ffprobe-darwin-arm64.gz"


def skill_root() -> Path:
    return Path(__file__).resolve().parent.parent


def tools_dir() -> Path:
    return skill_root() / ".tools"


def log(msg: str) -> None:
    print(f"[setup] {msg}", flush=True)


def warn_user(reason: str, what: str) -> None:
    print()
    print("=== ACTION NEEDED FROM THE USER ===")
    print(f"Why: {reason}")
    print(f"What to do: {what}")
    print("===================================")
    print()


def _plat_key() -> str:
    return f"{platform.system()}-{platform.machine()}"


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "rollup-skill-setup"})
    with urllib.request.urlopen(req, timeout=180) as resp, open(dest, "wb") as fh:
        shutil.copyfileobj(resp, fh)


def chmod_x(path: Path) -> None:
    if os.name == "nt":
        return
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def run(cmd: list[str], cwd: Path | None = None, env: dict | None = None, check: bool = True) -> subprocess.CompletedProcess:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    # Windows .cmd shims need a shell
    use_shell = os.name == "nt" and cmd and cmd[0].lower().endswith((".cmd", ".bat"))
    log("run: " + " ".join(cmd))
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=merged, check=check, shell=use_shell)


# ----- uv ------------------------------------------------------------------

def find_uv() -> Path | None:
    tools_uv = tools_dir() / "uv" / ("uv.exe" if os.name == "nt" else "uv")
    if tools_uv.is_file():
        return tools_uv
    found = shutil.which("uv")
    return Path(found) if found else None


def install_uv() -> Path:
    existing = find_uv()
    if existing:
        log(f"uv already present: {existing}")
        return existing
    dest = tools_dir() / "uv"
    dest.mkdir(parents=True, exist_ok=True)
    log("installing uv into .tools/uv (no PATH changes)")
    env = os.environ.copy()
    env["UV_INSTALL_DIR"] = str(dest)
    env["UV_NO_MODIFY_PATH"] = "1"
    if os.name == "nt":
        ps = shutil.which("powershell") or shutil.which("pwsh")
        if not ps:
            raise RuntimeError("PowerShell is required once, to install uv on Windows.")
        run(
            [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
             "Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression"],
            env=env,
        )
        exe = dest / "uv.exe"
    else:
        script = dest / "install.sh"
        download(UV_INSTALL_URL_SH, script)
        chmod_x(script)
        run(["sh", str(script)], env=env)
        exe = dest / "uv"
        if not exe.is_file():
            # some installers put it in dest/bin/uv
            alt = dest / "bin" / "uv"
            if alt.is_file():
                exe = alt
    if not exe.is_file():
        raise RuntimeError("uv install finished but the binary was not found in .tools/uv")
    return exe


# ----- Python venv via uv --------------------------------------------------

def uv_env(uv: Path) -> dict:
    tools = tools_dir()
    env = os.environ.copy()
    env["UV_CACHE_DIR"] = str(tools / "cache" / "uv")
    env["UV_PYTHON_INSTALL_DIR"] = str(tools / "python")
    env["UV_LINK_MODE"] = "copy"
    env["UV_PYTHON_PREFERENCE"] = "only-managed"
    return env


def ensure_venv(uv: Path) -> Path:
    root = skill_root()
    venv = root / ".venv"
    py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    env = uv_env(uv)
    pyproject = root / "pyproject.toml"
    lock = root / "uv.lock"
    if pyproject.is_file():
        sync = [str(uv), "sync", "--inexact", "--python", PYTHON_VERSION, "--directory", str(root)]
        if lock.is_file():
            sync.append("--frozen")
        run(sync, env=env)
    else:
        if not py.is_file():
            run([str(uv), "venv", str(venv), "--python", PYTHON_VERSION, "--python-preference", "only-managed"], env=env)
    if not py.is_file():
        # uv sync should have created .venv
        if not py.is_file():
            run([str(uv), "venv", str(venv), "--python", PYTHON_VERSION, "--python-preference", "only-managed"], env=env)
    if not py.is_file():
        raise RuntimeError(f"venv python missing at {py}")
    log(f"python: {py}")
    return py


def maybe_install_torch(uv: Path, py: Path) -> None:
    """Install torch/torchaudio when the skill lists them in pyproject optional align extra or needs them."""
    root = skill_root()
    marker = root / "pyproject.toml"
    if not marker.is_file():
        return
    text = marker.read_text(encoding="utf-8")
    if "torchaudio" not in text and "torch" not in text:
        return
    # already importable?
    probe = subprocess.run([str(py), "-c", "import torch, torchaudio, soundfile, num2words"], capture_output=True)
    if probe.returncode == 0:
        log("torch / torchaudio already importable")
        return
    ninfo = subprocess.run(["nvidia-smi"], capture_output=True)
    if ninfo.returncode == 0:
        index = "https://download.pytorch.org/whl/cu124"
        log("NVIDIA GPU detected; installing torch/torchaudio (CUDA 12.4 wheels)")
    else:
        index = "https://download.pytorch.org/whl/cpu"
        log("installing torch/torchaudio (CPU wheels)")
    env = uv_env(uv)
    cmd = [str(uv), "pip", "install", "--python", str(py),
           "torch", "torchaudio", "soundfile", "num2words",
           "--index-url", index, "--extra-index-url", "https://pypi.org/simple"]
    run(cmd, env=env)


# ----- Node ----------------------------------------------------------------

def node_arch() -> tuple[str, str]:
    system = platform.system()
    mach = platform.machine().lower()
    if system == "Windows":
        os_tag = "win"
        arch = "arm64" if mach in ("arm64", "aarch64") else "x64"
        ext = "zip"
    elif system == "Darwin":
        os_tag = "darwin"
        arch = "arm64" if mach in ("arm64", "aarch64") else "x64"
        ext = "tar.gz"
    else:
        os_tag = "linux"
        arch = "arm64" if mach in ("arm64", "aarch64") else "x64"
        ext = "tar.xz"
    return f"https://nodejs.org/dist/v{NODE_VERSION}/node-v{NODE_VERSION}-{os_tag}-{arch}.{ext}", ext


def find_node() -> Path | None:
    for folder in (tools_dir() / "node", tools_dir() / "node" / "bin"):
        exe = folder / ("node.exe" if os.name == "nt" else "node")
        if exe.is_file():
            return exe
    return None


def install_node() -> Path:
    existing = find_node()
    if existing:
        # verify version major
        try:
            out = subprocess.check_output([str(existing), "-v"], text=True).strip()
            log(f"Node already present: {existing} ({out})")
            if out.lstrip("v").startswith("22."):
                return existing
            log(f"portable Node is {out}, expected v{NODE_VERSION}; reinstalling")
        except subprocess.CalledProcessError:
            pass
    url, ext = node_arch()
    dest = tools_dir() / "node"
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    archive = tools_dir() / "cache" / f"node-v{NODE_VERSION}.{ext}"
    if not archive.is_file():
        download(url, archive)
    log(f"extracting Node {NODE_VERSION} into .tools/node")
    tmp = tools_dir() / "cache" / "node-extract"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    if ext == "zip":
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(tmp)
    else:
        with tarfile.open(archive) as tf:
            tf.extractall(tmp)
    # the archive contains a single top folder
    children = [p for p in tmp.iterdir() if p.name not in (".", "..")]
    top = children[0] if len(children) == 1 else tmp
    for item in top.iterdir():
        target = dest / item.name
        if target.exists():
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        shutil.move(str(item), str(target))
    shutil.rmtree(tmp, ignore_errors=True)
    exe = dest / ("node.exe" if os.name == "nt" else "node")
    if not exe.is_file():
        alt = dest / "bin" / "node"
        if alt.is_file():
            exe = alt
    if not exe.is_file():
        raise RuntimeError(f"Node extract finished but {exe} is missing")
    chmod_x(exe)
    log(f"Node: {exe} ({subprocess.check_output([str(exe), '-v'], text=True).strip()})")
    return exe


def node_bin_dir(node_exe: Path) -> Path:
    # Windows: .tools/node/node.exe, npm.cmd alongside
    # Unix tarball: .tools/node/bin/node
    return node_exe.parent


def ensure_npm_packages(node_exe: Path) -> None:
    root = skill_root()
    pkg = root / "package.json"
    if not pkg.is_file():
        log("no package.json; skipping npm")
        return
    bin_dir = node_bin_dir(node_exe)
    npm_cmd = bin_dir / ("npm.cmd" if os.name == "nt" else "npm")
    if not npm_cmd.is_file():
        npm_cmd = bin_dir / "npm"
    if not npm_cmd.is_file():
        raise RuntimeError(f"npm not found next to Node at {bin_dir}")
    env = os.environ.copy()
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    env["npm_config_audit"] = "false"
    env["npm_config_fund"] = "false"
    env["npm_config_update_notifier"] = "false"
    env["npm_config_cache"] = str(tools_dir() / "cache" / "npm")
    lock = root / "package-lock.json"
    if lock.is_file():
        run([str(npm_cmd), "ci", "--no-fund", "--no-audit"], cwd=root, env=env)
    else:
        run([str(npm_cmd), "install", "--no-fund", "--no-audit"], cwd=root, env=env)
        log("wrote package-lock.json (commit this file so future setups can npm ci)")


def ensure_playwright(node_exe: Path) -> Path | None:
    root = skill_root()
    pkg = root / "package.json"
    if not pkg.is_file():
        return None
    text = pkg.read_text(encoding="utf-8")
    if "playwright" not in text:
        return None
    browsers = tools_dir() / "playwright"
    browsers.mkdir(parents=True, exist_ok=True)
    bin_dir = node_bin_dir(node_exe)
    npx_cmd = bin_dir / ("npx.cmd" if os.name == "nt" else "npx")
    env = os.environ.copy()
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers)
    env["npm_config_cache"] = str(tools_dir() / "cache" / "npm")
    # Prefer the local package binary
    local = root / "node_modules" / ".bin" / ("playwright.cmd" if os.name == "nt" else "playwright")
    if local.is_file():
        cmd = [str(local), "install", "chromium"]
    else:
        cmd = [str(npx_cmd), "--no-install", "playwright", "install", "chromium"]
    run(cmd, cwd=root, env=env)
    chrome = _find_chrome(browsers)
    if chrome:
        log(f"Playwright Chromium: {chrome}")
    else:
        log("Playwright Chromium installed, but the chrome binary was not located yet")
    return chrome


def _find_chrome(base: Path) -> Path | None:
    if not base.exists():
        return None
    names = {"chrome", "chrome.exe", "chromium", "chromium.exe", "chrome-headless-shell", "chrome-headless-shell.exe"}
    for dirpath, _dirs, files in os.walk(base):
        for fn in files:
            if fn in names:
                return Path(dirpath) / fn
    return None


# ----- ffmpeg --------------------------------------------------------------

def find_ffmpeg_pair() -> tuple[Path | None, Path | None]:
    bin_dir = tools_dir() / "ffmpeg" / "bin"
    ff = bin_dir / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    fp = bin_dir / ("ffprobe.exe" if os.name == "nt" else "ffprobe")
    return (ff if ff.is_file() else None, fp if fp.is_file() else None)


def _extract_ffmpeg_archive(archive: Path, dest_bin: Path) -> None:
    dest_bin.mkdir(parents=True, exist_ok=True)
    tmp = tools_dir() / "cache" / "ffmpeg-extract"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    name = archive.name.lower()
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(tmp)
    elif name.endswith(".gz") and not name.endswith(".tar.gz"):
        import gzip
        out_name = "ffmpeg" if "ffmpeg" in name else "ffprobe"
        out = tmp / out_name
        with gzip.open(archive, "rb") as src, open(out, "wb") as dst:
            shutil.copyfileobj(src, dst)
        chmod_x(out)
    else:
        with tarfile.open(archive) as tf:
            tf.extractall(tmp)
    wanted = {"ffmpeg", "ffmpeg.exe", "ffprobe", "ffprobe.exe"}
    found = []
    for dirpath, _dirs, files in os.walk(tmp):
        for fn in files:
            if fn in wanted:
                found.append(Path(dirpath) / fn)
    if not found:
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError(f"no ffmpeg/ffprobe binary in {archive.name}")
    for src in found:
        target = dest_bin / src.name
        shutil.copy2(src, target)
        chmod_x(target)
    shutil.rmtree(tmp, ignore_errors=True)


def install_ffmpeg() -> tuple[Path, Path]:
    ff, fp = find_ffmpeg_pair()
    if ff and fp:
        log(f"ffmpeg already present: {ff}")
        return ff, fp
    dest_bin = tools_dir() / "ffmpeg" / "bin"
    dest_bin.mkdir(parents=True, exist_ok=True)
    key = _plat_key()
    url = FFMPEG_URLS.get(key)
    cache = tools_dir() / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    if url == "evermeet":
        for label, u in (("ffmpeg", EVERMEET_FFMPEG), ("ffprobe", EVERMEET_FFPROBE)):
            arch = cache / f"{label}-evermeet.zip"
            download(u, arch)
            _extract_ffmpeg_archive(arch, dest_bin)
    elif url == "eugeneware-arm64":
        for label, u in (("ffmpeg", EUGENE_FFMPEG), ("ffprobe", EUGENE_FFPROBE)):
            arch = cache / Path(u).name
            download(u, arch)
            _extract_ffmpeg_archive(arch, dest_bin)
    elif url:
        arch = cache / Path(url).name
        if not arch.is_file():
            download(url, arch)
        _extract_ffmpeg_archive(arch, dest_bin)
    else:
        raise RuntimeError(
            f"No portable ffmpeg build is configured for {_plat_key()}. "
            "Put ffmpeg and ffprobe in .tools/ffmpeg/bin and re-run setup."
        )
    ff, fp = find_ffmpeg_pair()
    if not ff or not fp:
        raise RuntimeError("ffmpeg install finished but ffmpeg/ffprobe are missing from .tools/ffmpeg/bin")
    log(f"ffmpeg: {ff}")
    log(f"ffprobe: {fp}")
    return ff, fp


# ----- env file + dotenv check --------------------------------------------

def write_env_files(node_exe: Path | None, ff: Path, fp: Path, py: Path, chrome: Path | None) -> None:
    tools = tools_dir()
    root = skill_root()
    env_vars = {
        "HYPERFRAMES_NO_TELEMETRY": "1",
        "HYPERFRAMES_SKIP_SKILLS": "1",
        "HYPERFRAMES_NO_UPDATE_CHECK": "1",
        "FFMPEG_BINARY": str(ff),
        "FFPROBE_BINARY": str(fp),
        "PLAYWRIGHT_BROWSERS_PATH": str(tools / "playwright"),
        "XDG_CACHE_HOME": str(tools / "cache"),
        "UV_CACHE_DIR": str(tools / "cache" / "uv"),
        "UV_PYTHON_INSTALL_DIR": str(tools / "python"),
    }
    if node_exe:
        env_vars["NODE_BINARY"] = str(node_exe)
        env_vars["npm_config_cache"] = str(tools / "cache" / "npm")
    if chrome:
        env_vars["HF_CHROME"] = str(chrome)
        env_vars["PUPPETEER_EXECUTABLE_PATH"] = str(chrome)
    path_dirs = [
        str(tools / "ffmpeg" / "bin"),
        str(node_bin_dir(node_exe)) if node_exe else "",
        str(tools / "uv"),
        str(py.parent),
        str(root / "node_modules" / ".bin"),
    ]
    path_dirs = [d for d in path_dirs if d]
    payload = {"env": env_vars, "path": path_dirs, "python": str(py), "ffmpeg": str(ff), "ffprobe": str(fp)}
    (tools / "env.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    ps = tools / "env.ps1"
    ps_lines = [
        "# Auto-generated by scripts/bootstrap.py. Dot-source:  . .\\ .tools\\env.ps1".replace("\\ ", ""),
        f"$Skill = Split-Path -Parent $PSScriptRoot",
    ]
    # fix the comment - I'll write a clean one
    ps_lines = [
        "# Auto-generated by scripts/bootstrap.py. Dot-source from the skill folder:",
        "#   . .\\.tools\\env.ps1",
        "$SkillRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path",
    ]
    for d in path_dirs:
        ps_lines.append(f'$env:PATH = "{d};" + $env:PATH')
    for k, v in env_vars.items():
        ps_lines.append(f'$env:{k} = "{v}"')
    ps.write_text("\n".join(ps_lines) + "\n", encoding="utf-8")

    sh = tools / "env.sh"
    sh_lines = [
        "# Auto-generated by scripts/bootstrap.py. Source from the skill folder:",
        "#   source .tools/env.sh",
        'SKILL_ROOT="$(cd "$(dirname "$0")/.." && pwd)"',
    ]
    for d in path_dirs:
        sh_lines.append(f'export PATH="{d}:$PATH"')
    for k, v in env_vars.items():
        sh_lines.append(f'export {k}="{v}"')
    sh.write_text("\n".join(sh_lines) + "\n", encoding="utf-8")
    chmod_x(sh)
    log(f"wrote {tools / 'env.json'}, env.ps1, env.sh")


def check_dotenv() -> list[str]:
    root = skill_root()
    example = root / ".env.example"
    if not example.is_file():
        return []
    needed = []
    for raw in example.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if key:
            needed.append(key)
    # load .env if present
    dotenv = root / ".env"
    have = set(os.environ.keys())
    if dotenv.is_file():
        for raw in dotenv.read_text(encoding="utf-8").splitlines():
            if raw.strip() and not raw.strip().startswith("#") and "=" in raw:
                have.add(raw.split("=", 1)[0].strip())
    missing = [k for k in needed if k not in have or not os.environ.get(k)]
    # If the key is in .env with a value, os.environ might not have it yet
    if dotenv.is_file():
        vals = {}
        for raw in dotenv.read_text(encoding="utf-8").splitlines():
            if raw.strip() and not raw.strip().startswith("#") and "=" in raw:
                k, _, v = raw.partition("=")
                vals[k.strip()] = v.strip().strip("'").strip('"')
        missing = [k for k in needed if not (vals.get(k) or os.environ.get(k))]
    return missing


def main() -> int:
    root = skill_root()
    os.chdir(root)
    log(f"skill folder: {root}")
    tools_dir().mkdir(parents=True, exist_ok=True)
    (tools_dir() / "cache").mkdir(parents=True, exist_ok=True)

    uv = install_uv()
    log(f"uv: {uv} ({subprocess.check_output([str(uv), '--version'], text=True).strip()})")
    py = ensure_venv(uv)
    maybe_install_torch(uv, py)
    ff, fp = install_ffmpeg()

    node_exe = None
    chrome = None
    if (root / "package.json").is_file():
        node_exe = install_node()
        ensure_npm_packages(node_exe)
        chrome = ensure_playwright(node_exe)
        # also search hyperframes' own cache after npm install
        if not chrome:
            chrome = _find_chrome(tools_dir() / "cache") or _find_chrome(Path.home() / ".cache" / "hyperframes")

    write_env_files(node_exe, ff, fp, py, chrome)

    missing = check_dotenv()
    print()
    log("setup complete. Local tools:")
    log(f"  python   {py}")
    log(f"  ffmpeg   {ff}")
    log(f"  ffprobe  {fp}")
    if node_exe:
        log(f"  node     {node_exe}")
    if chrome:
        log(f"  chrome   {chrome}")
    log("Activate them in a new shell with:")
    if os.name == "nt":
        log("  . .\\.tools\\env.ps1")
    else:
        log("  source .tools/env.sh")
    log("Or just import scripts/tooling.py (it reads .tools/env.json).")

    if missing:
        warn_user(
            "This skill can call an external API, and the key is not set. "
            "Setup itself does not need the key, but a later step that talks to that service will fail without it.",
            "Copy .env.example to .env and paste the value for: " + ", ".join(missing) + ". "
            "Do not commit .env. The key never leaves this machine unless you use that service.",
        )
        # not a hard failure — the rest of the skill works without it
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as e:
        log(f"command failed with exit {e.returncode}: {e.cmd}")
        sys.exit(e.returncode or 1)
    except Exception as e:
        log(f"ERROR: {e}")
        sys.exit(1)
