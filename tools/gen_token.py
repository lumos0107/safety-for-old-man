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
    ap.add_argument("--force", action="store_true", help="기존 .env를 덮어쓴다 (토큰 교체)")
    args = ap.parse_args()

    if args.env.exists() and not args.force:
        print(f"{args.env}가 이미 있습니다. 토큰을 바꾸려면 --force", file=sys.stderr)
        return 1
    token = secrets.token_urlsafe(32)
    args.env.write_text(f"TOKEN={token}\nALLOWED_ORIGINS=https://lumos0107.github.io\n", encoding="utf-8")
    print(f"{args.env} 작성 완료. 브라우저 설정에 넣을 토큰:")
    print(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
