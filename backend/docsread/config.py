from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[2]


def _load_env(path: Path) -> None:
    """프로젝트 루트 .env 의 KEY=VALUE 를 환경변수로 읽는다. 이미 설정된 환경변수가 우선한다."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip().removeprefix("export ").strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value or value.startswith("#"):     # 따옴표 없는 값 뒤의 주석
            value = value.split(" #", 1)[0].strip() if not value.startswith("#") else ""
        os.environ.setdefault(key, value)


_load_env(ROOT / ".env")
DATA_DIR = Path(os.environ.get("DOCSREAD_DATA", ROOT / "data"))
DB_URL = os.environ.get("DOCSREAD_DB", f"sqlite:///{DATA_DIR / 'docsread.db'}")
ASSET_DIR = DATA_DIR / "assets"
DOCS_DIR = Path(os.environ.get("DOCSREAD_DOCS", ROOT / "docs"))
