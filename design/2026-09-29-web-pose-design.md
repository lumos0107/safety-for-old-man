# 웹 카메라 자세 인식 시제품 설계

작성 2026-09-29 · 팀 눈길손길 (창의융합 캡스톤디자인)

## 1. 목적

카메라가 없는 PC의 GPU로 자세 인식을 돌리고, 카메라는 폰·노트북 브라우저로 대신한다.
폰이나 노트북에서 웹페이지에 들어가 카메라를 켜면, PC가 사람과 관절 17개를 인식해 결과를 돌려주고, 브라우저가 카메라 화면 위에 뼈대를 그린다.
어느 네트워크에서든 접속할 수 있어야 한다.

이 시제품의 목적은 **인식 파이프라인이 실제 카메라 영상에서 동작하는지 확인하는 것**이다. 넘어짐·휘청임 판단은 다음 단계에서 이 위에 얹는다.

> 개발용 구조다. 원본 영상을 네트워크로 보내므로 계획서의 "엣지 처리·원본 영상 미전송" 원칙과 다르다. 최종 시스템(라즈베리파이 엣지)으로 소개하지 않는다.

## 2. 범위

포함
- 브라우저 카메라 → PC 추론 → 관절 좌표 반환 → 화면 오버레이
- 접속 토큰, 접속 출처 제한
- 연결 상태 표시, 자동 재연결
- 백엔드 자동 테스트, 동영상 파일로 전체 경로 검증

제외 (다음 단계 이후)
- 넘어짐·휘청임 판단 규칙
- 여러 명 동시 접속, 기록 저장, 로그인 기능

## 3. 구조

```
[폰/노트북 브라우저]  https://lumos0107.github.io/safety-for-old-man/   ← 프런트 (GitHub Pages)
   │ 카메라 프레임을 긴 변 640px JPEG로 줄여 한 장씩 전송 (WSS)
   ▼
https://<pc이름>.<tailnet>.ts.net/ws                                   ← Tailscale Funnel
   ▼
[이 PC] FastAPI 백엔드 (127.0.0.1:8000) + YOLO11n-pose (RTX 4080 SUPER)
   │ 관절 좌표 JSON만 반환 · 이미지는 메모리에서만 처리, 디스크·로그에 남기지 않음
   ▼
[브라우저] 로컬 카메라 화면 위에 박스·뼈대를 그림
```

- 백엔드는 `127.0.0.1`에만 열고, 바깥 접속은 Funnel을 통해서만 들어온다.
- Funnel은 TLS를 이 PC에서 풀기 때문에 Tailscale 중계 서버는 영상 내용을 볼 수 없다.
- 전송은 **한 장 보내고 결과를 받은 뒤 다음 장**(stop-and-wait). 네트워크가 느려도 지연이 쌓이지 않고 fps만 떨어진다. 화면의 카메라 영상은 기기에서 바로 나오므로 끊기지 않는다.

## 4. 통신 규격 (WebSocket `/ws`)

1. **접속**: 서버가 `Origin` 헤더를 허용 목록(`ALLOWED_ORIGINS`)과 비교한다. 목록에 없으면 코드 `4003`으로 닫는다.
2. **인증**: 클라이언트의 첫 메시지는 텍스트 `{"type":"auth","token":"..."}`. 맞으면 서버가 `{"type":"ready","model":"yolo11n-pose"}`를 보낸다. 틀리면 `4001`, 5초 안에 오지 않으면 `4008`으로 닫는다.
3. **추론**: 클라이언트가 JPEG 바이너리를 보내면 서버가 답한다.

```json
{"type":"result","infer_ms":8.7,
 "people":[{"box":[x1,y1,x2,y2],"score":0.91,
            "kpts":[[x,y,conf], ... 17개]}]}
```
   좌표는 이미지 크기 대비 0~1 비율이다. 키포인트 순서는 COCO 17점(코, 눈, 귀, 어깨, 팔꿈치, 손목, 골반, 무릎, 발목).
4. **오류**: 디코딩 불가 → `{"type":"error","code":"bad_image"}`, 1 MB 초과 → `{"type":"error","code":"too_large"}`. 연결은 유지한다.

## 5. 구성 요소

### 프런트 (저장소 루트, GitHub Pages)
- `index.html`, `app.js`, `style.css` — 프레임워크·빌드 없음
- 폰 세로 화면 기준 배치
  - 위: 상태(설정 필요/연결 중/연결됨/토큰 오류/허용되지 않은 주소/서버 꺼짐), fps, 처리 ms, 인원
  - 가운데: 카메라 영상 + 오버레이 캔버스(박스·뼈대)
  - 아래: 시작/정지, 앞/뒤 카메라 전환, 설정(서버 주소·토큰)
- 서버 주소와 토큰은 `localStorage`에만 저장한다. 코드와 저장소에는 넣지 않는다.
- 연결이 끊기면 3초 뒤 재연결, 실패할수록 최대 10초 간격까지 늘린다.
- 카메라 권한 거부 시 브라우저 설정에서 허용하는 방법을 안내한다.

### 백엔드 (`server/`)
- `app.py` — FastAPI, WebSocket `/ws`, 헬스체크 `GET /health`
- 모델은 시작할 때 한 번 올리고 예열한다. 추론은 스레드로 넘겨 이벤트 루프를 막지 않는다.
- 설정은 `server/.env`: `TOKEN`, `ALLOWED_ORIGINS`(기본 `https://lumos0107.github.io`, 개발용 `http://localhost:5500`)
- 토큰 비교는 `secrets.compare_digest`
- `run.ps1` — 가상환경으로 uvicorn 실행. Funnel은 한 번 `tailscale funnel --bg 8000`으로 켜 두면 유지된다.

### 도구 (`tools/`)
- `stream_video.py` — 동영상 파일을 프레임 단위로 `/ws`에 보내 fps·인원을 출력 (카메라 없이 전체 경로 검증)
- `webcam_pose.py` — 처음 만든 로컬 웹캠 시험 스크립트 (보관용)

## 6. 파일 구조

```
index.html  app.js  style.css      프런트
server/app.py  server/requirements.txt  server/.env.example  server/run.ps1
server/tests/test_ws.py
tools/stream_video.py  tools/webcam_pose.py
design/2026-09-29-web-pose-design.md
.gitignore                          .venv/ *.pt .env 영상·사진 파일
```

공개 저장소이므로 토큰(`.env`), 모델 가중치, 테스트 중 찍은 영상·사진, 팀원 개인정보는 올리지 않는다.
Pages가 저장소 루트를 배포하므로 `server/` 소스도 웹에서 보이지만, 비밀값이 없으므로 문제없다.

## 7. 테스트

자동 (pytest, FastAPI TestClient)
- 올바른 토큰 → `ready`
- 틀린 토큰 → `4001`, 허용되지 않은 출처 → `4003`
- 깨진 이미지 → `bad_image` 후 연결 유지
- 예시 사진(ultralytics `bus.jpg`) → 사람 3명 이상, 사람마다 관절 17개, 좌표 0~1 범위

수동
- `tools/stream_video.py`로 로컬 서버와 Funnel 주소 각각 검증
- 폰 브라우저로 Pages 접속 → 설정 입력 → 뼈대 표시 확인 (사용자)

## 8. 사용자가 직접 할 일

- 이 PC에 Tailscale 설치·로그인, 관리 화면에서 Funnel 허용
- 로그인 계정 2단계 인증
- 첫 `git push` 때 GitHub 로그인
