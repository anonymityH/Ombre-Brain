$ErrorActionPreference = "Stop"

function Assert-LastExitCode([string]$Step) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE"
    }
}

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

Write-Host "[1/4] Preparing isolated Python environment..."
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    py -m venv .venv
    Assert-LastExitCode "venv creation"

    & $VenvPython -m pip install --upgrade pip
    Assert-LastExitCode "pip upgrade"

    & $VenvPython -m pip install -r requirements.txt
    Assert-LastExitCode "project dependency install"
}

# Test-only dependencies are kept out of the production requirements file.
& $VenvPython -m pip install pytest pytest-asyncio
Assert-LastExitCode "pytest dependency install"

Write-Host "[2/4] Applying event-time source changes..."
& $VenvPython .\scripts\apply_event_time.py
Assert-LastExitCode "event-time source application"

Write-Host "[3/4] Checking diff hygiene..."
& git diff --check -- src/import_memory.py src/bucket_manager.py
Assert-LastExitCode "git diff --check"

Write-Host "[4/4] Running event-time and neighboring regression tests..."
& $VenvPython -m pytest `
    .\tests\test_import_event_time.py `
    .\tests\test_import_extraction_json.py `
    .\tests\test_bucket_metadata_boundary.py `
    -q
Assert-LastExitCode "pytest"

Write-Host ""
Write-Host "Event-time checks completed successfully."
Write-Host "Review with: git diff --stat"
Write-Host "Review with: git diff -- src/import_memory.py src/bucket_manager.py"
