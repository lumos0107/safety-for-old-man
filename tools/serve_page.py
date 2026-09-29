"""로컬 확인용 정적 서버: 화면 파일만 내주고 나머지(server/.env 토큰, 저장소 파일)는 404. 127.0.0.1에만 연다.

`python -m http.server`는 저장소 루트를 통째로 내줘 http://localhost:5500/server/.env 로 실제 토큰이 열린다.
화면을 로컬에서 확인할 때(README '화면을 고칠 때')와 종단 검증(tools/e2e_browser.py)은 이 서버를 쓴다.

사용: .venv\\Scripts\\python tools/serve_page.py [포트, 기본 5500]
"""
import sys
import urllib.parse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# 허용 목록 방식: 페이지가 실제로 쓰는 것만 내준다
PAGE_ENTRIES = {"index.html", "app.js", "lib.js", "face.js", "style.css", "vendor"}


class PageHandler(SimpleHTTPRequestHandler):
    def send_head(self):
        raw = urllib.parse.urlsplit(self.path).path
        segments = urllib.parse.unquote(raw).split("/")
        parts = [p for p in segments if p]
        refused = (
            ".." in segments or "\\" in raw or "%5c" in raw.lower()
            or any(p.startswith(".") for p in parts)
            or (parts and parts[0] not in PAGE_ENTRIES)
        )
        if refused:
            self.send_error(404)
            return None
        return super().send_head()

    def log_message(self, *args):  # 요청 로그는 남기지 않는다
        pass


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5500
    server = ThreadingHTTPServer(("127.0.0.1", port), partial(PageHandler, directory=str(ROOT)))
    print(f"화면 파일만 제공: http://localhost:{port}/ (Ctrl+C로 종료)", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
