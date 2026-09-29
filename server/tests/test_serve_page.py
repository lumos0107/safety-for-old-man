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
