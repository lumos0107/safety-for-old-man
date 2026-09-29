# 웹 카메라 자세 인식 시제품 구현 계획 검토

작성 2026-09-29 · 대상 문서 `design/2026-09-29-web-pose-plan.md`

## 1. 총평

계획대로 진행해도 된다. 작업 단위가 잘게 나뉘어 있고, 테스트 개수 같은 기대값도 대부분 맞다.

설계 검토에서 이미 받아들인 위험은 다시 다루지 않는다. CT 로그로 `*.ts.net` 주소가 공개되는 문제와 `lumos0107.github.io` 출처를 다른 저장소와 함께 쓰는 문제가 여기에 해당한다.

**2장은 구현 전에 반영한다.** 3장은 보안 보완, 4장은 계획서 정정, 5장은 선택이다.

## 2. 구현 전에 반영할 것

### 2.1 밀려난 연결(4009)이 인증에 성공해 활성 연결을 빼앗는 경쟁
위치: Task 5 `server/app.py` `ws_endpoint` (계획서 890~897행)

- 대기 연결을 `4009`로 닫아도, 그 연결의 처리 코드는 `authenticate()`에서 `ws.receive()`를 계속 기다린다.
- 닫히기 전에 도착한 인증 메시지가 이미 버퍼에 있으면 그 메시지로 인증에 성공한다. 이어서 `promote()`가 현재 활성 연결을 `4010`으로 닫는다.
- 프런트는 `4010`을 받으면 재연결하지 않고 멈춘다. 결과적으로 정상 사용 중인 기기가 멈춘다.
- 토큰이 있는 연결에서만 생기므로 빈도는 낮다. 한 줄로 막는다.

```python
if not await authenticate(ws) or ws not in registry.pending:
    return
```

- 테스트 추가: 대기 연결을 밀어낸 뒤 그 연결의 인증 메시지가 처리되어도 `registry.active`가 바뀌지 않는지 확인한다.

### 2.2 `run.ps1`이 Funnel 허용 안내를 숨긴다
위치: Task 7 `server/run.ps1` (1260행)

- tailnet에서 Funnel이나 HTTPS 인증서가 아직 허용되지 않았으면, `tailscale funnel`은 허용 링크를 출력하고 기다린다.
- 그런데 `| Out-Null`이 이 출력을 버리므로 스크립트가 멈춘 것처럼 보인다.
- 켜는 명령(`funnel --bg 8000`)에서는 `| Out-Null`을 뺀다. 끄는 명령에서는 그대로 둔다.

### 2.3 `test_over_ws_max_size_disconnects`가 불안정하다
위치: Task 7 `server/tests/test_integration.py` (1221~1226행)

- 서버는 2.2 MB 프레임의 헤더만 읽고 바로 연결을 닫는다. 그래서 클라이언트의 `ws.send()`에서 먼저 예외가 날 수 있다.
- 특히 Windows에서는 읽지 않은 데이터가 남은 채 닫혀 RST가 나기 쉽고, 이때 `ConnectionClosed`나 `ConnectionResetError`가 발생한다.
- `send`와 `recv`를 모두 예외 확인 블록 안에 넣는다.

```python
with pytest.raises((ConnectionClosed, OSError)):
    ws.send(b"\xff" * 2_200_000)
    ws.recv(timeout=10)
```

### 2.4 GitHub Pages 활성화 단계가 없다
위치: Task 11

- 저장소 Settings → Pages → Source `Deploy from a branch`, `main` / `(root)` 설정 단계를 Step 3 앞에 넣는다.
- 무료 계정이면 저장소가 public이어야 Pages를 쓸 수 있다.
- 이 설정 없이 push하면 Step 3의 `curl`은 `404`를 받는다.

### 2.5 Funnel 주소 유출 검사가 일부 형태만 잡는다
위치: Task 11 Step 2 (2409행)

- `tail[0-9a-f]{4,}\.ts\.net`은 `tailXXXX.ts.net` 형태의 tailnet 이름만 잡는다. `cat-crocodile.ts.net` 같은 이름을 쓰면 놓친다.
- 실제 호스트 문자열로 검사한다: `git log --all -p | grep -c "<실제 호스트>"`
- 주소는 CT 로그로 이미 공개된다. 따라서 이 검사는 보안 수단이 아니라 저장소 정리 목적이다.

## 3. 보안 보완

### 3.1 `localStorage` 키 이름에 접두사를 붙인다
위치: Task 9 `app.js`, DOM 계약 (1638행)

- `token`, `serverUrl`은 너무 일반적인 이름이다. 같은 출처를 쓰는 다른 Pages 프로젝트가 같은 키를 쓰면 서로 값을 덮어쓴다.
- 설계 검토는 다른 프로젝트가 값을 읽는 위험만 다뤘고, 덮어쓰는 충돌은 다루지 않았다.
- `pose.serverUrl`, `pose.token`으로 바꾼다.

