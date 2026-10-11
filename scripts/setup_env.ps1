param([string]$PythonPath = "", [switch]$Agent)

$ErrorActionPreference = "Stop"
Set-Location (Resolve-Path (Join-Path $PSScriptRoot ".."))

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed with exit code $LASTEXITCODE" }
}

if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    if ($PythonPath) {
        $runtimePython = (Resolve-Path -LiteralPath $PythonPath).Path
    } elseif (Test-Path -LiteralPath '.python311\python.exe') {
        $runtimePython = (Resolve-Path -LiteralPath '.python311\python.exe').Path
    } elseif (Get-Command py -ErrorAction SilentlyContinue) {
        $runtimePython = & py -3.11 -c 'import sys; print(sys.executable)'
        if ($LASTEXITCODE -ne 0) { throw "Python 3.11 is required." }
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        $runtimePython = (Get-Command python).Source
    } else {
        throw "Install Python 3.11 or provide -PythonPath."
    }
    Invoke-Checked $runtimePython @('-c', 'import sys; assert sys.version_info[:2] == (3, 11)')
    Invoke-Checked $runtimePython @('-m', 'venv', '.venv')
}

$projectPython = (Resolve-Path -LiteralPath '.venv\Scripts\python.exe').Path
Invoke-Checked $projectPython @('-c', 'import sys; assert sys.version_info[:2] == (3, 11)')
New-Item -ItemType Directory -Force '.venv\tmp' | Out-Null
$originalTemp = $env:TEMP
$originalTmp = $env:TMP
try {
    $env:TEMP = (Resolve-Path -LiteralPath '.venv\tmp').Path
    $env:TMP = $env:TEMP
    $lockFile = if ($Agent) { 'requirements-agent.lock' } else { 'requirements-dev.lock' }
    $extras = if ($Agent) { '.[dev,agent]' } else { '.[dev]' }
    Invoke-Checked $projectPython @('-m', 'pip', 'install', '-r', $lockFile)
    Invoke-Checked $projectPython @('-m', 'pip', 'install', '--no-build-isolation', '-e', $extras)
    Invoke-Checked $projectPython @('-m', 'pytest', '-q')
    Invoke-Checked $projectPython @('-m', 'ruff', 'check', '.')
} finally {
    $env:TEMP = $originalTemp
    $env:TMP = $originalTmp
}
Write-Host 'Environment ready. Demo: .\.venv\Scripts\python.exe -m agenttest demo'
