# 얼굴 윤곽 표시 설계

작성 2026-09-30 · 팀 눈길손길 · 기반 설계 `design/2026-09-29-web-pose-design.md` (이 문서는 그 위에 얹는 추가 기능)

## 1. 목적

폰 화면에서 사람의 뼈대에 더해 **얼굴 윤곽(턱선·이마 둘레)**을 그린다.

지금 자세 모델(YOLO11n-pose)은 얼굴에서 코·눈 2개·귀 2개의 5점만 준다. 이 점들로는 윤곽을 만들 수 없으므로 얼굴 전용 랜드마크 모델을 추가한다.

넘어짐·휘청임 판단에는 쓰지 않는다. 판단에 필요한 머리 위치는 기존 5점으로 충분하다. 이 기능은 **화면 표시용**이다.

## 2. 범위

포함
- 서버: 얼굴 랜드마크 모델, 얼굴 윤곽 36점 반환
- 통신 규격: 프레임마다 얼굴 계산을 요청하는 선택 필드, 결과의 `faces` 필드, `ready`의 기능 여부 표시
- 화면: 분홍 닫힌 윤곽선, 얼굴 수 표시, 설정의 켜기/끄기

제외
- 눈·눈썹·입 모양, 표정·통증 판단, 고개 방향 계산
- 얼굴로 사람을 식별하는 모든 기능
- 얼굴과 사람(뼈대)을 짝짓기 — 화면에 따로 그리기만 한다

## 3. 모델

- **MediaPipe Face Landmarker** (tasks API, 이미지 모드). 얼굴당 478점을 주지만 그중 **윤곽(face oval) 36점만** 쓴다.
- 윤곽 36점의 순서 (MediaPipe 얼굴 메시 번호, 이 순서로 이으면 닫힌 윤곽선이 된다):
  `10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288, 397, 365, 379, 378, 400, 377, 152, 148, 176, 149, 150, 136, 172, 58, 132, 93, 234, 127, 162, 21, 54, 103, 67, 109`
- 설정: 얼굴 최대 3개, 얼굴 탐지 신뢰도 0.5.
- 모델 파일 `face_landmarker.task`(약 4MB)는 저장소에 넣지 않는다. 서버가 처음 뜰 때 없으면 아래 주소에서 내려받아 `server/models/`에 둔다. `.gitignore`에 `*.task`를 더한다.
  `https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task`
- 거리 한계: 얼굴 탐지는 가까운 거리(약 2m 이내)용이다. 실제 설치 거리(3~4m)에서는 윤곽이 안 나올 수 있다. 시연은 폰을 가까이 들고 한다.

## 4. 설치 (의존성 충돌)

`mediapipe`(1.0.1)는 `opencv-contrib-python`을 함께 설치하려 한다. 이미 있는 `opencv-python`(ultralytics 의존)과 같은 `cv2` 모듈을 덮어써 충돌한다.

- 1안(우선): `mediapipe`를 `--no-deps`로 설치하고, 필요한 의존성(`absl-py`, `flatbuffers`, `sounddevice`, `cffi`)만 따로 설치한다. `opencv-contrib-python`은 설치하지 않는다. Face Landmarker(tasks API)가 이 상태로 동작하는지 먼저 확인한다.
- 2안(1안 실패 시): `opencv-python`을 빼고 `opencv-contrib-python`(상위 호환)만 둔다. ultralytics는 `cv2`만 쓰므로 동작하지만 `uv pip check`는 경고를 낸다. 이 경우 README에 이유를 적는다.
- 어느 쪽이든 `server/requirements.txt`에 설치 방법을 주석으로 남기고 `requirements.lock.txt`를 다시 만든다.

## 5. 통신 규격 변경

기존 규격(기반 설계 5장)에서 달라지는 것만 적는다. 기존 클라이언트는 그대로 동작한다.

| 메시지 | 변경 |
|---|---|
| `ready` | `{"type":"ready","model":"yolo11n-pose","face":true}` — `face`는 서버가 얼굴 윤곽을 계산할 수 있는지 (모델 로드 실패면 `false`) |
| `frame` | 선택 필드 `"face": true`를 붙이면 그 프레임에 대해 얼굴 윤곽을 계산한다. `face`가 불린이 아니면 `bad_message` |
| `result` | `face: true`로 요청했고 서버가 계산할 수 있으면 `"faces": [{"outline": [[x, y], … 36개]}]`를 더한다 (얼굴이 없으면 `[]`). 요청하지 않았으면 `faces` 키가 없다 |

