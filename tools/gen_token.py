"""접속 토큰을 만들어 server/.env에 쓴다. 출력된 토큰을 브라우저 설정에 넣는다.

사용: .venv\\Scripts\\python tools/gen_token.py [--force] [--env 경로]
"""
import argparse
import io
import os
import secrets
import sys
from pathlib import Path

from dotenv import dotenv_values

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
    text = "\n".join(lines) + "\n"
    token = token_line.split("=", 1)[1]
    # 쓰기 전에, 서버가 이 파일을 읽었을 때 실제로 새 토큰이 적용되는지 확인한다 (합의 R2-3):
    # TOKEN 줄이 여러 개면 마지막 줄이, 'export TOKEN='도 TOKEN으로 읽혀 옛 토큰이 계속 유효할 수 있다
    if dotenv_values(stream=io.StringIO(text)).get("TOKEN") != token:
        print(f"토큰이 적용되지 않아 {args.env}를 바꾸지 않았습니다: TOKEN 줄이 여러 개이거나 "
              "'export TOKEN=' 형식입니다. TOKEN 줄을 하나(TOKEN=...)로 정리한 뒤 다시 실행하세요.", file=sys.stderr)
        return 1
    replacing = args.env.exists()
    args.env.write_text(text, encoding="utf-8")
    if os.environ.get("TOKEN"):
        print("주의: 환경변수 TOKEN이 설정돼 있어 서버는 .env 대신 그 값을 씁니다.", file=sys.stderr)
    if replacing:
        print(f"{args.env}의 토큰을 바꿨습니다. 서버를 다시 시작하고, 각 폰 설정에 새 토큰을 넣으세요:")
    else:
        print(f"{args.env} 작성 완료. 폰 설정에 넣을 토큰:")
    print(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