### 3.2 `ws://`는 로컬 주소에만 허용한다
위치: Task 8 `normalizeServerUrl`

- 지금은 모든 호스트에 `http://`·`ws://`를 받는다.
- Pages(https)에서는 브라우저가 혼합 콘텐츠로 막는다. 그러나 로컬 http 페이지에서는 원격 서버로 토큰이 평문으로 전송될 수 있다.
- `ws:`(`http:` 입력 포함)는 `localhost`, `127.0.0.1`일 때만 허용하고, 나머지는 `null`을 돌려준다.
- 테스트 추가: `http://mypc.tailnet.ts.net` → `null`

### 3.3 대기 연결 폭주에 대한 설계 문장을 사실대로 고친다
위치: 설계 문서 58행 "토큰 없는 연결을 반복해 열어도 정상 사용자가 막히지 않는다"

- 이미 인증된 연결은 영향이 없다.
- 하지만 공격자가 LTE와 Funnel을 거치는 왕복 시간 동안 대기 연결을 4개 이상 열면, 재연결하는 정상 사용자가 인증 전에 계속 밀려난다. 와이파이↔LTE 전환 때가 여기에 해당한다.
- 시제품이므로 이 위험은 받아들인다. 다만 문장은 "인증된 연결은 영향 없음, 재연결은 방해받을 수 있음"으로 고친다.

### 3.4 추론 중 예상하지 못한 예외를 처리한다
위치: Task 6 `serve_frames`

- CUDA 메모리 부족 같은 예외가 나면 처리 코드가 끝나고 연결이 `1011`로 닫힌다. 그러면 클라이언트는 재연결을 반복한다.
- `except Exception`으로 받아 `{"type":"error","code":"server_error","seq":N}`로 답하고 연결을 유지한다.
- 로그에는 예외 종류만 남기고, 이미지 데이터는 남기지 않는다.

### 3.5 (선택) CSP meta 태그
위치: Task 9 `index.html`

토큰이 `localStorage`에 있으므로 보조 방어로 둔다. 지금 코드는 `textContent`만 쓰기 때문에 XSS 위험은 낮다.

```html
<meta http-equiv="Content-Security-Policy"
      content="default-src 'self'; connect-src wss: ws://localhost:* ws://127.0.0.1:*; img-src 'self' blob:; media-src 'self' blob:">
```

## 4. 계획서 정정

- **Task 6 Step 2 기대 결과**
  - "`test_health_and_no_docs`만 PASS"라고 되어 있지만, Task 5의 임시 `serve_frames`에서도 `test_binary_without_frame_is_bad_message`와 `test_bad_message_discards_pending_frame`이 통과한다.
  - 기대 결과를 "3개 PASS, 나머지 FAIL"로 고친다. 그대로 두면 계획을 따라 실행하는 쪽이 "실패 확인" 단계에서 혼란을 겪는다.
- **Task 1 `test_gen_token_writes_env_and_refuses_overwrite` 마지막 단언** (170행)
  - `.env`가 두 줄이라 이 비교는 항상 참이고, 토큰이 실제로 바뀌었는지는 확인하지 못한다.
  - 새 파일의 `TOKEN` 값이 이전 값과 다른지 비교한다.
- **Task 1 `server/requirements.txt`**
  - 코드가 직접 가져오는 `pillow`, `numpy`, `opencv-python`이 다른 패키지를 따라 설치되는 것에 기대고 있다. 명시적으로 넣는다.
  - 기술 스택에는 "ultralytics 8.4"라고 적었지만 버전을 고정하지 않았다. `ultralytics~=8.4` 등으로 고정하거나, 설치 후 `uv pip freeze`로 잠금 파일을 남긴다.

## 5. 프런트 사소한 점 (선택)

- 프레임 오류 안내(`#notice`)가 이후에 결과가 정상으로 와도 지워지지 않는다. `onResult`에서 `setNotice("")`를 호출한다.
- 노트북은 전면 카메라만 있어서, 기본값이 `facing: "environment"`여도 전면 카메라가 잡힌다. 그런데 반전 여부를 `state.facing`으로만 판단하므로 좌우 반전이 되지 않는다. `track.getSettings().facingMode`로 판단한다. 값이 없으면 `user`로 간주한다.
- 동기 표시에서 창 크기 변경이나 모드 전환 때 `draw()`가 다시 불리면, `capture`에 이미 다음 프레임이 그려져 있다. 그래서 새 영상 위에 이전 뼈대가 겹친다. 결과가 도착할 때 프레임을 별도 캔버스에 복사해 두면 된다.
