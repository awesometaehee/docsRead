"""이미 적재한 문서의 블록(조·섹션·문단·표)만 원본 PDF 에서 다시 만든다.

  실행: uv run python -m docsread.ingest.reblock [--apply] [--ids 1,2,3]

- 문서 id·쪽·그리기 요소(LayoutItem)·이미지는 그대로 두고 블록만 바꾼다(편집 문서의 원본 연결이 유지된다).
- 기본은 미리 보기: 바뀌는 문서 수와 바뀐 문단을 보고만 한다. --apply 를 주어야 DB 에 쓴다.
- 블록이 바뀌면 공통 모듈 출현 위치(block_seq)가 어긋나므로, 적용 뒤에는 모듈을 다시 만들어야 한다
  (API: POST /api/module-rebuild 또는 python -m docsread.modules.build).
- 원본 PDF 는 읽기만 한다.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter

import pymupdf
from sqlalchemy import delete, select

from ..config import ASSET_DIR, DOCS_DIR
from ..db import Block, Document, SessionLocal
from .extract import extract_page
from .structure import build_blocks


def _sig(b: dict) -> tuple:
    return (b["kind"], b["parent_seq"], b.get("title"), b.get("text"),
            tuple(round(v) for v in b["bbox"]) if b.get("bbox") else None)


def rebuild(doc: Document) -> list[dict]:
    pdf = pymupdf.open(DOCS_DIR / doc.rel_path)
    cache: dict = {}
    pages = [extract_page(pdf, p, ASSET_DIR, cache) for p in pdf]
    return build_blocks(pages, doc.doc_type), pages


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="DB 에 적용한다(없으면 미리 보기)")
    ap.add_argument("--ids", default="", help="쉼표로 구분한 문서 id(없으면 전체)")
    ap.add_argument("--show", type=int, default=3, help="바뀐 문서마다 보여 줄 문단 수")
    args = ap.parse_args(argv)
    ids = [int(x) for x in args.ids.split(",") if x.strip()]
    changed, unchanged, failed = [], 0, []
    kinds_before, kinds_after = Counter(), Counter()
    with SessionLocal() as s:
        q = select(Document).order_by(Document.id)
        if ids:
            q = q.where(Document.id.in_(ids))
        docs = list(s.scalars(q))
        for i, d in enumerate(docs, 1):
            try:
                new, pages = rebuild(d)
            except Exception as e:  # 한 문서 실패가 전체를 막지 않되 반드시 보고한다
                failed.append((d.id, d.filename, repr(e)))
                continue
            old = [{"kind": b.kind, "parent_seq": b.parent_seq, "title": b.title, "text": b.text, "bbox": b.bbox}
                   for b in d.blocks]
            kinds_before.update(b["kind"] for b in old)
            kinds_after.update(b["kind"] for b in new)
            if [_sig(b) for b in old] == [_sig(b) for b in new]:
                unchanged += 1
                continue
            old_paras = {b["text"] for b in old if b["kind"] == "paragraph"}
            new_paras = [b for b in new if b["kind"] == "paragraph" and b["text"] not in old_paras]
            changed.append((d, len(old), len(new), new_paras))
            if args.apply:
                s.execute(delete(Block).where(Block.document_id == d.id))
                for b in new:
                    s.add(Block(document_id=d.id, **b))
                st = dict(d.stats or {})
                st.update({"tables": sum(len(p.tables) for p in pages),
                           "articles": sum(1 for b in new if b["kind"] == "article"),
                           "sections": sum(1 for b in new if b["kind"] == "section")})
                d.stats = st
                s.commit()
            if i % 50 == 0:
                print(f"[{i}/{len(docs)}] 바뀜 {len(changed)}", flush=True)
    print(f"\n문서 {len(docs)}개: 바뀜 {len(changed)}, 그대로 {unchanged}, 실패 {len(failed)}"
          f" ({'적용함' if args.apply else '미리 보기 — DB 는 그대로'})")
    print("블록 종류 전:", dict(kinds_before))
    print("블록 종류 후:", dict(kinds_after))
    by_type = Counter(d.doc_type for d, *_ in changed)
    print("바뀐 문서 종류:", dict(by_type))
    for d, n0, n1, paras in changed[: 40]:
        print(f"\n#{d.id} {d.doc_type} 블록 {n0}→{n1} {d.filename}")
        for b in paras[: args.show]:
            print(f"    새 문단 {[round(v) for v in b['bbox']]} {b['text'][:70]}")
    for fid, name, err in failed:
        print("실패:", fid, name, err)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
