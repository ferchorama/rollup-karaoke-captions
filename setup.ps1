# Windows entry point. Installs uv (user-space) if needed, then runs scripts/bootstrap.py.
# No admin rights, no PATH edits, no system packages.
param()
$ErrorActionPreference = "Stop"
$Skill = $PSScriptRoot
$Tools = Join-Path $Skill ".tools"
$UvDir = Join-Path $Tools "uv"
$uv = Join-Path $UvDir "uv.exe"
if (-not (Test-Path $uv)) {
  $sys = Get-Command uv -ErrorAction SilentlyContinue
  if ($sys) { $uv = $sys.Source }
  else {
    Write-Host "[setup] installing uv into .tools/uv"
    New-Item -ItemType Directory -Force -Path $UvDir | Out-Null
    $env:UV_INSTALL_DIR = $UvDir
    $env:UV_NO_MODIFY_PATH = "1"
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    $uv = Join-Path $UvDir "uv.exe"
    if (-not (Test-Path $uv)) { throw "uv install finished but $uv is missing" }
  }
}
$env:UV_CACHE_DIR = Join-Path $Tools "cache\uv"
$env:UV_PYTHON_INSTALL_DIR = Join-Path $Tools "python"
$env:UV_LINK_MODE = "copy"
New-Item -ItemType Directory -Force -Path $env:UV_CACHE_DIR, $env:UV_PYTHON_INSTALL_DIR | Out-Null
& $uv run --python 3.12 --python-preference only-managed --no-project -- "$Skill\scripts\bootstrap.py" @args
exit $LASTEXITCODE
