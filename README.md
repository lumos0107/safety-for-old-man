# safety-for-old-man

팀 눈길손길 (제주대 창의융합 캡스톤디자인) — 다중 센서 융합 비전 기반 독거노인 안전 모니터링 시스템의 **개발용 자세 인식 시제품**.

폰·노트북 브라우저 카메라 영상을 개발 PC의 GPU로 보내 관절 17개를 인식하고 화면에 뼈대를 그린다.
원본 영상을 네트워크로 보내는 **개발용 구조**이며, 최종 시스템(엣지 처리·원본 영상 미전송)과 다르다.
영상은 저장하지 않는다. 촬영 전 대상자 동의를 받는다.

- 페이지: https://lumos0107.github.io/safety-for-old-man/
- 설계: [design/2026-09-29-web-pose-design.md](design/2026-09-29-web-pose-design.md)

## 서버 (개발 PC)

```powershell
uv venv --python 3.12 .venv
uv pip install --python .venv torch torchvision --index-url https://download.pytorch.org/whl/cu128
uv pip install --python .venv -r server/requirements.txt
.venv\Scripts\python tools\gen_token.py          # server/.env 생성, 토큰 출력
powershell -ExecutionPolicy Bypass -File server\run.ps1   # Funnel 켜고 서버 실행, Ctrl+C로 둘 다 끔
```

창을 그냥 닫았다면 `tailscale funnel status`로 확인하고 `tailscale funnel --https=443 off`.
토큰이 새었다면 `tools\gen_token.py --force` 후 서버 재시작.

## 사용 (폰·노트북)

페이지 → 설정에 서버 주소(`https://<pc이름>.<tailnet>.ts.net`)와 토큰 입력 → 시작.

## 테스트

```powershell
.venv\Scripts\python -m pytest                      # 서버 (GPU 필요)
node --test "web_tests/*.test.mjs"                  # 프런트 순수 함수
.venv\Scripts\python -X utf8 tools\e2e_browser.py   # 브라우저 종단 (Chrome 필요)
```
