$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

Write-Host "[1/4] Preparing isolated Python environment..."
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    py -m venv .venv
    & $VenvPython -m pip install --upgrade pip
    & $VenvPython -m pip install -r requirements.txt pytest
}

Write-Host "[2/4] Applying event-time source changes..."
& $VenvPython .\scripts\apply_event_time.py

Write-Host "[3/4] Checking diff hygiene..."
git diff --check

Write-Host "[4/4] Running event-time and neighboring regression tests..."
& $VenvPython -m pytest `
    .\tests\test_import_event_time.py `
    .\tests\test_import_extraction_json.py `
    .\tests\test_bucket_metadata_boundary.py `
    -q

Write-Host ""
Write-Host "Event-time checks completed successfully."
Write-Host "Review with: git diff -- src/import_memory.py src/bucket_manager.py"
