$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Project virtual environment not found. Create it and install requirements first: py -3.11 -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt"
}
& $Python -m app.setup_database
if ($LASTEXITCODE -ne 0) { throw "Database setup failed with exit code $LASTEXITCODE" }
