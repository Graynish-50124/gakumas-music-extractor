param(
    [string]$Version = ''
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ReleaseRoot = Join-Path $ProjectRoot 'release'
$StagingRoot = Join-Path $ReleaseRoot 'staging'

if (-not $Version) {
    $Changelog = Get-Content -Raw -LiteralPath (Join-Path $ProjectRoot 'CHANGELOG.md')
    $VersionMatch = [regex]::Match($Changelog, '(?m)^##\s+(?<version>\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?)\s+-')
    if (-not $VersionMatch.Success) {
        throw 'CHANGELOG.mdから最新バージョンを取得できません。'
    }
    $Version = $VersionMatch.Groups['version'].Value
}
$NormalizedVersion = [regex]::Match(
    $Version,
    '^v?(?<number>\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?)$'
)
if (-not $NormalizedVersion.Success) {
    throw "バージョン形式が不正です: $Version"
}
$Version = "v$($NormalizedVersion.Groups['number'].Value)"
$ArchivePrefix = "GakumasMusicExtractor-$Version"

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


$Archives = @(
    @{ Path = Join-Path $ReleaseRoot "$ArchivePrefix-one-folder.zip"; Source = $OnedirStage },
    @{ Path = Join-Path $ReleaseRoot "$ArchivePrefix-one-file.zip"; Source = $OnefileStage }
)
foreach ($Archive in $Archives) {
    if (Test-Path -LiteralPath $Archive.Path) {
        Remove-Item -LiteralPath $Archive.Path -Force
    }
    Compress-Archive -LiteralPath $Archive.Source -DestinationPath $Archive.Path -CompressionLevel Optimal
}
$LegacyArtifacts = @(
    Join-Path $ReleaseRoot 'GakumasMusicExtractor.exe'
    Join-Path $ReleaseRoot 'GakumasMusicExtractor-source.zip'
    Join-Path $ReleaseRoot 'SHA256SUMS.txt'
    Join-Path $ReleaseRoot 'GakumasMusicExtractor-one-folder.zip'
    Join-Path $ReleaseRoot 'GakumasMusicExtractor-one-file.zip'
)
foreach ($Path in $LegacyArtifacts) {
    Remove-VerifiedReleasePath $Path
}

Remove-VerifiedReleasePath $StagingRoot
$Archives.Path | ForEach-Object { Get-Item -LiteralPath $_ }
