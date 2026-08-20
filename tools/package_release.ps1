$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ReleaseRoot = Join-Path $ProjectRoot 'release'
$StagingRoot = Join-Path $ReleaseRoot 'staging'

function Remove-VerifiedReleasePath([string]$Path) {
    $ResolvedPath = [IO.Path]::GetFullPath($Path)
    $ResolvedRelease = [IO.Path]::GetFullPath($ReleaseRoot)
    $AllowedPrefix = $ResolvedRelease.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $ResolvedPath.StartsWith($AllowedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "配布作業用削除先が不正です: $ResolvedPath"
    }
    if (Test-Path -LiteralPath $ResolvedPath) {
        Remove-Item -LiteralPath $ResolvedPath -Recurse -Force
    }
}

$OnedirBuilt = Join-Path $ProjectRoot 'dist\one-folder\GakumasMusicExtractor'
$OnefileBuilt = Join-Path $ProjectRoot 'dist\one-file\GakumasMusicExtractor.exe'
if (-not (Test-Path -LiteralPath (Join-Path $OnedirBuilt 'GakumasMusicExtractor.exe'))) {
    throw 'one-folderビルドがありません。先にbuild.ps1を実行してください。'
}
if (-not (Test-Path -LiteralPath $OnefileBuilt)) {
    throw 'one-fileビルドがありません。先にbuild.ps1を実行してください。'
}

New-Item -ItemType Directory -Force -Path $ReleaseRoot | Out-Null
Remove-VerifiedReleasePath $StagingRoot
New-Item -ItemType Directory -Force -Path $StagingRoot | Out-Null

$Docs = @(
    'README.md',
    'CHANGELOG.md',
    'VALIDATION.md',
    'LICENSE',
    'LICENSE.md',
    'THIRD_PARTY_NOTICES.md'
)

$OnedirStage = Join-Path $StagingRoot 'one-folder\GakumasMusicExtractor'
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $OnedirStage) | Out-Null
Copy-Item -LiteralPath $OnedirBuilt -Destination $OnedirStage -Recurse
foreach ($Name in $Docs) {
    Copy-Item -LiteralPath (Join-Path $ProjectRoot $Name) -Destination (Join-Path $OnedirStage $Name)
}

$OnefileStage = Join-Path $StagingRoot 'one-file\GakumasMusicExtractor-one-file'
New-Item -ItemType Directory -Force -Path $OnefileStage | Out-Null
Copy-Item -LiteralPath $OnefileBuilt -Destination (Join-Path $OnefileStage 'GakumasMusicExtractor.exe')
foreach ($Name in $Docs) {
    Copy-Item -LiteralPath (Join-Path $ProjectRoot $Name) -Destination (Join-Path $OnefileStage $Name)
}

$SourceStage = Join-Path $StagingRoot 'source\GakumasMusicExtractor-source'
New-Item -ItemType Directory -Force -Path $SourceStage | Out-Null
foreach ($Name in @('src', 'config', 'tests', 'tools')) {
    Copy-Item -LiteralPath (Join-Path $ProjectRoot $Name) -Destination (Join-Path $SourceStage $Name) -Recurse
}
New-Item -ItemType Directory -Force -Path (Join-Path $SourceStage 'vendor') | Out-Null
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'vendor\GkmasObjectManager') -Destination (Join-Path $SourceStage 'vendor\GkmasObjectManager') -Recurse
foreach ($Name in @('vgmstream-linux', 'vgmstream-mac')) {
    $NonWindowsBinary = Join-Path $SourceStage "vendor\GkmasObjectManager\bin\vgmstream\$Name"
    if (Test-Path -LiteralPath $NonWindowsBinary) {
        Remove-VerifiedReleasePath $NonWindowsBinary
    }
}
New-Item -ItemType Directory -Force -Path (Join-Path $SourceStage 'vendor\ffmpeg') | Out-Null
foreach ($Name in @('LICENSE', 'README.txt')) {
    $Source = Join-Path $ProjectRoot "vendor\ffmpeg\$Name"
    if (Test-Path -LiteralPath $Source) {
        Copy-Item -LiteralPath $Source -Destination (Join-Path $SourceStage "vendor\ffmpeg\$Name")
    }
}
foreach ($Name in @(
    'build.ps1',
    'run.ps1',
    'requirements.txt',
    'GakumasMusicExtractor-onedir.spec',
    'GakumasMusicExtractor-onefile.spec'
) + $Docs) {
    Copy-Item -LiteralPath (Join-Path $ProjectRoot $Name) -Destination (Join-Path $SourceStage $Name)
}
Get-ChildItem -LiteralPath $SourceStage -Recurse -Directory -Filter '__pycache__' | ForEach-Object {
    Remove-VerifiedReleasePath $_.FullName
}
Get-ChildItem -LiteralPath $SourceStage -Recurse -File -Filter '*.pyc' | Remove-Item -Force

$Archives = @(
    @{ Path = Join-Path $ReleaseRoot 'GakumasMusicExtractor-one-folder.zip'; Source = $OnedirStage },
    @{ Path = Join-Path $ReleaseRoot 'GakumasMusicExtractor-one-file.zip'; Source = $OnefileStage },
    @{ Path = Join-Path $ReleaseRoot 'GakumasMusicExtractor-source.zip'; Source = $SourceStage }
)
foreach ($Archive in $Archives) {
    if (Test-Path -LiteralPath $Archive.Path) {
        Remove-Item -LiteralPath $Archive.Path -Force
    }
    Compress-Archive -LiteralPath $Archive.Source -DestinationPath $Archive.Path -CompressionLevel Optimal
}
$LegacyDirectExe = Join-Path $ReleaseRoot 'GakumasMusicExtractor.exe'
Remove-VerifiedReleasePath $LegacyDirectExe

$HashTargets = @($Archives.Path)
$HashLines = $HashTargets | ForEach-Object {
    $Item = Get-Item -LiteralPath $_
    $Hash = (Get-FileHash -LiteralPath $Item.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    "$Hash  $($Item.Name)"
}
$HashFile = Join-Path $ReleaseRoot 'SHA256SUMS.txt'
Set-Content -LiteralPath $HashFile -Value $HashLines -Encoding ascii

Remove-VerifiedReleasePath $StagingRoot
@($HashTargets) + $HashFile | ForEach-Object { Get-Item -LiteralPath $_ }
