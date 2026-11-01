param([int]$Port = 18012, [switch]$Demo, [string]$ModelEnv, [string]$Projects, [string]$Data)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$projectPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) { throw '请先按照 README 准备项目 .venv。' }
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'web\dist\index.html'))) {
    throw '请先进入 web 运行 npm run build，再启动网页。'
}
$workspaceArguments = @('-X', 'utf8', (Join-Path $PSScriptRoot 'serve_web_workspace.py'), '--port', "$Port")
if ($Demo) { $workspaceArguments += '--demo' }
if ($ModelEnv) { $workspaceArguments += @('--model-env', $ModelEnv) }
if ($Projects) { $workspaceArguments += @('--projects', $Projects) }
if ($Data) { $workspaceArguments += @('--data', $Data) }
& $projectPython @workspaceArguments
exit $LASTEXITCODE
