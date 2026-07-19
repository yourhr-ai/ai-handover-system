# PyInstaller onefile/windowed exe build script for 인수인계10분.
# Usage (from repo root): powershell -File scripts/build_exe.ps1

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

Write-Host "[1/3] 이전 빌드 산출물 정리 (dist/, build/)" -ForegroundColor Cyan
Remove-Item -Recurse -Force "dist" -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force "build" -ErrorAction SilentlyContinue

Write-Host "[2/3] PyInstaller 빌드 실행" -ForegroundColor Cyan
py -B -m PyInstaller handover_analyzer.spec --noconfirm
if ($LASTEXITCODE -ne 0) {
    Write-Host "빌드 실패 (exit code $LASTEXITCODE)" -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host "[3/3] 빌드 완료" -ForegroundColor Cyan
$ExePath = Get-ChildItem -Path "dist" -Filter "*.exe" -Recurse | Select-Object -First 1
if ($ExePath) {
    $SizeMb = [math]::Round($ExePath.Length / 1MB, 1)
    Write-Host "생성된 exe: $($ExePath.FullName) ($SizeMb MB)" -ForegroundColor Green
} else {
    Write-Host "dist/ 안에서 exe 파일을 찾지 못했습니다." -ForegroundColor Red
    exit 1
}
