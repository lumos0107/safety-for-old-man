"""브라우저 종단 검증: 예시 사진을 가짜 카메라로 넣고 로컬 페이지 → 로컬 백엔드 전체를 확인한다.

사용: .venv\\Scripts\\python tools/e2e_browser.py [--shots 스크린샷폴더]
설치된 Chrome을 쓴다 (playwright 브라우저 내려받기 불필요). 스크린샷은 저장소 밖에 둔다.
"""
import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import cv2
from playwright.sync_api import expect, sync_playwright
from ultralytics.utils import ASSETS

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "e" * 43
PAGE_PORT, API_PORT, BLACKHOLE_PORT = 5500, 8765, 8766
PAGE = f"http://localhost:{PAGE_PORT}/"
# 인원 3명 이상. 페이지 CSP가 문자열 평가(wait_for_function)를 막으므로 텍스트 정규식으로 기다린다
AT_LEAST_3 = re.compile(r"^(?:[3-9]|[1-9]\d+)$")


def write_face_y4m(dst: Path, scale: float = 1.0, frames: int = 30) -> None:
    """가로 1280x720 가짜 카메라. zidane.jpg 오른쪽 사람을 2배로 확대해 얼굴이 화면 폭의 약 1/5이 되게 한다 (폰을 가까이 든 상황).
    원본 zidane.jpg(얼굴이 폭의 약 1/10)는 얼굴 탐지기(가까운 거리용)가 잡지 못한다 — 2026-09-30 실측.
    scale<1이면 줄여서 검은 바탕 가운데에 둔다 (멀리 있는 얼굴 흉내). scale=0.5가 원본 사진의 얼굴 크기와 같다."""
    w, h = 1280, 720
    img = cv2.resize(cv2.imread(str(ASSETS / "zidane.jpg"))[0:360, 640:1280], (w, h))
    if scale < 1:
        small = cv2.resize(img, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
        img = img * 0
        y, x = (h - small.shape[0]) // 2, (w - small.shape[1]) // 2
        img[y:y + small.shape[0], x:x + small.shape[1]] = small
    yuv = cv2.cvtColor(img, cv2.COLOR_BGR2YUV_I420).tobytes()
    with open(dst, "wb") as f:
        f.write(f"YUV4MPEG2 W{w} H{h} F30:1 Ip A1:1 C420jpeg\n".encode())
        for _ in range(frames):
            f.write(b"FRAME\n" + yuv)


def write_y4m(dst: Path, w: int = 480, h: int = 640, frames: int = 30) -> None:
    """세로(480x640) 가짜 카메라 영상. 폰을 세운 상황을 흉내낸다."""
    img = cv2.resize(cv2.imread(str(ASSETS / "bus.jpg")), (w, h))
    yuv = cv2.cvtColor(img, cv2.COLOR_BGR2YUV_I420).tobytes()
    with open(dst, "wb") as f:
        f.write(f"YUV4MPEG2 W{w} H{h} F30:1 Ip A1:1 C420jpeg\n".encode())
        for _ in range(frames):
            f.write(b"FRAME\n" + yuv)


def start_api() -> subprocess.Popen:
    env = {**os.environ, "TOKEN": TOKEN, "ALLOWED_ORIGINS": f"http://localhost:{PAGE_PORT}"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "--factory", "server.app:build_app", "--host", "127.0.0.1",
         "--port", str(API_PORT), "--ws-max-size", "2097152", "--log-level", "warning"],
        cwd=ROOT, env=env)
    deadline = time.time() + 120
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{API_PORT}/health", timeout=1)
            return proc
        except OSError:
            time.sleep(0.5)
    proc.kill()
    raise SystemExit("백엔드가 뜨지 않음")


def stop_proc(proc: subprocess.Popen) -> None:
    proc.terminate()
    proc.wait(timeout=15)


def step(name: str) -> None:
    print(f"OK  {name}", flush=True)


def people(page) -> int:
    text = page.locator("#people").text_content() or "-"
    return int(text) if text.isdigit() else -1


