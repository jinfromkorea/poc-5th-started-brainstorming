#Requires -Version 5.1
# Windows wrapper for check_prereqs.py -- finds a python interpreter first,
# since the Python check itself can't run without one.
$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command python3 -ErrorAction SilentlyContinue }

if (-not $python) {
    Write-Host "[FAIL] Python not found on PATH -- install Python 3.11+ before running this tool." -ForegroundColor Red
    Write-Host "       https://www.python.org/downloads/"
    exit 1
}

& $python.Source (Join-Path $scriptDir "check_prereqs.py")
exit $LASTEXITCODE
