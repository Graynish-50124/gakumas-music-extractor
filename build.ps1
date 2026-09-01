param(
    [switch]$SetupOnly,
    [switch]$SkipOneFile
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$WorkRoot = [IO.Path]::GetFullPath((Join-Path $ProjectRoot 'work'))
$CleanBuildPath = @(
    (Join-Path $ProjectRoot '.venv\Scripts'),
    (Join-Path $env:SystemRoot 'System32'),
    $env:SystemRoot,
    (Join-Path $env:SystemRoot 'System32\Wbem'),
    (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0')
) -join [IO.Path]::PathSeparator

function Remove-VerifiedWorkTree([string]$Path) {
    $ResolvedPath = [IO.Path]::GetFullPath($Path)
    $AllowedPrefix = $WorkRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $ResolvedPath.StartsWith($AllowedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "作業用削除先が不正です: $ResolvedPath"
    }
    if (Test-Path -LiteralPath $ResolvedPath) {
        Remove-Item -LiteralPath $ResolvedPath -Recurse -Force
    }
}

function Invoke-CleanPyInstaller([string]$DistPath, [string]$BuildPath, [string]$SpecPath) {
    $PreviousPath = $env:PATH
    try {
        # Native dependency discovery must not see unrelated DLLs supplied by
        # developer tools on PATH.  Otherwise a foreign ICU/OpenSSL runtime can
        # be copied into the app and shadow the Windows/packaged dependency.
        $env:PATH = $CleanBuildPath
        & $VenvPython -m PyInstaller --noconfirm --clean --distpath $DistPath --workpath $BuildPath $SpecPath
        if ($LASTEXITCODE -ne 0) {
            throw "PyInstallerビルドに失敗しました: $SpecPath"
        }
    }
    finally {
        $env:PATH = $PreviousPath
    }
}

if (-not (Test-Path -LiteralPath $VenvPython)) {
    py -3.12 -m venv (Join-Path $ProjectRoot '.venv')
}

& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r (Join-Path $ProjectRoot 'requirements.txt')

$Ffmpeg = Join-Path $ProjectRoot 'vendor\ffmpeg\bin\ffmpeg.exe'
$Vgmstream = Join-Path $ProjectRoot 'vendor\GkmasObjectManager\bin\vgmstream\vgmstream-win.exe'
if (-not (Test-Path -LiteralPath $Ffmpeg)) {
    throw 'vendor\ffmpeg\bin\ffmpeg.exe がありません。tools\fetch_ffmpeg.ps1を実行してください。'
}
if (-not (Test-Path -LiteralPath $Vgmstream)) {
    throw '同梱vgmstreamが見つかりません。'
}

if ($SetupOnly) {
    exit 0
}

Push-Location $ProjectRoot
try {
    & $VenvPython -m pytest -q
    Invoke-CleanPyInstaller 'dist\one-folder' 'build\one-folder' 'GakumasMusicExtractor-onedir.spec'
    $OnedirExe = Join-Path $ProjectRoot 'dist\one-folder\GakumasMusicExtractor\GakumasMusicExtractor.exe'
    $OnedirReport = Join-Path $ProjectRoot 'dist\one-folder\self-test.json'
    $OnedirSelfTest = Start-Process -FilePath $OnedirExe -ArgumentList @('--self-test', '--report', "`"$OnedirReport`"") -Wait -PassThru -WindowStyle Hidden
    if ($OnedirSelfTest.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $OnedirReport)) { throw 'one-folder自己診断に失敗しました。' }
    $OnedirSelfTestJson = Get-Content -Raw -LiteralPath $OnedirReport | ConvertFrom-Json
    if (-not $OnedirSelfTestJson.ok) { throw 'one-folder同梱コンポーネント診断に失敗しました。' }

    $AcceptanceRoot = Join-Path $ProjectRoot 'work\acceptance-one-folder'
    $AcceptanceReport = Join-Path $ProjectRoot 'work\acceptance-one-folder.json'
    Remove-VerifiedWorkTree $AcceptanceRoot
    Remove-Item -LiteralPath $AcceptanceReport -Force -ErrorAction SilentlyContinue
    $OnedirAcceptance = Start-Process -FilePath $OnedirExe -ArgumentList @('--acceptance-test', '--output', "`"$AcceptanceRoot`"", '--report', "`"$AcceptanceReport`"") -Wait -PassThru -WindowStyle Hidden
    if ($OnedirAcceptance.ExitCode -ne 0) { throw 'one-folder実データ受け入れテストに失敗しました。' }
    Remove-VerifiedWorkTree $AcceptanceRoot

    if (-not $SkipOneFile) {
        Invoke-CleanPyInstaller 'dist\one-file' 'build\one-file' 'GakumasMusicExtractor-onefile.spec'
        $OnefileExe = Join-Path $ProjectRoot 'dist\one-file\GakumasMusicExtractor.exe'
        $OnefileReport = Join-Path $ProjectRoot 'dist\one-file\self-test.json'
        $OnefileSelfTest = Start-Process -FilePath $OnefileExe -ArgumentList @('--self-test', '--report', "`"$OnefileReport`"") -Wait -PassThru -WindowStyle Hidden
        if ($OnefileSelfTest.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $OnefileReport)) { throw 'one-file自己診断に失敗しました。' }
        $OnefileSelfTestJson = Get-Content -Raw -LiteralPath $OnefileReport | ConvertFrom-Json
        if (-not $OnefileSelfTestJson.ok) { throw 'one-file同梱コンポーネント診断に失敗しました。' }

        $OnefileAcceptanceRoot = Join-Path $ProjectRoot 'work\acceptance-one-file'
        $OnefileAcceptanceReport = Join-Path $ProjectRoot 'work\acceptance-one-file.json'
        Remove-VerifiedWorkTree $OnefileAcceptanceRoot
        Remove-Item -LiteralPath $OnefileAcceptanceReport -Force -ErrorAction SilentlyContinue
        $OnefileAcceptance = Start-Process -FilePath $OnefileExe -ArgumentList @('--acceptance-test', '--output', "`"$OnefileAcceptanceRoot`"", '--report', "`"$OnefileAcceptanceReport`"") -Wait -PassThru -WindowStyle Hidden
        if ($OnefileAcceptance.ExitCode -ne 0) { throw 'one-file実データ受け入れテストに失敗しました。' }
        Remove-VerifiedWorkTree $OnefileAcceptanceRoot
    }
}
finally {
    Pop-Location
}
