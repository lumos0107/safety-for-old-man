# 얼굴 윤곽 표시 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 브라우저에서 MediaPipe Face Landmarker로 얼굴 윤곽 36점을 계산해 뼈대와 함께 그린다 (기본 끔, 서버 무변경).

**Architecture:** 프런트만 바뀐다. `face.js`가 모델 불러오기·계산 일정·수명을 맡고, `lib.js`의 순수 함수 `faceOutline`이 좌표를 만든다. `app.js`는 설정·그리기·동기 표시 연결만 한다. MediaPipe 파일은 `vendor/mediapipe/`에 두고 Pages가 제공한다.

**Tech Stack:** `@mediapipe/tasks-vision` 1.0.1 (ES 모듈 + WASM SIMD), `face_landmarker.task` float16, 바닐라 JS, Node 테스트, Playwright(설치된 Chrome) 종단 검증

**Spec:** `design/2026-09-30-face-outline-design.md` (검토 2건 반영판)

## Global Constraints

- 서버(`server/`)·통신 규격·서버 테스트는 바꾸지 않는다.
- 기본 끔, `localStorage` 키 `pose.face`. 얼굴 좌표는 서버로 보내지 않고 저장하지 않는다.
- CSP: `default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; connect-src 'self' wss: ws://localhost:* ws://127.0.0.1:*; img-src 'self' blob:; media-src 'self' blob:` — 외부 주소 금지.
- 수치: 얼굴 최대 3, 탐지 신뢰도 0.5, 계산 상한 초당 15회, 불러오기 시간 제한 30초, 윤곽 두께 2px, 색 `COLORS.face`.
- SIMD 판만 포함하고 경로를 고정한다. 비SIMD 브라우저는 얼굴 기능만 "쓸 수 없음".
- 커밋 메시지 끝에 `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. push는 Task 5에서.

## Review Focus

1. 켜기·끄기 빠른 반복 → 모델이 하나만, 마지막 상태를 따름 (Task 4 e2e)
2. 서버 없이 실시간 표시에서 윤곽이 나옴 (Task 4 e2e)
3. 동기 표시에서 윤곽이 보낸 프레임과 같은 순간 (Task 4 구현 규칙, 스크린샷)
4. CSP 위반·페이지 오류 없음 (Task 3 수집기, 모든 e2e 단계)
5. 끄면 모델을 닫고 계산이 멈춤, `#faces`가 `-` (Task 4 e2e)

---

### Task 1: 외부 파일 포함 (vendor)

**Files:** Create `vendor/mediapipe/vision_bundle.mjs`, `vendor/mediapipe/wasm/vision_wasm_internal.{js,wasm}`, `vendor/mediapipe/face_landmarker.task`, `vendor/mediapipe/LICENSE`, `vendor/mediapipe/SOURCE.md`, `.gitattributes`

- [ ] npm 패키지 `@mediapipe/tasks-vision@1.0.1` 압축에서 위 세 파일을 복사한다 (비SIMD·`.cjs`·`.map`·`module_internal` 제외).
- [ ] 모델 `https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task`를 받는다.
- [ ] Apache-2.0 전문을 `https://raw.githubusercontent.com/google-ai-edge/mediapipe/master/LICENSE`에서 받아 `LICENSE`로 둔다.
- [ ] `SOURCE.md`에 출처 주소·버전·파일별 크기·SHA-256(`sha256sum`)을 적는다.
- [ ] `.gitattributes`: `vendor/** linguist-vendored`, `*.wasm binary`, `*.task binary`.
- [ ] 확인: `sha256sum -c`로 SOURCE.md의 값과 일치. `.gitignore`가 이 파일들을 막지 않는지 `git check-ignore`.
- [ ] 커밋.

### Task 2: `faceOutline` 순수 함수

**Files:** Modify `lib.js`, `web_tests/lib.test.mjs`
**Produces:** `FACE_OVAL: number[]` (36개, 설계 4장 순서), `faceOutline(landmarks: {x,y,z}[], rect) -> [x, y][]`

