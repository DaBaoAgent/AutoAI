param(
    [Parameter(Mandatory = $true)][string]$Id,
    [Parameter(Mandatory = $true)][string]$UrlFile,
    [switch]$AudioOnly
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$output = if ($AudioOnly) {
    Join-Path $root "data\media\$Id.m4a"
} else {
    Join-Path $root "data\media\$Id.mp4"
}
$url = Get-Content -LiteralPath $UrlFile -Raw

curl.exe -L --fail --connect-timeout 15 --max-time 180 `
    -A "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36" `
    -e "https://www.douyin.com/" `
    -H "Range: bytes=0-" `
    -o $output `
    $url

if ($LASTEXITCODE -ne 0) {
    throw "Media download failed: $Id"
}

if (-not $AudioOnly) {
    & (Join-Path $PSScriptRoot "extract_audio.ps1") -Id $Id
    $output = Join-Path $root "data\media\$Id.m4a"
}

ffprobe -v error -show_entries format=duration,size -of default=noprint_wrappers=1 $output
