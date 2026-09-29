"""브라우저 종단 검증: 예시 사진을 가짜 카메라로 넣고 로컬 페이지 → 로컬 백엔드 전체를 확인한다.

사용: .venv\\Scripts\\python tools/e2e_browser.py [--shots 스크린샷폴더]
설치된 Chrome을 쓴다 (playwright 브라우저 내려받기 불필요). 스크린샷은 저장소 밖에 둔다.
"""
import argparse
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
window.__sockets = [];
const NativeWS = window.WebSocket;
window.WebSocket = class extends NativeWS {
  constructor(...args) { super(...args); window.__sockets.push(this); }
};
window.__streams = [];
const md = navigator.mediaDevices;
const gum = md.getUserMedia.bind(md);
md.getUserMedia = async (c) => { const s = await gum(c); window.__streams.push(s); return s; };
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

    errors += [f"CSP: {v}" for v in csp_violations(page)]
    if errors:
        raise SystemExit(f"얼굴 윤곽 페이지 오류: {errors}")
    step("얼굴 윤곽: 페이지 오류·콘솔 오류·CSP 위반 없음"
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
        page.goto(PAGE)
        set_face(page, True, API_PORT)
        page.click("#start")
        try:
            expect(page.locator("#faces")).to_have_text(FACES_AT_LEAST_1, timeout=30_000)
        except AssertionError:
            pass
        record.append(f"{scale:.2f}배 → 얼굴 {page.locator('#faces').text_content()}")
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
    web = subprocess.Popen([sys.executable, "-m", "http.server", str(PAGE_PORT), "--bind", "127.0.0.1"],
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
            step("설정 없으면 '설정 필요'")

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

            hole = blackhole(BLACKHOLE_PORT)
            try:
                configure(page, TOKEN, port=BLACKHOLE_PORT)
                page.click("#start")
                expect(status).to_have_text("연결 중")
                expect(status).to_have_text("서버 꺼짐", timeout=15_000)
            finally:
                hole.close()
            page.click("#start")  # 정지
            step("응답 없는 서버면 '연결 중'에 머물지 않고 '서버 꺼짐'으로 재시도")

            errors += [f"CSP: {v}" for v in csp_violations(page)]
            if errors:
                raise SystemExit(f"페이지 오류: {errors}")
            step("페이지 오류·콘솔 오류·CSP 위반 없음")
            browser.close()

            face_checks(p, tmp, args.shots)
    finally:
        for proc in (api, web):
            if proc.poll() is None:
                stop_proc(proc)
    print(f"전체 통과. 스크린샷: {args.shots}")


if __name__ == "__main__":
    main()
