# PyArmor 난독화 + PyInstaller onefile/windowed exe build script for 인수인계10분.
# Usage (from repo root): powershell -File scripts/build_exe_obfuscated.ps1
#
# 순서: 1) 이전 산출물 정리 -> 2) PyArmor로 app/ 난독화(obf_app/) ->
#       3) 트라이얼 라이선스 용량 제한으로 난독화 불가능한 대형 파일은 원본
#          그대로 복사 -> 4) 난독화된 obf_app/ 기준으로 PyInstaller 빌드 ->
#       5) 결과 exe 경로/크기 출력.
#
# PyArmor 무료(trial) 라이선스는 파일 하나가 일정 크기를 넘으면
# "out of license" 에러로 난독화를 거부한다(공식 문서에 정확한 줄 수는
# 명시되어 있지 않음 - 실측으로 552줄은 통과, 1388줄부터 실패 확인).
# 아래 6개 파일이 현재 그 한도를 넘는 파일들이며, 유료 라이선스가 없는 한
# 이 파일들은 난독화 없이 일반 소스로 exe에 포함된다.
$OversizedFiles = @(
    "app/ui/main_window.py",
    "app/services/rag_package_builder.py",
    "app/services/rag_search.py",
    "app/ui/chatbot_dialog.py",
    "app/services/report_writer.py",
    "app/ui/memodialog.py"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

Write-Host "[1/5] 이전 산출물 정리 (obf_app/, dist/, build/)" -ForegroundColor Cyan
Remove-Item -Recurse -Force "obf_app" -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force "dist" -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force "build" -ErrorAction SilentlyContinue

Write-Host "[2/5] PyArmor로 app/ 난독화 -> obf_app/" -ForegroundColor Cyan
$PyarmorExcludeArgs = @("--exclude", "app/__pycache__")
foreach ($f in $OversizedFiles) {
    $PyarmorExcludeArgs += "--exclude"
    $PyarmorExcludeArgs += $f
}
py -B -m pyarmor.cli.__main__ gen -O obf_app -r @PyarmorExcludeArgs app
if ($LASTEXITCODE -ne 0) {
    Write-Host "PyArmor 난독화 실패 (exit code $LASTEXITCODE)" -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host "[3/5] 트라이얼 용량 제한 초과 파일 원본 그대로 복사 ($($OversizedFiles.Count)개)" -ForegroundColor Cyan
foreach ($f in $OversizedFiles) {
    $dest = Join-Path "obf_app" $f
    Copy-Item -Path $f -Destination $dest -Force
    Write-Host "  (미난독화) $f" -ForegroundColor Yellow
}

Write-Host "[4/5] PyInstaller 빌드 실행 (obf_app 기준)" -ForegroundColor Cyan
py -B -m PyInstaller handover_analyzer_obfuscated.spec --noconfirm
if ($LASTEXITCODE -ne 0) {
    Write-Host "빌드 실패 (exit code $LASTEXITCODE)" -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host "[5/5] 빌드 완료" -ForegroundColor Cyan
$ExePath = Get-ChildItem -Path "dist" -Filter "*.exe" -Recurse | Select-Object -First 1
if ($ExePath) {
    $SizeMb = [math]::Round($ExePath.Length / 1MB, 1)
    Write-Host "생성된 exe: $($ExePath.FullName) ($SizeMb MB)" -ForegroundColor Green
} else {
    Write-Host "dist/ 안에서 exe 파일을 찾지 못했습니다." -ForegroundColor Red
    exit 1
}
