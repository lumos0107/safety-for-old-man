"""관절 26점 모델(RTMPose-m Halpe26)을 받아 server/models/에 둔다. 설계: design/2026-09-30-body-detail-design.md 6.3

사용: .venv\\Scripts\\python tools\\fetch_models.py
- OpenMMLab 배포 zip에서 end2end.onnx 하나만 꺼내 SHA-256을 확인한 뒤 정한 이름으로 옮긴다 (다른 파일은 풀지 않음).
- 해시가 다르면 아무것도 바꾸지 않는다 (이미 있던 파일도 그대로).
"""
import hashlib
import os
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.pose_detail import MODEL_PATH, MODEL_SHA256, MODEL_URL  # noqa: E402

CHUNK = 1 << 20


class FetchError(RuntimeError):
    pass


def extract_model(zip_path: Path, dest: Path, expected_sha256: str) -> None:
    """zip 안의 */end2end.onnx 하나를 dest로 옮긴다. 해시가 맞을 때만 dest를 바꾼다."""
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise FetchError(f"받은 파일이 zip이 아닙니다 ({exc})") from exc
    with zf:
        members = [n for n in zf.namelist() if n.split("/")[-1] == "end2end.onnx"]
        if len(members) != 1:
            raise FetchError(f"zip 안에 end2end.onnx가 하나가 아닙니다 ({len(members)}개)")
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=dest.parent, suffix=".part")
        digest = hashlib.sha256()
        try:
            with os.fdopen(fd, "wb") as out, zf.open(members[0]) as src:
                while chunk := src.read(CHUNK):
                    digest.update(chunk)
                    out.write(chunk)
            if digest.hexdigest() != expected_sha256:
                raise FetchError(f"해시가 다릅니다: {digest.hexdigest()} (기대 {expected_sha256}) — 파일을 바꾸지 않았습니다")
            os.replace(tmp, dest)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)


def main() -> int:
    if MODEL_PATH.exists() and hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest() == MODEL_SHA256:
        print(f"이미 있습니다 (해시 일치): {MODEL_PATH}")
        return 0
    print(f"받는 중: {MODEL_URL}")
    fd, zpath = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    try:
        urllib.request.urlretrieve(MODEL_URL, zpath)
        extract_model(Path(zpath), MODEL_PATH, MODEL_SHA256)
    except (OSError, FetchError) as exc:
        print(f"실패: {exc}", file=sys.stderr)
        return 1
    finally:
        os.remove(zpath)
    print(f"완료: {MODEL_PATH} (SHA-256 확인됨). 서버를 다시 시작하면 관절 26점으로 동작합니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
