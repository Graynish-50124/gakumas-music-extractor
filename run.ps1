$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) {
    throw '.venv がありません。先に build.ps1 -SetupOnly を実行してください。'
}
& $Python (Join-Path $ProjectRoot 'src\main.py')