- 좌표는 기존과 같이 이미지 크기 대비 0~1 비율이고, 범위를 벗어나면 0~1로 자른다.
- 서버가 `face: false`인데 요청이 오면 `faces` 키 없이 결과만 돌려준다 (오류로 보지 않음).

## 6. 서버

- `server/face.py` — `FaceModel(model_path)`: 파일이 없으면 내려받고 모델을 올린 뒤 예열한다. `outlines(img_bgr) -> list[list[[x, y]]]`.
- `create_app(settings, predictor, face_predictor=None)` — `face_predictor`가 있으면 `ready.face = true`.
- 얼굴 계산은 자세 추론과 **같은 단일 추론 스레드**에서 자세 다음에 실행한다 (기반 설계의 "모델 호출이 겹치지 않음" 유지). 얼굴 계산 중 예외가 나면 그 프레임은 기존처럼 `server_error`.
- `build_app()` — 얼굴 모델 로드(다운로드 포함)가 실패하면 서버는 얼굴 기능 없이 뜬다. 로그에 경고 한 줄을 남기고 `ready.face = false`.
- 성능: CPU로 프레임당 10~20ms 더 걸릴 것으로 본다 (왕복 약 50fps → 30~40fps). 구현 후 실측해 README에 적는다.

## 7. 화면

- 설정 창에 체크박스 **"얼굴 윤곽 표시"**. 기본은 켬. `localStorage` 키 `pose.face`.
- 켜져 있고 서버의 `ready.face`가 `true`이면 프레임에 `face: true`를 붙인다.
- 서버가 `ready.face = false`이면 안내문에 "서버에서 얼굴 윤곽을 쓸 수 없습니다"를 띄우고 요청하지 않는다.
- 상단 표시줄에 **얼굴 수** (`#faces`). 꺼져 있으면 `-`.
- 그리기: 얼굴마다 36점을 이은 **분홍(`COLORS.face`) 닫힌 선**, 두께 2px. 사람 뼈대 다음에 그린다. 동기 표시·좌우 반전·레터박스는 뼈대와 같은 좌표 변환을 쓴다.
- `lib.js`에 `faceOutline(outline, rect) -> [[x, y], …]` (0~1 → 화면 좌표) 순수 함수를 두고 테스트한다.

## 8. 사생활

- 얼굴 윤곽 좌표는 사람을 알아보는 데 쓰일 수 있는 정보에 가깝다. 기반 설계와 같이 **메모리에서만 처리하고 저장·로그에 남기지 않는다.**
- 화면에서 끌 수 있고, 끄면 서버는 얼굴 계산 자체를 하지 않는다.
- 넘어짐 판단 등 이후 기능은 얼굴 윤곽을 입력으로 쓰지 않는다. 심사·발표에서는 "시연용 표시 기능"으로만 소개한다.

## 9. 테스트

자동 (서버)
- `FaceModel`: `ultralytics.utils.ASSETS / "zidane.jpg"`(가까이 찍힌 2명) → 얼굴 1개 이상, 얼굴마다 36점, 좌표 0~1. 빈 이미지 → `[]`.
- WebSocket (가짜 얼굴 함수 주입)
  - `face: true` → `faces` 포함, `face` 없음 → `faces` 키 없음
  - `face`가 불린이 아님 → `bad_message`
  - `face_predictor` 없는 서버 → `ready.face = false`, 요청해도 `faces` 없음
  - 얼굴 함수 예외 → `server_error`, 연결 유지
- 통합 (실제 uvicorn): `zidane.jpg` + `face: true` → `faces` 1개 이상.

자동 (화면)
- Node: `faceOutline` 좌표 변환.
- 브라우저 종단: 기존 8단계 유지. 가짜 카메라를 `zidane.jpg`로 한 브라우저를 따로 띄워 `#faces`가 1 이상인지 확인하고 스크린샷으로 윤곽이 얼굴 위에 겹치는지 본다. 설정에서 끄면 `#faces`가 `-`인지 확인한다.

## 10. 반영 절차

- 구현·테스트 후 `main`에 push한다 (Pages 반영 1~2분).
- 서버 코드가 바뀌므로 PC에서 실행 중인 서버를 한 번 다시 시작해야 한다 (Ctrl+C 후 같은 명령). 첫 시작 때 얼굴 모델을 내려받는다.
- 기반 설계 문서 5장(통신 규격)에 이 문서의 변경을 요약해 반영한다.
