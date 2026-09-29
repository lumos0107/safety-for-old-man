# safety-for-old-man

팀 눈길손길 (제주대 창의융합 캡스톤디자인) — 다중 센서 융합 비전 기반 독거노인 안전 모니터링 시스템의 **개발용 자세 인식 시제품**.

폰·노트북 브라우저 카메라 영상을 개발 PC의 GPU로 보내 관절 17개를 인식하고 화면에 뼈대를 그린다.
원본 영상을 네트워크로 보내는 **개발용 구조**이며, 최종 시스템(엣지 처리·원본 영상 미전송)과 다르다.
영상은 저장하지 않는다. 촬영 전 대상자 동의를 받는다.

- 페이지: https://lumos0107.github.io/safety-for-old-man/
- 설계: [자세 인식](design/2026-09-29-web-pose-design.md) · [얼굴 윤곽](design/2026-09-30-face-outline-design.md) · [개발 경위와 결정 기록](design/개발_경위와_결정_기록.md) · [평가·보완 기록](design/2026-09-30-evaluation-rounds.md) · [3인 합의 점검 기록](design/2026-09-30-consensus-rounds.md)

> **보안 주의 — 페이지 주소를 다른 사이트와 함께 씀 (결정 필요):** GitHub Pages는 한 계정의 모든 저장소가 같은 출처(`https://lumos0107.github.io`)를 쓰고, 브라우저 저장소(localStorage)는 출처별이다. 이 계정에는 지금 이 저장소 말고도 Pages 사이트가 4개(abang-log-presentation, HealthAge, inventory-app, web-practice-2026) 있어, 그중 한 곳에 스크립트 결함이 생기면 폰에 저장된 토큰·서버 주소를 읽거나 바꿀 수 있다 (가능성은 낮지만 영향은 촬영 영상 유출). 권장: 이 페이지를 **전용 GitHub Organization으로 옮긴다** (옮기면 페이지 주소가 바뀌고, `ALLOWED_ORIGINS`를 새 주소로 바꾼 뒤 `gen_token.py --force`로 토큰을 교체, 폰마다 설정을 다시 넣는다). 대안: 다른 4개 사이트의 Pages를 끈다.

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

- 창에 **"서버 준비 완료"**가 뜨면 폰에서 시작한다 (모델을 불러오느라 몇 초, 첫 실행은 더 걸린다).
- 폰에 넣을 **서버 주소**는 실행 창에 나오는 `https://<pc이름>.<tailnet>.ts.net` (또는 `tailscale funnel status`). 서버는 이 PC 안의 18080번 포트(흔한 개발 포트를 피함)에서 돌고, 폰 주소에는 포트가 없다.
- 서버 창에는 준비 완료, 인증 성공·연결 대체, 허용되지 않은 주소 거부, 인증 실패(1분 요약)가 시각과 함께 한 줄씩 나온다. 토큰·영상·접속 IP는 남기지 않는다. 인증 실패가 계속 늘면 토큰 유출·스캔을 의심하고 토큰을 바꾼다.
- 이미 서버가 "서버 준비 완료"까지 떠 있으면 `run.ps1`은 Tailscale을 건드리기 전에 멈춘다. 첫 서버가 모델을 불러오는 동안에는 이 검사로 못 막으니 두 창을 띄우지 않는다.
- **창을 그냥 닫았거나 PC가 재부팅·전원 차단된 뒤에는** `tailscale funnel status`로 확인하고, 켜져 있으면 `tailscale funnel --https=443 off`. (남아 있으면 이 PC의 18080번에 다른 프로그램을 띄웠을 때 그것이 인터넷에 공개된다.)
- 토큰이 새었다면 `tools\gen_token.py --force` 후 서버 재시작 (TOKEN 줄만 바뀌고 다른 설정은 그대로).
- 서버는 Ultralytics의 사용 통계 전송을 끈 상태로 돈다 (`server/pose.py`).

## 사용 (폰·노트북)

1. 페이지를 열고 **설정**에 서버 주소와 토큰을 넣고 **저장** → **시작**.
2. 토큰 원본은 PC의 `server/.env`의 `TOKEN=` 뒤 값이다 (언제든 다시 볼 수 있음). 폰으로 옮길 때는 단체방 대신 '나에게 보내기'를 쓰고, 붙여 넣은 뒤 그 메시지는 지운다. 설정 창의 **보기**로 붙여 넣은 값을 확인할 수 있다.
3. 뼈대 색: 파랑 = 사람의 왼쪽, 주황 = 사람의 오른쪽, 노랑 = 몸통, 분홍 = 얼굴.

## 얼굴 윤곽 (시연용, 기본 끔)

설정 → "얼굴 윤곽 표시"를 켜면 얼굴 윤곽을 분홍 선으로 그린다. 폰·노트북 **브라우저 안에서** 계산하고(MediaPipe, `vendor/mediapipe/`), 서버로 보내거나 저장하지 않는다. 처음 켤 때 약 16MB를 받는다. 넘어짐 판단에는 쓰지 않는다.

- **거리 한계:** 얼굴 탐지기가 가까운 거리용이라 **얼굴이 화면 폭의 약 1/5 이상**일 때 잡힌다 (자동 테스트 실측: 약 1/5 → 잡힘, 약 1/10 이하 → 못 잡음). 폰을 가까이 들고 시연한다.
- MediaPipe의 사용 통계 전송(Google)은 페이지 보안 정책(CSP)이 막는다. **얼굴 윤곽 기능이** 기기 밖으로 보내는 것은 없다 (카메라 영상은 기존대로 뼈대 계산용 서버로 전송된다).