def configure(page, token: str, port: int = API_PORT) -> None:
    page.click("#settings-btn")
    page.fill("#server-url", f"http://127.0.0.1:{port}")
    page.fill("#token", token)
    page.click("#settings button[value=save]")


# 페이지가 만든 WebSocket과 카메라 스트림을 센다 (CDP로 주입되므로 페이지 CSP와 무관)
PROBE = """
window.__csp = [];
document.addEventListener("securitypolicyviolation",
  (e) => window.__csp.push(e.violatedDirective + " " + e.blockedURI));
window.__sent = [];
const nativeSend = WebSocket.prototype.send;
WebSocket.prototype.send = function (data) {
  if (typeof data === "string") window.__sent.push(data);
  return nativeSend.call(this, data);
};
window.__sockets = [];
const NativeWS = window.WebSocket;
window.WebSocket = class extends NativeWS {
  constructor(...args) { super(...args); window.__sockets.push(this); }
};
window.__streams = [];
const md = navigator.mediaDevices;
const gum = md.getUserMedia.bind(md);
md.getUserMedia = async (c) => {
  const s = await gum(c);
  window.__streams.push(s);
  if (window.__endNextTrack) {  // 시작하는 도중 카메라를 빼앗긴 상황 흉내
    window.__endNextTrack = false;
    s.getVideoTracks().forEach((t) => t.stop());
  }
  return s;
};
"""
# 요청이 1초 늦게 끝나는 가짜 Wake Lock — 잡힌 잠금 수를 센다
FAKE_WAKE_LOCK = """
window.__locksActive = 0;
Object.defineProperty(navigator, "wakeLock", { configurable: true, value: {
  request: () => new Promise((resolve) => setTimeout(() => {
    window.__locksActive++;
    resolve({ release: async () => { window.__locksActive--; } });
  }, 1000)),
}});
"""
# 타이머를 20배 빠르게 — 재시도 상한(약 3분)을 몇 초 만에 확인
FAST_TIMERS = """
const nativeSetTimeout = window.setTimeout;
window.setTimeout = (fn, ms, ...args) => nativeSetTimeout(fn, (ms || 0) / 20, ...args);
"""
LIVE_TRACKS = "() => window.__streams.flatMap(s => s.getTracks()).filter(t => t.readyState === 'live').length"


def watch(page, errors: list) -> None:
    """페이지 오류와 콘솔 오류를 모은다. 예상된 것(자동 favicon 요청, 서버를 일부러 끈 뒤의 WebSocket 실패)은 뺀다."""
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

    def on_console(msg):
        if msg.type != "error":
            return
        if "favicon.ico" in (msg.location or {}).get("url", "") or msg.text.startswith("WebSocket connection to"):
            return
        if msg.text.startswith("INFO: Created TensorFlow Lite XNNPACK delegate"):  # MediaPipe가 정보 메시지를 error로 찍음
            return
        if TELEMETRY in msg.text:  # 아래 csp_violations 설명 참고 — 따로 확인한다
            return
        errors.append(f"console: {msg.text}")
    page.on("console", on_console)


# MediaPipe는 모델을 만들 때 사용 통계 기록기를 붙이고 Google로 보내려 한다 (끄는 옵션 없음).
# 페이지 CSP가 이를 막는 것이 의도된 동작이다 — 얼굴 기능 설계 4장. 이 차단만 예상된 위반으로 분리한다.
TELEMETRY = "odml.pa.googleapis.com"


def assert_no_face_data_sent(page) -> None:
    """서버로 간 텍스트 메시지는 auth와 {type:frame, seq}뿐이어야 한다 — 얼굴 좌표·개수가 섞이면 실패."""
    for raw in page.evaluate("() => window.__sent"):
        msg = json.loads(raw)
        ok = ((msg.get("type") == "auth" and set(msg) == {"type", "token"})
              or (msg.get("type") == "frame" and set(msg) == {"type", "seq"}))
        assert ok, f"서버로 예상 밖 메시지: {raw[:120]}"


