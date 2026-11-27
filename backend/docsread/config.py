from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("DOCSREAD_DATA", ROOT / "data"))
DB_URL = os.environ.get("DOCSREAD_DB", f"sqlite:///{DATA_DIR / 'docsread.db'}")
ASSET_DIR = DATA_DIR / "assets"
DOCS_DIR = Path(os.environ.get("DOCSREAD_DOCS", ROOT / "docs"))
