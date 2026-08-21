# Windows one-click local quality verification (ISSUE-002).
#
# Usage (from the repository root):
#   powershell -ExecutionPolicy Bypass -File scripts\verify.ps1
#
# Runs, in order:
#   1. non-hardware pytest
#   2. ruff
#   3. mypy
#   4. package import check
#
# The first failing gate stops the run and propagates its exit code.
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Error "Missing virtual environment at $VenvPython"
    Write-Error "Create it with: py -3.12 -m venv .venv"
    Write-Error "Then install:    .\.venv\Scripts\python.exe -m pip install -e .[dev]"
    exit 1
}

$RepoRoot = Resolve-Path (Join-Path $Root "tools\quality\verify.py")
& $VenvPython $RepoRoot
exit $LASTEXITCODE
