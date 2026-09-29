"""tools/serve_page.py — 로컬 확인용 정적 서버가 비밀 파일을 내주지 않는지."""
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def page_server():
    port = free_port()
    proc = subprocess.Popen([sys.executable, str(ROOT / "tools" / "serve_page.py"), str(port)], cwd=ROOT,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            urllib.request.urlopen(base + "/index.html", timeout=1)
            break
        except OSError:
            time.sleep(0.1)
    yield base
    proc.terminate()
    proc.wait(timeout=10)


def status(url: str) -> int:
    try:
        return urllib.request.urlopen(url, timeout=3).status
    except urllib.error.HTTPError as e:
        return e.code


@pytest.mark.parametrize("path", ["/", "/index.html", "/app.js", "/lib.js", "/face.js", "/style.css",
                                  "/vendor/mediapipe/vision_bundle.mjs"])
def test_page_files_served(page_server, path):
    assert status(page_server + path) == 200


@pytest.mark.parametrize("path", ["/server/.env", "/server/.env.example", "/server/app.py", "/tools/gen_token.py",
                                  "/.git/config", "/.gitignore", "/.venv/pyvenv.cfg", "/server/%2e%2e/server/.env",
                                  "/%2e%2e/%2e%2e/Windows/win.ini", "/vendor/../server/.env"])
def test_secret_and_repo_files_refused(page_server, path):
    assert status(page_server + path) == 404


def raw_status(base: str, target: str) -> int:
    """urllib이 만들 수 없는 요청 대상(절대형 등)을 원시 소켓으로 보낸다."""
    host, port = base.split("//")[1].split(":")
    with socket.create_connection((host, int(port)), timeout=3) as s:
        s.sendall(f"GET {target} HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n".encode("ascii"))
        head = s.recv(64)
    return int(head.split()[1])


@pytest.mark.parametrize("target", ["x://server%2f.env", "X://server%2f.env", "a://.git%2fconfig", "x://server/",
                                    "server/.env", "http://x/server/.env", "x:/server/.env"])
def test_absolute_form_targets_refused(page_server, target):
    # 한 글자 스킴은 Windows에서 드라이브로 읽혀 허용 목록 검사를 건너뛴 적이 있다 (합의 R2-2)
    assert raw_status(page_server, target) in (400, 404)