def csp_violations(page) -> list:
    """예상된 텔레메트리 차단을 뺀 CSP 위반."""
    return [v for v in page.evaluate("() => window.__csp") if TELEMETRY not in v]


def telemetry_blocked(page) -> bool:
    return any(TELEMETRY in v for v in page.evaluate("() => window.__csp"))


FACES_AT_LEAST_1 = re.compile(r"^[1-9]\d*$")


def launch(p, y4m: Path):
    return p.chromium.launch(channel="chrome", headless=True, args=[
        "--use-fake-ui-for-media-stream",
        "--use-fake-device-for-media-stream",
        f"--use-file-for-fake-video-capture={y4m}",
    ])


def set_face(page, on: bool, port: int) -> None:
    page.click("#settings-btn")
    page.fill("#server-url", f"http://127.0.0.1:{port}")
    page.fill("#token", TOKEN)
    if page.is_checked("#face-toggle") != on:
        page.click("#face-toggle")
    page.click("#settings button[value=save]")


def face_checks(p, tmp: Path, shots: Path) -> None:
    """얼굴 윤곽 (design/2026-09-30-face-outline-design.md 7장)."""
    y4m = tmp / "face.y4m"
    write_face_y4m(y4m)
    browser = launch(p, y4m)
    context = browser.new_context(viewport={"width": 390, "height": 844})
    context.add_init_script(PROBE)
    page = context.new_page()
    errors: list = []
    watch(page, errors)
    vendor = []
    page.on("request", lambda r: "vendor/mediapipe" in r.url and vendor.append(r.url))
    status, faces = page.locator("#status"), page.locator("#faces")
    page.goto(PAGE)

    hole = blackhole(BLACKHOLE_PORT)
    try:
        page.click("#settings-btn")
        assert not page.is_checked("#face-toggle"), "얼굴 윤곽 기본값이 켬"
        page.click("#settings-cancel")
        set_face(page, False, BLACKHOLE_PORT)
        page.click("#start")
        page.wait_for_timeout(3000)
        expect(faces).to_have_text("-")
        assert not vendor, f"끈 상태에서 MediaPipe 파일 요청: {vendor}"
        step("얼굴 윤곽 기본 끔, 끈 상태에서는 모델 파일을 받지 않음")

        set_face(page, True, BLACKHOLE_PORT)
        expect(faces).to_have_text(FACES_AT_LEAST_1, timeout=90_000)
        expect(status).not_to_have_text("연결됨")
        step(f"서버 연결 없이도 실시간 표시에 얼굴 윤곽 ({faces.text_content()}개)")
    finally:
        hole.close()

    page.click("#start")  # 정지
    set_face(page, True, API_PORT)
    page.click("#start")
    expect(status).to_have_text("연결됨", timeout=30_000)
    expect(faces).to_have_text(FACES_AT_LEAST_1, timeout=60_000)
    page.wait_for_timeout(1000)
    page.screenshot(path=str(shots / "3_face_live.png"))
    expect(page.locator("#face-notice")).to_have_text("")
    step(f"서버 연결 상태에서 뼈대와 얼굴 윤곽 ({faces.text_content()}개)")

    page.click("#mode")
    expect(page.locator("#mode")).to_have_text("표시: 동기")
    page.wait_for_timeout(1500)
    expect(faces).to_have_text(FACES_AT_LEAST_1, timeout=30_000)
    page.screenshot(path=str(shots / "4_face_sync.png"))
    page.click("#mode")
    step("동기 표시에서도 얼굴 윤곽")

    page.evaluate("""() => {
      const t = document.getElementById('face-toggle');
      const f = document.getElementById('settings-form');
      const save = document.querySelector('#settings button[value=save]');
      for (let i = 0; i < 11; i++) {
        document.getElementById('settings').showModal();
        t.checked = !t.checked;
        f.requestSubmit(save);
      }
    }""")
    page.wait_for_timeout(3000)
    expect(faces).to_have_text("-")
    set_face(page, True, API_PORT)
    expect(faces).to_have_text(FACES_AT_LEAST_1, timeout=60_000)
    set_face(page, False, API_PORT)
    expect(faces).to_have_text("-", timeout=10_000)
    step("켜기·끄기를 빠르게 반복해도 마지막 상태를 따르고, 다시 켜면 동작")

    # 서버에 연결된 상태에서 모델 불러오기가 실패해도 안내문이 남아야 한다 (프레임 결과가 지우지 않음)
    ctx2 = browser.new_context(viewport={"width": 390, "height": 844})  # 캐시가 따로인 새 컨텍스트
    ctx2.add_init_script(PROBE)
    failing = ctx2.new_page()
    failing_errors: list = []
    watch(failing, failing_errors)
    failing.route("**/face_landmarker.task", lambda route: route.abort())
    failing.goto(PAGE)
    set_face(failing, True, API_PORT)
    failing.click("#start")
    expect(failing.locator("#status")).to_have_text("연결됨", timeout=30_000)
    failing.click("#settings-btn")  # 설정 창을 연 채로 실패를 맞는다
    expect(failing.locator("#face-notice")).to_contain_text("불러오지 못해", timeout=60_000)
    assert not failing.is_checked("#face-toggle"), "열린 설정 창의 체크박스가 켬으로 남음"
    failing.click("#settings-cancel")
    failing.wait_for_timeout(3000)
    expect(failing.locator("#face-notice")).to_contain_text("불러오지 못해")
    expect(failing.locator("#faces")).to_have_text("-")
    failing.click("#settings-btn")
    assert not failing.is_checked("#face-toggle"), "실패 뒤 설정이 켬으로 남음"
    failing.click("#settings-cancel")
    # 일부러 막은 모델 요청의 실패(net::ERR_FAILED)만 예상된 오류다
    errors += [e for e in failing_errors if "net::ERR_FAILED" not in e]
    errors += [f"CSP(실패 경로): {v}" for v in csp_violations(failing)]
    ctx2.close()
    step("서버 연결 중 얼굴 모델 불러오기 실패 → 안내문이 남고 설정은 끔으로 돌아감")

    errors += [f"CSP: {v}" for v in csp_violations(page)]
    assert_no_face_data_sent(page)
    if errors:
        raise SystemExit(f"얼굴 윤곽 페이지 오류: {errors}")
    step("얼굴 윤곽: 서버로 간 메시지에 얼굴 정보 없음, 페이지 오류·콘솔 오류·CSP 위반 없음"
         + (" (MediaPipe 사용 통계 전송은 CSP가 차단함)" if telemetry_blocked(page) else ""))
    browser.close()

    record = []
    for scale in (0.5, 1 / 3):
        far = tmp / f"face_{scale:.2f}.y4m"
        write_face_y4m(far, scale)
        b = launch(p, far)
        pg = b.new_context(viewport={"width": 390, "height": 844})
        pg.add_init_script(PROBE)
        page = pg.new_page()
        far_errors: list = []
        watch(page, far_errors)
        page.goto(PAGE)
        set_face(page, True, API_PORT)
        page.click("#start")
        try:
            expect(page.locator("#faces")).to_have_text(FACES_AT_LEAST_1, timeout=30_000)
        except AssertionError:
            pass
        found = page.locator("#faces").text_content()
        assert found != "-", "먼 얼굴 페이지에서 얼굴 모델이 뜨지 않음"
        if found == "0":  # 못 잡으면 가까이 대라는 안내가 떠야 한다
            expect(page.locator("#face-notice")).to_contain_text("가까이", timeout=10_000)
        far_errors += [f"CSP: {v}" for v in csp_violations(page)]
        if far_errors:
            raise SystemExit(f"먼 얼굴 페이지 오류: {far_errors}")
        record.append(f"{scale:.2f}배 → 얼굴 {found}")
        b.close()
    print("기록  멀리 있는 얼굴 흉내 (1280x720 가운데 배치): " + ", ".join(record), flush=True)


