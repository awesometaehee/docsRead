"""docs/ 아래 PDF 를 DB 에 적재한다.  실행: uv run python -m docsread.ingest.pipeline [폴더] [--force]

- 원본 PDF 는 읽기만 한다(수정·삭제 없음). 결과는 data/ 아래에 따로 쓴다.
- 파일 해시로 중복 적재를 막고, 실패한 파일은 건너뛰지 않고 목록으로 보고한다.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import time
import unicodedata
from pathlib import Path

import pymupdf
from sqlalchemy import delete, func, select

from ..config import ASSET_DIR, DOCS_DIR
from ..db import Asset, Block, Document, LayoutItem, Page, SessionLocal, init_db
from .extract import extract_page
from .filename import parse_filename
from .structure import build_blocks


def doc_type_of(path: Path) -> str:
    return "brochure" if "상품설명서" in unicodedata.normalize("NFC", str(path)) else "terms"


def ingest_file(session, path: Path, root: Path, force: bool, seen: dict) -> str:
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if sha in seen:   # 이름만 다른 동일 파일
        return "duplicate"
    seen[sha] = path.name
    existing = session.scalar(select(Document).where(Document.sha256 == sha))
    if existing and not force:
        return "skipped"
    if existing:
        session.delete(existing)
        session.flush()
    doc = pymupdf.open(stream=raw, filetype="pdf")
    dtype = doc_type_of(path)
    name = unicodedata.normalize("NFC", path.name)
    img_cache: dict = {}
    pages = [extract_page(doc, p, ASSET_DIR, img_cache) for p in doc]
    blocks = build_blocks(pages, dtype)

    stats = {
        "chars": sum(len(l.text) for pd in pages for l in pd.lines),
        "paths": sum(1 for pd in pages for i in pd.items if i["kind"] == "path"),
        "images": sum(len(pd.images) for pd in pages),
        "tables": sum(len(pd.tables) for pd in pages),
        "articles": sum(1 for b in blocks if b["kind"] == "article"),
        "sections": sum(1 for b in blocks if b["kind"] == "section"),
        "warnings": sorted({w for pd in pages for w in pd.warnings}),
    }
    d = Document(sha256=sha, rel_path=unicodedata.normalize("NFC", str(path.relative_to(root))), filename=name,
                 doc_type=dtype, page_count=len(pages), stats=stats,
                 meta={k: v for k, v in (doc.metadata or {}).items() if v}, **parse_filename(name))
    for pno, pd in enumerate(pages):
        pg = Page(page_no=pno, width=pd.width, height=pd.height)
        for it in pd.items:
            bb = it["bbox"]
            pg.items.append(LayoutItem(z=it["z"], kind=it["kind"], x0=round(bb[0], 2), y0=round(bb[1], 2),
                                       x1=round(bb[2], 2), y1=round(bb[3], 2), data=it["data"]))
        d.pages.append(pg)
        for im in pd.images:
            a = session.get(Asset, im["sha"])
            if a is None:
                session.add(Asset(sha256=im["sha"], width=im["w"], height=im["h"], bytes=im["bytes"], use_count=1))
                session.flush()
            else:
                a.use_count += 1
    for b in blocks:
        d.blocks.append(Block(**b))
    session.add(d)
    return "ingested"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("folder", nargs="?", default=str(DOCS_DIR))
    ap.add_argument("--force", action="store_true", help="이미 적재된 파일도 다시 처리")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)
    root = Path(args.folder)
    files = sorted(root.rglob("*.pdf"))
    if args.limit:
        files = files[: args.limit]
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    init_db()
    counts = {"ingested": 0, "skipped": 0, "duplicate": 0, "failed": 0}
    failures, dups, seen = [], [], {}
    t0 = time.time()
    with SessionLocal() as session:
        if args.force:
            session.execute(delete(Asset))
        for i, f in enumerate(files, 1):
            try:
                with session.begin_nested():
                    result = ingest_file(session, f, root, args.force, seen)
                counts[result] += 1
                if result == "duplicate":
                    dups.append(f.name)
                session.commit()
            except Exception as e:  # 한 파일의 실패가 전체를 막지 않되 반드시 보고한다
                session.rollback()
                counts["failed"] += 1
                failures.append((f.name, repr(e)))
            if i % 25 == 0 or i == len(files):
                print(f"[{i}/{len(files)}] {counts} {time.time() - t0:.0f}s", flush=True)
        total = {
            "documents": session.scalar(select(func.count()).select_from(Document)),
            "pages": session.scalar(select(func.count()).select_from(Page)),
            "layout_items": session.scalar(select(func.count()).select_from(LayoutItem)),
            "blocks": session.scalar(select(func.count()).select_from(Block)),
            "assets": session.scalar(select(func.count()).select_from(Asset)),
        }
    print("대사:", {"파일": len(files), **counts}, "DB:", total)
    for name in dups:
        print("중복(내용 동일):", unicodedata.normalize("NFC", name))
    for name, err in failures:
        print("실패:", name, err)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
