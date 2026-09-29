"""접속 토큰을 만들어 server/.env에 쓴다. 출력된 토큰을 브라우저 설정에 넣는다.

사용: .venv\\Scripts\\python tools/gen_token.py [--force] [--env 경로]
"""
import argparse
import secrets
import sys
from pathlib import Path

DEFAULT_ENV = Path(__file__).resolve().parents[1] / "server" / ".env"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", type=Path, default=DEFAULT_ENV)
    ap.add_argument("--force", action="store_true", help="기존 .env의 TOKEN만 새로 바꾼다 (다른 줄은 그대로)")
    args = ap.parse_args()

    if args.env.exists() and not args.force:
        print(f"{args.env}가 이미 있습니다. 토큰을 바꾸려면 --force", file=sys.stderr)
        return 1
    token_line = f"TOKEN={secrets.token_urlsafe(32)}"
    if args.env.exists():
        # TOKEN 줄만 바꾸고 주석·허용 주소·MODEL 같은 다른 줄은 형식까지 그대로 둔다
        lines = args.env.read_text(encoding="utf-8").splitlines()
        at = next((i for i, line in enumerate(lines) if line.split("=", 1)[0].strip() == "TOKEN"), None)
        if at is None:
            lines.insert(0, token_line)
        else:
            lines[at] = token_line
    else:
        lines = [token_line, "ALLOWED_ORIGINS=https://lumos0107.github.io"]
    args.env.write_text("\n".join(lines) + "\n", encoding="utf-8")
    token = token_line.split("=", 1)[1]
    print(f"{args.env} 작성 완료. 브라우저 설정에 넣을 토큰:")
    print(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
