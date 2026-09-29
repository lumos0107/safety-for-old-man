# safety-for-old-man

팀 눈길손길 (제주대 창의융합 캡스톤디자인) — 다중 센서 융합 비전 기반 독거노인 안전 모니터링 시스템의 **개발용 자세 인식 시제품**.

폰·노트북 브라우저 카메라 영상을 개발 PC의 GPU로 보내 관절 17개를 인식하고 화면에 뼈대를 그린다.
원본 영상을 네트워크로 보내는 **개발용 구조**이며, 최종 시스템(엣지 처리·원본 영상 미전송)과 다르다.
영상은 저장하지 않는다. 촬영 전 대상자 동의를 받는다.

- 페이지: https://lumos0107.github.io/safety-for-old-man/
- 설계: [자세 인식](design/2026-09-29-web-pose-design.md) · [얼굴 윤곽](design/2026-09-30-face-outline-design.md) · [개발 경위와 결정 기록](design/개발_경위와_결정_기록.md) · [평가·보완 기록](design/2026-09-30-evaluation-rounds.md)

> **운영 원칙:** GitHub Pages는 계정의 모든 저장소가 같은 출처(`https://lumos0107.github.io`)를 쓴다. 토큰이 그 출처의 브라우저 저장소에 있으므로, **이 계정에는 다른 Pages 사이트를 두지 않는다.** 두어야 하면 이 페이지를 전용 계정·조직으로 옮긴다.

## 서버 (개발 PC)

**준비물:** Windows, NVIDIA GPU와 드라이버(CUDA 12.8 지원), [uv](https://docs.astral.sh/uv/), [Tailscale](https://tailscale.com/download) (설치 후 로그인, 로그인 계정은 2단계 인증 권장), 인터넷 (첫 실행 때 자세 모델 `yolo11n-pose.pt`를 자동으로 받는다 — `*.pt`는 저장소에 없음).

**처음 한 번**

```powershell
uv venv --python 3.12 .venv
uv pip install --python .venv torch torchvision --index-url https://download.pytorch.org/whl/cu128
uv pip install --python .venv -r server/requirements.txt
.venv\Scripts\python tools\gen_token.py          # server/.env 생성, 폰에 넣을 토큰 출력
```

첫 `run.ps1` 실행 때 Tailscale이 Funnel·HTTPS 허용 링크를 출력하면 브라우저에서 한 번 허용한다.

**매번**

```powershell
powershell -ExecutionPolicy Bypass -File server\run.ps1   # Funnel 켜고 서버 실행, Ctrl+C로 둘 다 끔
```

- 폰에 넣을 **서버 주소**는 실행 창에 나오는 `https://<pc이름>.<tailnet>.ts.net` (또는 `tailscale funnel status`).
- 이미 서버가 떠 있으면 `run.ps1`은 Tailscale을 건드리기 전에 멈춘다 (두 번째 실행이 첫 서버의 Funnel을 끄지 않게).
- 창을 그냥 닫았다면 `tailscale funnel status`로 확인하고 `tailscale funnel --https=443 off`.
- 토큰이 새었다면 `tools\gen_token.py --force` 후 서버 재시작 (허용 주소 설정은 유지된다).

## 사용 (폰·노트북)

페이지 → 설정에 서버 주소(`https://<pc이름>.<tailnet>.ts.net`)와 토큰 입력 → 시작.

## 얼굴 윤곽 (시연용, 기본 끔)

설정 → "얼굴 윤곽 표시"를 켜면 얼굴 윤곽을 분홍 선으로 그린다. 폰·노트북 **브라우저 안에서** 계산하고(MediaPipe, `vendor/mediapipe/`), 서버로 보내거나 저장하지 않는다. 처음 켤 때 약 16MB를 받는다. 넘어짐 판단에는 쓰지 않는다.

- **거리 한계:** 얼굴 탐지기가 가까운 거리용이라 **얼굴이 화면 폭의 약 1/5 이상**일 때 잡힌다 (자동 테스트 실측: 약 1/5 → 잡힘, 약 1/10 이하 → 못 잡음). 폰을 가까이 들고 시연한다. 한 번 잡힌 뒤 물러났을 때 얼마나 유지되는지는 실기기로 확인할 것.
- MediaPipe의 사용 통계 전송(Google)은 페이지 보안 정책(CSP)이 막는다. **얼굴 윤곽 기능이** 기기 밖으로 보내는 것은 없다 (카메라 영상은 기존대로 뼈대 계산용 서버로 전송된다).
- 실기기 확인할 것: 거리별(0.5 / 1 / 2m) 인식, 켬/끔에 따른 뼈대 fps·발열, 아이폰 Safari 동작과 앱 전환 뒤 복구.

## 테스트

필요: Python 가상환경(위), Node 21 이상(`node --test`의 glob), 설치된 Chrome. 종단 검증은 포트 5500·8765·8766을 쓴다.

```powershell
.venv\Scripts\python -m pytest                      # 서버 (GPU 필요)
node --test "web_tests/*.test.mjs"                  # 프런트 순수 함수
.venv\Scripts\python -X utf8 tools\e2e_browser.py   # 브라우저 종단 (Chrome 필요)
```

종단 검증 중 `ConnectionResetError [WinError 10054]` 트레이스백이 찍힐 수 있다. 테스트가 서버를 일부러 끄고 다시 켜는 단계에서 나는 것으로, 마지막 줄이 "전체 통과"면 문제없다.

## 시연 체크리스트

- 시작 전: PC 절전 끄기, `run.ps1` 실행, 폰에서 페이지를 열어 얼굴 윤곽을 한 번 켜 모델(16MB)을 미리 받아 두기 (행사장 네트워크가 느릴 수 있음).
- 프로젝터에 폰 화면을 비출 때 설정 창의 토큰 "보기"를 누르지 않는다. 토큰이 드러났으면 끝난 뒤 `gen_token.py --force`.
- 전신 뼈대(멀리서)와 얼굴 윤곽(가까이서)은 거리 한계 때문에 한 장면에 같이 보여 주기 어렵다 — 순서를 나눠 시연한다.
- 촬영 대상자 동의. 화면 아래 안내대로 영상은 서버(개발 PC)로 전송되지만 저장하지 않는다고 설명한다.
- 끝나면 PC 창에서 Ctrl+C (Funnel도 함께 꺼짐).

