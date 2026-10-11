$ErrorActionPreference = "Stop"
Set-Location (Resolve-Path (Join-Path $PSScriptRoot ".."))
$pyVersion = "3.11"
& py -3.11 --version 2>$null
if ($LASTEXITCODE -ne 0) {
    $pyVersion = "3.12"
    & py -3.12 --version 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "Python 3.11 or 3.12 is required. Install it first."
    }
}
& py "-$pyVersion" -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -e '.[dev]'
& .\.venv\Scripts\python.exe -m pytest -q
Write-Host "Environment ready. Start with: .\.venv\Scripts\python.exe -m uvicorn agenttest.main:app --reload"