def blackhole(port: int) -> socket.socket:
    """TCP 연결은 받지만 WebSocket 핸드셰이크에 영원히 답하지 않는 서버 (응답 없는 네트워크 흉내)."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", port))
    srv.listen()
    held = []

    def accept():
        while True:
            try:
                held.append(srv.accept()[0])
            except OSError:
                return
    threading.Thread(target=accept, daemon=True).start()
    return srv


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", type=Path, default=Path(tempfile.gettempdir()) / "e2e_shots")
    args = ap.parse_args()
    args.shots.mkdir(parents=True, exist_ok=True)

    tmp = Path(tempfile.mkdtemp())
    y4m = tmp / "cam.y4m"
    write_y4m(y4m)
    # 화면 파일만 내주는 서버 — http.server는 저장소 루트를 통째로 내줘 server/.env(실제 토큰)가 열린다
    web = subprocess.Popen([sys.executable, str(ROOT / "tools" / "serve_page.py"), str(PAGE_PORT)],
                           cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    api = start_api()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True, args=[
                "--use-fake-ui-for-media-stream",
                "--use-fake-device-for-media-stream",
                f"--use-file-for-fake-video-capture={y4m}",
            ])
            context = browser.new_context(viewport={"width": 390, "height": 844})
            context.add_init_script(PROBE)
            page = context.new_page()
            errors = []
            watch(page, errors)
            page.goto(PAGE)
            status = page.locator("#status")
            expect(status).to_have_text("설정 필요")
            expect(page.locator("#privacy")).to_contain_text("서버")  # 영상이 서버로 간다는 고지
            step("설정 없으면 '설정 필요', 첫 화면에 영상 전송 고지")

            page.click("#settings-btn")
            size = page.evaluate("() => getComputedStyle(document.getElementById('server-url')).fontSize")
            assert size == "16px", f"입력칸 글자 {size} — 아이폰이 화면을 확대한다"
            expect(page.locator("#token")).to_have_attribute("type", "password")
            page.click("#token-show")
            expect(page.locator("#token")).to_have_attribute("type", "text")
            page.click("#token-show")
            expect(page.locator("#token")).to_have_attribute("type", "password")
            page.click("#settings-cancel")
            step("설정 입력칸 16px(아이폰 확대 방지), 토큰 보기 전환")

            # 폰 키보드의 "이동"처럼 토큰 칸에서 Enter로 저장한다. 앞뒤 공백이 붙은 토큰.
            page.click("#settings-btn")
            page.fill("#server-url", f"http://127.0.0.1:{API_PORT}")
            page.fill("#token", f"  {TOKEN}  ")
            page.press("#token", "Enter")
            expect(page.locator("#settings")).not_to_have_attribute("open", "")
            page.click("#start")
            expect(status).to_have_text("연결됨", timeout=30_000)
            expect(page.locator("#people")).to_have_text(AT_LEAST_3, timeout=30_000)
            page.screenshot(path=str(args.shots / "1_live.png"))
            step(f"Enter로 저장한 공백 붙은 토큰으로 연결, 세로 영상에서 {people(page)}명 인식")

            page.click("#mode")
            page.wait_for_timeout(1000)
            page.screenshot(path=str(args.shots / "2_sync.png"))
            expect(page.locator(".hint")).not_to_contain_text("'동기'로 바꾸세요")
            expect(page.locator("#stage")).to_have_class(re.compile(r"\bsync\b"))
            page.click("#mode")
            step("동기 표시 전환")

            page.click("#start")  # 정지
            expect(status).to_have_text("정지")
            before = page.evaluate("() => window.__sockets.length")
            page.evaluate("() => { const b = document.getElementById('start'); b.click(); b.click(); }")
            expect(status).to_have_text("연결됨", timeout=30_000)
            page.evaluate("() => { const b = document.getElementById('flip'); b.click(); b.click(); }")
            page.wait_for_timeout(5000)
            expect(status).to_have_text("연결됨")
            assert page.evaluate("() => window.__sockets.length") - before == 1, "시작 두 번에 소켓이 여러 개"
            assert page.evaluate(LIVE_TRACKS) == 1, f"살아 있는 카메라 트랙 {page.evaluate(LIVE_TRACKS)}개"
            step("시작·카메라 전환을 빠르게 두 번 눌러도 소켓 1개, 카메라 트랙 1개, 연결 유지")

            page.evaluate("() => { document.getElementById('flip').click(); document.getElementById('start').click(); }")
            expect(status).to_have_text("정지")
            page.wait_for_timeout(2000)
            assert page.evaluate(LIVE_TRACKS) == 0, f"정지했는데 카메라 트랙 {page.evaluate(LIVE_TRACKS)}개 살아 있음"
            assert page.evaluate("() => !document.getElementById('video').srcObject"), "정지했는데 영상이 붙어 있음"
            page.click("#start")
            expect(status).to_have_text("연결됨", timeout=30_000)
            step("카메라 전환 직후 정지해도 카메라가 꺼짐")

            page.evaluate("""() => {
              const track = window.__streams.at(-1).getVideoTracks()[0];
              track.stop();
              track.dispatchEvent(new Event('ended'));
            }""")
            expect(status).to_have_text("정지", timeout=5_000)
            expect(page.locator("#notice")).to_contain_text("카메라가 꺼졌")
            assert page.evaluate(LIVE_TRACKS) == 0
            page.click("#start")
            expect(status).to_have_text("연결됨", timeout=30_000)
            step("연결 중 카메라가 끊기면 '연결됨'으로 빈 프레임을 보내지 않고 멈춤·안내")

            page.click("#start")  # 정지
            expect(status).to_have_text("정지")
            page.evaluate("() => { window.__endNextTrack = true; }")
            page.click("#start")
            expect(page.locator("#notice")).to_contain_text("카메라가 꺼졌", timeout=10_000)
            page.wait_for_timeout(2000)
            expect(status).not_to_have_text("연결됨")
            assert page.evaluate(LIVE_TRACKS) == 0
            page.click("#start")
            expect(status).to_have_text("연결됨", timeout=30_000)
            step("시작하는 도중 카메라를 빼앗겨도 '연결됨'으로 빈 프레임을 보내지 않음")

            page2 = context.new_page()
            watch(page2, errors)
            page2.goto(PAGE)
            page2.click("#start")
            expect(page2.locator("#status")).to_have_text("연결됨", timeout=30_000)
            expect(status).to_have_text("다른 기기에서 사용 중", timeout=10_000)
            page.wait_for_timeout(5000)
            expect(status).to_have_text("다른 기기에서 사용 중")
            expect(page2.locator("#status")).to_have_text("연결됨")
            expect(page.locator("#start")).to_have_text("시작")
            page2.close()
            step("다른 탭이 가져가면 '다른 기기에서 사용 중', 5초간 서로 뺏지 않음")

            page.click("#start")
            expect(status).to_have_text("연결됨", timeout=30_000)
            stop_proc(api)
            expect(status).to_have_text("서버 꺼짐", timeout=15_000)
            api = start_api()
            expect(status).to_have_text("연결됨", timeout=60_000)
            expect(page.locator("#people")).to_have_text(AT_LEAST_3, timeout=30_000)
            step("서버가 죽었다 살아나면 새로고침 없이 다시 연결")

            configure(page, "wrong-token")
            expect(status).to_have_text("토큰 확인", timeout=15_000)
            page.wait_for_timeout(5000)
            expect(status).to_have_text("토큰 확인")
            expect(page.locator("#start")).to_have_text("시작")
            step("틀린 토큰이면 '토큰 확인', 재연결하지 않음")

            configure(page, TOKEN)  # 멈춘 상태에서 설정을 고쳐 저장
            expect(status).to_have_text("대기")
            expect(page.locator("#notice")).to_have_text("")
            step("멈춘 뒤 설정을 고쳐 저장하면 이전 안내가 지워짐")

            hole = blackhole(BLACKHOLE_PORT)
            try:
                configure(page, TOKEN, port=BLACKHOLE_PORT)
                page.click("#start")
                expect(status).to_have_text("연결 중")
                expect(status).to_have_text("서버 꺼짐", timeout=15_000)
                expect(page.locator("#notice")).to_contain_text("주소")  # 한 번도 연결된 적 없으면 주소 확인 안내
            finally:
                hole.close()
            page.click("#start")  # 정지
            step("응답 없는 서버면 '연결 중'에 머물지 않고 '서버 꺼짐'으로 재시도")

            errors += [f"CSP: {v}" for v in csp_violations(page)]
            if errors:
                raise SystemExit(f"페이지 오류: {errors}")
            step("페이지 오류·콘솔 오류·CSP 위반 없음")

            other = context.new_page()  # 서버 허용 목록에 없는 주소(127.0.0.1)로 연 페이지
            other.goto(f"http://127.0.0.1:{PAGE_PORT}/")
            configure(other, TOKEN)
            other.click("#start")
            expect(other.locator("#status")).to_have_text("허용되지 않은 주소", timeout=15_000)
            expect(other.locator("#notice")).to_contain_text("ALLOWED_ORIGINS")
            other.close()
            step("허용되지 않은 주소면 원인(ALLOWED_ORIGINS) 안내")

            locks = browser.new_context(viewport={"width": 390, "height": 844})
            locks.add_init_script(PROBE)
            locks.add_init_script(FAKE_WAKE_LOCK)
            lpage = locks.new_page()
            lpage.goto(PAGE)
            configure(lpage, TOKEN)
            lpage.click("#start")
            expect(lpage.locator("#start")).to_have_text("정지")
            lpage.click("#start")  # 화면 꺼짐 방지 요청이 끝나기 전에 정지
            lpage.wait_for_timeout(2000)
            held = lpage.evaluate("() => window.__locksActive")
            locks.close()
            assert held == 0, f"정지했는데 화면 꺼짐 방지 {held}개가 남음"
            step("시작 직후 정지해도 화면 꺼짐 방지가 남지 않음")

            fast = browser.new_context(viewport={"width": 390, "height": 844})
            fast.add_init_script(PROBE)
            fast.add_init_script(FAST_TIMERS)
            fpage = fast.new_page()
            fpage.goto(PAGE)
            hole = blackhole(BLACKHOLE_PORT)
            try:
                configure(fpage, TOKEN, port=BLACKHOLE_PORT)
                fpage.click("#start")
                expect(fpage.locator("#notice")).to_contain_text("멈췄습니다", timeout=60_000)
                expect(fpage.locator("#status")).to_have_attribute("data-kind", "bad")
                expect(fpage.locator("#start")).to_have_text("시작")
                assert fpage.evaluate(LIVE_TRACKS) == 0
                sockets = fpage.evaluate("() => window.__sockets.length")
            finally:
                hole.close()
                fast.close()
            step(f"서버가 계속 없으면 {sockets}번 시도 뒤 멈추고 카메라를 끔 (타이머 20배속)")

            wide = browser.new_context(viewport={"width": 844, "height": 390})
            wide.add_init_script(PROBE)
            wpage = wide.new_page()
            wpage.goto(PAGE)
            configure(wpage, TOKEN)
            wpage.click("#start")
            expect(wpage.locator("#status")).to_have_text("연결됨", timeout=30_000)
            wpage.wait_for_timeout(1500)
            wpage.screenshot(path=str(args.shots / "5_landscape.png"))
            stage_h = wpage.evaluate("() => document.getElementById('stage').clientHeight")
            expect(wpage.locator("#privacy")).to_be_visible()  # 가로 화면에서도 영상 전송 고지는 보여야 한다
            wide.close()
            print(f"기록  가로 844x390에서 영상 영역 높이 {stage_h}px", flush=True)
            browser.close()

            face_checks(p, tmp, args.shots)
    finally:
        for proc in (api, web):
            if proc.poll() is None:
                stop_proc(proc)
    print(f"전체 통과. 스크린샷: {args.shots}")


if __name__ == "__main__":
    main()
