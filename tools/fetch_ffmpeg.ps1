$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$WorkRoot = Join-Path $ProjectRoot 'work\ffmpeg-download'
$Archive = Join-Path $WorkRoot 'ffmpeg-release-essentials.zip'
$ChecksumFile = Join-Path $WorkRoot 'ffmpeg-release-essentials.zip.sha256'
$Extracted = Join-Path $WorkRoot 'extracted'
$Destination = Join-Path $ProjectRoot 'vendor\ffmpeg'

New-Item -ItemType Directory -Force -Path $WorkRoot | Out-Null
Invoke-WebRequest -UseBasicParsing -Uri 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile $Archive
Invoke-WebRequest -UseBasicParsing -Uri 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip.sha256' -OutFile $ChecksumFile

$Expected = ((Get-Content -Raw -LiteralPath $ChecksumFile).Trim() -split '\s+')[0].ToLowerInvariant()
$Actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Archive).Hash.ToLowerInvariant()
if ($Expected -ne $Actual) {
    throw "FFmpeg ZIPのSHA256が一致しません。expected=$Expected actual=$Actual"
}

if (Test-Path -LiteralPath $Extracted) {
    $ResolvedExtracted = [IO.Path]::GetFullPath($Extracted)
    $ResolvedWorkRoot = [IO.Path]::GetFullPath($WorkRoot)
    if (-not $ResolvedExtracted.StartsWith($ResolvedWorkRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'FFmpeg一時展開先が作業フォルダ外です。'
    }
    Remove-Item -LiteralPath $ResolvedExtracted -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $Extracted | Out-Null
Expand-Archive -LiteralPath $Archive -DestinationPath $Extracted -Force
$BuildRoot = Get-ChildItem -LiteralPath $Extracted -Directory | Select-Object -First 1
if (-not $BuildRoot) { throw 'FFmpeg ZIPの内容を認識できません。' }

New-Item -ItemType Directory -Force -Path (Join-Path $Destination 'bin') | Out-Null
foreach ($Name in @('ffmpeg.exe', 'ffprobe.exe')) {
    Copy-Item -LiteralPath (Join-Path $BuildRoot.FullName "bin\$Name") -Destination (Join-Path $Destination "bin\$Name") -Force
}
foreach ($Name in @('LICENSE', 'README.txt')) {
    $Source = Join-Path $BuildRoot.FullName $Name
    if (Test-Path -LiteralPath $Source) {
        Copy-Item -LiteralPath $Source -Destination (Join-Path $Destination $Name) -Force
    }
}

[PSCustomObject]@{
    Source = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'
    SHA256 = $Actual
    FFmpeg = (Join-Path $Destination 'bin\ffmpeg.exe')
}
