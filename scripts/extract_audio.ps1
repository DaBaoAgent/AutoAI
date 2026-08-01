param(
    [Parameter(Mandatory = $true)][string]$Id
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$video = Join-Path $root "data\media\$Id.mp4"
$audio = Join-Path $root "data\media\$Id.m4a"

if (-not (Test-Path -LiteralPath $video)) {
    throw "找不到视频中间文件：$video"
}

ffmpeg -y -i $video -vn -c:a copy $audio
if ($LASTEXITCODE -ne 0) {
    throw "音频提取失败：$Id"
}

ffprobe -v error -show_entries format=duration,size -of json $audio

