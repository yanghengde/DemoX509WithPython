$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw '缺少 .venv，请先按 README.md 安装依赖。'
}
& $pythonPath (Join-Path $PSScriptRoot 'main.py')
exit $LASTEXITCODE
