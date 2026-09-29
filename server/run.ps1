# 백엔드 실행: Funnel 켜기 → 서버 실행 → 종료(Ctrl+C) 시 Funnel 끄기
# 사용: powershell -ExecutionPolicy Bypass -File server\run.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Root ".venv\Scripts\python.exe"
$Ts = (Get-Command tailscale -ErrorAction SilentlyContinue).Source
if (-not $Ts) { $Ts = "C:\Program Files\Tailscale\tailscale.exe" }
if (-not (Test-Path $Ts)) { throw "Tailscale을 찾을 수 없습니다. https://tailscale.com/download 에서 설치하고 로그인하세요." }

if (-not (Test-Path (Join-Path $PSScriptRoot ".env"))) {
    throw "server\.env가 없습니다. 먼저 실행: .venv\Scripts\python tools\gen_token.py"
}
Set-Location $Root

# 이미 서버가 떠 있으면 여기서 멈춘다: 두 번째 서버는 포트 충돌로 곧 끝나고, 그때 finally가
# 첫 번째 서버의 Funnel까지 꺼 버린다 (Tailscale을 건드리기 전에 확인)
if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) {
    throw "8000번 포트를 이미 쓰고 있습니다 (서버가 이미 실행 중?). 그 창에서 Ctrl+C로 끈 뒤 다시 실행하세요."
}

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