- [ ] 실패하는 테스트: 478개 `{x: i/1000, y: i/2000, z: 0}` 입력 → 36점, 첫 점은 10번 점의 화면 좌표, 마지막은 109번; `rect` 변환; 478개 미만이면 `[]`; `FACE_OVAL` 길이 36·중복 없음·모두 0~477.
- [ ] RED 확인 → 구현 → GREEN (`node --test "web_tests/*.test.mjs"`).
- [ ] 커밋.

### Task 3: CSP 갱신과 위반 수집

**Files:** Modify `index.html`, `tools/e2e_browser.py`

- [ ] e2e 초기화 스크립트에 `securitypolicyviolation` 수집(`window.__csp`)과 콘솔 `error` 수집을 추가하고, 끝에서 둘 다 비어 있어야 통과하게 한다. 콘솔 수집에서 브라우저 자동 `favicon.ico` 404만 제외한다.
- [ ] e2e 실행 → 지금 CSP에서 위반이 없어 통과해야 한다 (수집기가 동작하는지는 Task 4 RED에서 드러남).
- [ ] `index.html` CSP를 Global Constraints 값으로 바꾼다. e2e 8단계 재확인.
- [ ] 커밋.

### Task 4: 얼굴 윤곽 기능 (`face.js`, `app.js`, 화면)

**Files:** Create `face.js`; Modify `app.js`, `index.html`, `style.css`, `tools/e2e_browser.py`
**Consumes:** `faceOutline`, `FACE_OVAL` (Task 2)
**Produces:** `face.js` — `createFaceTracker({ onUpdate, onError })` → `{ setEnabled(bool), setSource(videoOrNull), detectNow(canvas) -> outlines|null, enabled, ready }`

- [ ] **e2e 먼저 (RED)**: `zidane.jpg` 가짜 카메라 브라우저를 따로 띄워
  - 기본 끔: `#faces`가 `-`, `vendor/mediapipe` 요청 0건
  - 서버 없이(설정만, 서버 주소는 응답 없는 포트) 켬 → 실시간 표시 `#faces` 1 이상
  - 서버 있음 → `연결됨`, `#faces` 1 이상, 스크린샷; 동기 표시에서 `#faces` 1 이상, 스크린샷
  - 켬/끔 10번 빠르게 반복 후 끔 → `#faces` `-`, 오류 없음
  - 멀리 있는 얼굴 흉내(1/2, 1/3 크기 가운데 배치)에서 `#faces` 값 기록 (합격 기준 아님)
- [ ] `face.js` 구현: SIMD 확인 → 고정 경로 fileset → GPU로 생성, 실패 시 CPU; 불러오기 약속 하나·요청 번호·30초 시간 제한; 끄면 `close()`; 계산 예외 시 한 번 재생성; `requestVideoFrameCallback`(없으면 `currentTime` 비교) + 초당 15회 상한; 탭이 가려지면 멈춤.
- [ ] `app.js`: 설정 체크박스(`#face-toggle`, 기본 끔), `#faces` 표시·`resetStats`, `draw()` 순서 분리(윤곽은 뼈대 결과와 무관), 동기 표시 — `pump`에서 원본 크기 캔버스(`full`)에 그리고 거기서 640 캡처, 보낸 직후 `full`로 얼굴 계산해 `seq`와 보관, 결과 도착 시 함께 그림.
- [ ] `index.html`: 체크박스, `#faces` 통계. `style.css`: 체크박스 줄.
- [ ] GREEN: e2e 전 단계 + Node 테스트 + `node --check`. 스크린샷 확인.
- [ ] 커밋.

### Task 5: 문서와 배포

- [ ] README: 얼굴 윤곽 사용법(설정에서 켜기, 기본 끔, 시연용), 자동 테스트로 잰 "멀리 있는 얼굴" 결과, 수동 실측 항목.
- [ ] 기반 설계 6장에 한 줄, 개발 경위 5장 테스트 개수 갱신.
- [ ] 전체 검증: `pytest`, Node, e2e.
- [ ] 새 검토자 전체 검토 → 중요 이상 수정(각각 RED→GREEN).
- [ ] `main` push, Pages에 `vendor/mediapipe/*.wasm` 200 확인.