## 실기기 확인 (한 곳에 모아 기록)

자동 테스트는 가짜 카메라로 돌므로 아래는 실제 폰·노트북으로 확인해 이 표에 적는다.

| 항목 | 방법 | 기기 | 결과 | 날짜 |
|---|---|---|---|---|
| 기본 동작 | 페이지 → 설정 → 시작, 뼈대가 사람 위에 겹치는지 | | | |
| 얼굴 윤곽 거리 | 0.5 / 1 / 2m에서 윤곽이 나오는지, 가까이서 잡은 뒤 물러났을 때 어디까지 유지되는지 | | | |
| 속도·발열 | 얼굴 윤곽 켬/끔에서 상단 fps(뼈대) 비교, 10분 뒤 발열 | | | |
| 앱 전환 복구 | 시연 중 다른 앱(전화·카메라)에 갔다 돌아왔을 때 영상·뼈대·윤곽이 다시 움직이는지 | | | |
| 아이폰 Safari | 얼굴 윤곽이 켜지는지(WebAssembly 보안 정책), 권한 거부 안내의 경로가 맞는지 | | | |
| 네트워크 전환 | 와이파이 ↔ LTE로 바꿀 때 '서버 꺼짐' → '연결됨'으로 돌아오는지 | | | |
| 화면 회전 | 세로 ↔ 가로에서 뼈대가 맞게 겹치는지 | | | |
| 카메라 끊김 | 다른 앱이 카메라를 쓸 때 '카메라가 꺼졌습니다' 안내가 뜨는지 (거짓 안내가 없는지) | | | |
| 모델 미리 받기 | 전날 받아 둔 얼굴 모델이 당일에 바로 켜지는지 | | | |

## 화면을 고칠 때

`main`에 push하면 1~10분 안에 **시연 페이지가 바로 바뀐다** (Pages 캐시 최대 10분). 그래서:

1. 로컬 확인: `server/.env`의 `ALLOWED_ORIGINS`에 `,http://localhost:5500`을 더하고 서버를 켠 뒤, `.venv\Scripts\python tools\serve_page.py`로 화면만 띄워 `http://localhost:5500/`에서 확인한다. (`python -m http.server`는 저장소 전체를 내줘 `server/.env`의 토큰이 열리므로 쓰지 않는다.)
2. `node --test "web_tests/*.test.mjs"`와 브라우저 종단 검증을 통과한 뒤 push.
3. **시연 전날부터는 `main`에 push하지 않는다.**

## 테스트

필요: Python 가상환경(위), Node 21 이상(`node --test`의 glob), 설치된 Chrome. 종단 검증은 포트 5500·8765·8766을 쓴다.

```powershell
.venv\Scripts\python -m pytest                      # 서버 (GPU 필요)
node --test "web_tests/*.test.mjs"                  # 프런트 순수 함수·얼굴 상태 머신
.venv\Scripts\python -X utf8 tools\e2e_browser.py   # 브라우저 종단 (Chrome 필요)
```

종단 검증 중 `ConnectionResetError [WinError 10054]` 트레이스백이 찍힐 수 있다. 테스트가 서버를 일부러 끄고 다시 켜는 단계에서 나는 것으로, 마지막 줄이 "전체 통과"면 문제없다.

## 시연 체크리스트

- **전날:** 폰에서 설정 → 얼굴 윤곽 켬 → 저장 → **시작** → 상단 "얼굴" 칸이 `-`에서 숫자로 바뀔 때까지 기다린다 (서버 없이도 됨, 이때 16MB를 받는다). 저장만 하고 시작하지 않으면 받지 않는다. 당일 시작 전에 한 번 더 확인한다 (캐시가 지워졌을 수 있음). 전날부터 `main` push 금지.
- **시작 전:** PC 절전 끄기, `run.ps1` 실행 → "서버 준비 완료" 확인.
- **한 기기만:** 시연 중에는 다른 팀원 기기·탭에서 시작하지 않는다 (새 연결이 시연 폰을 밀어내고, 밀려난 폰은 스스로 되찾지 않는다).
- **순서:** 전신 뼈대는 얼굴 윤곽을 끈 채 멀리서 → 얼굴 시연 전에 설정에서 켜고 가까이. 설정 창에는 서버 주소가 보이므로 **프로젝터에 비치지 않는 순간에 연다.** 토큰 "보기"는 누르지 않는다. 토큰이 드러났으면 끝난 뒤 `gen_token.py --force`.
- 촬영 대상자 동의. 화면 아래 안내대로 영상은 서버(개발 PC)로 전송되지만 저장하지 않는다고 설명한다.
- 끝나면 PC 창에서 Ctrl+C (Funnel도 함께 꺼짐).

## 라이선스 메모

- 서버가 쓰는 Ultralytics와 YOLO11 가중치는 **AGPL-3.0**이다. 지금은 공개 저장소라 소스 공개 조건과 맞지만, 비공개·상용(엣지 장치, 기업 연계 등)으로 쓸 때는 라이선스를 검토해야 한다.
- 화면의 MediaPipe 파일과 얼굴 모델은 Apache-2.0이다 (`vendor/mediapipe/SOURCE.md`).
