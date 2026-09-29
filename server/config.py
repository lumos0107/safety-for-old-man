"""서버 설정. 환경변수가 server/.env보다 우선한다."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

DEFAULT_ENV = Path(__file__).resolve().parent / ".env"
DEFAULT_ORIGINS = "https://lumos0107.github.io"
MIN_TOKEN_LEN = 32


@dataclass(frozen=True)
class Settings:
    token: str
    allowed_origins: frozenset[str]
    auth_timeout: float = 3.0
    max_pending: int = 4
    max_bytes: int = 1_048_576
    max_side: int = 2000
    model: str = "yolo11n-pose.pt"

    @property
    def model_name(self) -> str:
        return Path(self.model).stem


def load_settings(env_file: Path | None = DEFAULT_ENV) -> Settings:
    values = dict(dotenv_values(env_file)) if env_file and Path(env_file).exists() else {}
    values.update(os.environ)  # os.environ을 바꾸지 않고 합친다

    token = (values.get("TOKEN") or "").strip()
    if len(token) < MIN_TOKEN_LEN:
        raise RuntimeError("TOKEN이 없거나 너무 짧습니다. tools/gen_token.py로 만드세요.")

    raw = values.get("ALLOWED_ORIGINS") or DEFAULT_ORIGINS
    origins = frozenset(o.strip().rstrip("/") for o in raw.split(",") if o.strip())
    return Settings(
        token=token,
        allowed_origins=origins,
        auth_timeout=float(values.get("AUTH_TIMEOUT") or 3.0),
        model=values.get("MODEL") or "yolo11n-pose.pt",
    )
