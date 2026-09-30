import subprocess
import sys
from pathlib import Path

import pytest

from server.config import Settings, load_settings

ROOT = Path(__file__).resolve().parents[2]
GOOD = "a" * 43


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in ("TOKEN", "ALLOWED_ORIGINS", "AUTH_TIMEOUT", "MODEL", "DETAIL"):
        monkeypatch.delenv(key, raising=False)


def test_env_values(monkeypatch):
    monkeypatch.setenv("TOKEN", GOOD)
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://a.io/, http://localhost:5500")
    monkeypatch.setenv("AUTH_TIMEOUT", "0.5")
    s = load_settings(env_file=None)
    assert s.token == GOOD
    assert s.allowed_origins == frozenset({"https://a.io", "http://localhost:5500"})
    assert s.auth_timeout == 0.5
    assert s.model_name == "yolo11n-pose"


def test_default_origin_is_pages_only(monkeypatch):
    monkeypatch.setenv("TOKEN", GOOD)
    assert load_settings(env_file=None).allowed_origins == frozenset({"https://lumos0107.github.io"})


def test_token_whitespace_is_stripped(monkeypatch):
    monkeypatch.setenv("TOKEN", f"  {GOOD}\n")
    assert load_settings(env_file=None).token == GOOD


@pytest.mark.parametrize("token", [None, "", "short-token"])
def test_missing_or_short_token_refused(monkeypatch, token):
    if token is not None:
        monkeypatch.setenv("TOKEN", token)
    with pytest.raises(RuntimeError, match="TOKEN"):
        load_settings(env_file=None)


def test_env_file_used_and_env_var_wins(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(f"TOKEN={GOOD}\nALLOWED_ORIGINS=https://file.io\n", encoding="utf-8")
    assert load_settings(env_file=env).allowed_origins == frozenset({"https://file.io"})
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://env.io")
    assert load_settings(env_file=env).allowed_origins == frozenset({"https://env.io"})


def test_settings_defaults():
    s = Settings(token=GOOD, allowed_origins=frozenset())
    assert (s.auth_timeout, s.max_pending, s.max_bytes, s.max_side) == (3.0, 4, 1_048_576, 2000)


def run_gen(*args):
    # Windows는 파이프 출력을 cp949로 쓰므로 하위 프로세스를 UTF-8 모드로 띄운다
    return subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "tools" / "gen_token.py"), *args],
                          capture_output=True, text=True, encoding="utf-8")


def test_gen_token_writes_env_and_refuses_overwrite(tmp_path):
    env = tmp_path / ".env"
    first = run_gen("--env", str(env))
    assert first.returncode == 0
    lines = dict(line.split("=", 1) for line in env.read_text(encoding="utf-8").splitlines())
    assert len(lines["TOKEN"]) >= 43
    assert lines["ALLOWED_ORIGINS"] == "https://lumos0107.github.io"
    assert lines["TOKEN"] in first.stdout

    again = run_gen("--env", str(env))
    assert again.returncode != 0
    assert run_gen("--env", str(env), "--force").returncode == 0
    new = dict(line.split("=", 1) for line in env.read_text(encoding="utf-8").splitlines())
    assert new["TOKEN"] != lines["TOKEN"]  # --force는 토큰을 실제로 교체한다


def test_gen_token_force_keeps_allowed_origins(tmp_path):
    env = tmp_path / ".env"
    env.write_text("TOKEN=" + "o" * 43 + "\nALLOWED_ORIGINS=https://lumos0107.github.io,http://localhost:5500\n",
                   encoding="utf-8")
    assert run_gen("--env", str(env), "--force").returncode == 0
    lines = dict(line.split("=", 1) for line in env.read_text(encoding="utf-8").splitlines())
    assert lines["TOKEN"] != "o" * 43
    assert lines["ALLOWED_ORIGINS"] == "https://lumos0107.github.io,http://localhost:5500"  # 토큰만 바꾼다


def test_gen_token_force_keeps_every_other_line(tmp_path):
    env = tmp_path / ".env"
    original = ("# 팀 메모: 로컬 개발용 주소 포함\n"
                "ALLOWED_ORIGINS = https://lumos0107.github.io,http://localhost:5500\n"
                "TOKEN=" + "o" * 43 + "\n"
                "MODEL=yolo11s-pose.pt\n"
                "AUTH_TIMEOUT=5\n")
    env.write_text(original, encoding="utf-8")
    assert run_gen("--env", str(env), "--force").returncode == 0
    after = env.read_text(encoding="utf-8").splitlines()
    before = original.splitlines()
    assert len(after) == len(before)
    for old, new in zip(before, after):
        if old.startswith("TOKEN="):
            assert new.startswith("TOKEN=") and new != old and len(new) >= 6 + 43
        else:
            assert new == old  # 주석·공백 형식·다른 설정을 그대로 둔다


def test_gen_token_force_adds_token_line_when_missing(tmp_path):
    env = tmp_path / ".env"
    env.write_text("ALLOWED_ORIGINS=https://lumos0107.github.io\n", encoding="utf-8")
    assert run_gen("--env", str(env), "--force").returncode == 0
    lines = env.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("TOKEN=") and lines[1] == "ALLOWED_ORIGINS=https://lumos0107.github.io"


@pytest.mark.parametrize("original", [
    "TOKEN=" + "a" * 43 + "\nTOKEN=" + "b" * 43 + "\n",          # TOKEN 줄이 둘 — 서버는 마지막 줄을 쓴다
    "export TOKEN=" + "c" * 43 + "\nALLOWED_ORIGINS=https://x.io\n",  # export 형식도 TOKEN으로 읽힌다
], ids=["duplicate_lines", "export_form"])
def test_gen_token_force_refuses_when_new_token_would_not_apply(tmp_path, original):
    env = tmp_path / ".env"
    env.write_text(original, encoding="utf-8")
    result = run_gen("--env", str(env), "--force")
    assert result.returncode != 0
    assert "적용되지 않" in result.stderr
    assert env.read_text(encoding="utf-8") == original  # 파일은 건드리지 않는다



def test_detail_default_and_off(monkeypatch):
    monkeypatch.setenv("TOKEN", GOOD)
    assert load_settings(env_file=None).detail == "halpe26"
    monkeypatch.setenv("DETAIL", " OFF ")
    assert load_settings(env_file=None).detail == "off"


def test_unknown_detail_refused(monkeypatch):
    monkeypatch.setenv("TOKEN", GOOD)
    monkeypatch.setenv("DETAIL", "wholebody")
    with pytest.raises(RuntimeError, match="DETAIL"):
        load_settings(env_file=None)
