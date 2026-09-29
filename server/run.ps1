# 백엔드 실행: Funnel 켜기 → 서버 실행 → 종료(Ctrl+C) 시 Funnel 끄기
# 사용: powershell -ExecutionPolicy Bypass -File server\run.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Root ".venv\Scripts\python.exe"
$Ts = "C:\Program Files\Tailscale\tailscale.exe"

if (-not (Test-Path (Join-Path $PSScriptRoot ".env"))) {
    throw "server\.env가 없습니다. 먼저 실행: .venv\Scripts\python tools\gen_token.py"
}
Set-Location $Root

# 출력을 숨기지 않는다: Funnel·HTTPS가 아직 허용되지 않았으면 허용 링크를 출력하고 기다린다.
# 이미 켜져 있으면 같은 설정으로 다시 적용된다.
& $Ts funnel --bg 8000
if ($LASTEXITCODE -ne 0) { throw "Funnel을 켜지 못했습니다. 'tailscale funnel status'를 확인하세요." }

try {
    & $Py -m uvicorn --factory server.app:build_app --host 127.0.0.1 --port 8000 `
        --ws-max-size 2097152 --log-level warning
}
finally {
    & $Ts funnel --https=443 off | Out-Null
    Write-Host "Funnel 꺼짐"
}
