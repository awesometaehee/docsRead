"""조회 API. 실행: uv run uvicorn docsread.api.main:app --reload --port 8000"""
from __future__ import annotations

import hashlib
import re
import threading
import unicodedata
from pathlib import Path

import pymupdf
from datetime import datetime

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, or_, select

from ..editor.convert import document_to_editor
from ..ingest.pipeline import ingest_file
from ..config import ASSET_DIR, DOCS_DIR, ROOT
from ..db import (Asset, Block, Document, Draft, LayoutItem, Module, ModuleOccurrence, ModuleVariant, Page,
                  SessionLocal, init_db)

init_db()
app = FastAPI(title="docsRead")


def _doc_row(d: Document) -> dict:
    return {"id": d.id, "filename": d.filename, "doc_type": d.doc_type, "company_code": d.company_code,
            "company": d.company, "form_no": d.form_no, "product": d.product, "valid_from": d.valid_from,
            "valid_to": d.valid_to, "revised_at": d.revised_at, "page_count": d.page_count, "stats": d.stats}


@app.get("/api/stats")
def stats():
    with SessionLocal() as s:
        by = s.execute(select(Document.doc_type, Document.company, func.count()).group_by(Document.doc_type, Document.company)).all()
        return {
            "documents": s.scalar(select(func.count()).select_from(Document)),
            "pages": s.scalar(select(func.count()).select_from(Page)),
            "layout_items": s.scalar(select(func.count()).select_from(LayoutItem)),
            "blocks": s.scalar(select(func.count()).select_from(Block)),
            "assets": s.scalar(select(func.count()).select_from(Asset)),
            "by_type_company": [{"doc_type": t, "company": c, "count": n} for t, c, n in by],
        }


@app.get("/api/documents")
def list_documents(doc_type: str | None = None, company: str | None = None, q: str | None = None):
    with SessionLocal() as s:
        stmt = select(Document).order_by(Document.doc_type, Document.company_code, Document.form_no)
        if doc_type:
            stmt = stmt.where(Document.doc_type == doc_type)
        if company:
            stmt = stmt.where(Document.company == company)
        if q:
            like = f"%{q}%"
            hit = select(Block.document_id).where(Block.text.like(like))
            stmt = stmt.where(or_(Document.filename.like(like), Document.id.in_(hit)))
        return [_doc_row(d) for d in s.scalars(stmt)]


def _get_doc(s, doc_id: int) -> Document:
    d = s.get(Document, doc_id)
    if d is None:
        raise HTTPException(404, "문서를 찾을 수 없습니다")
    return d


@app.get("/api/documents/{doc_id}")
def get_document(doc_id: int):
    with SessionLocal() as s:
        d = _get_doc(s, doc_id)
        return {**_doc_row(d), "meta": d.meta,
                "pages": [{"page_no": p.page_no, "width": p.width, "height": p.height} for p in d.pages]}


@app.get("/api/documents/{doc_id}/pages/{page_no}/layout")
def page_layout(doc_id: int, page_no: int):
    """레이아웃 계층: 그리기 순서대로 정렬된 글자·도형·이미지."""
    with SessionLocal() as s:
        p = s.scalar(select(Page).where(Page.document_id == doc_id, Page.page_no == page_no))
        if p is None:
            raise HTTPException(404, "페이지를 찾을 수 없습니다")
        return {"width": p.width, "height": p.height,
                "items": [{"k": i.kind, "b": [i.x0, i.y0, i.x1, i.y1], **i.data} for i in p.items]}


@app.get("/api/documents/{doc_id}/blocks")
def doc_blocks(doc_id: int):
    """구조 계층: 조·문단·표·그림."""
    with SessionLocal() as s:
        d = _get_doc(s, doc_id)
        return [{"seq": b.seq, "parent": b.parent_seq, "page": b.page_no, "kind": b.kind, "label": b.label,
                 "title": b.title, "text": b.text, "content": b.content, "bbox": b.bbox} for b in d.blocks]


@app.get("/api/documents/{doc_id}/pages/{page_no}/original.png")
def page_original(doc_id: int, page_no: int, dpi: int = Query(144, ge=50, le=300)):
    """비교용 원본 렌더링(PDF 를 그대로 그린 그림)."""
    with SessionLocal() as s:
        d = _get_doc(s, doc_id)
        path = (DOCS_DIR.parent / d.rel_path) if (DOCS_DIR.parent / d.rel_path).exists() else DOCS_DIR / d.rel_path
    if not path.exists():
        raise HTTPException(404, "원본 PDF 가 없습니다")
    with pymupdf.open(path) as pdf:
        if not 0 <= page_no < len(pdf):
            raise HTTPException(404, "페이지를 찾을 수 없습니다")
        png = pdf[page_no].get_pixmap(dpi=dpi).tobytes("png")
    return Response(png, media_type="image/png", headers={"Cache-Control": "max-age=3600"})


@app.get("/api/assets/{sha}.png")
def asset(sha: str):
    if not re.fullmatch(r"[0-9a-f]{64}", sha) or not (ASSET_DIR / f"{sha}.png").exists():
        raise HTTPException(404, "이미지를 찾을 수 없습니다")
    return FileResponse(ASSET_DIR / f"{sha}.png", headers={"Cache-Control": "max-age=31536000, immutable"})


@app.get("/api/modules")
def list_modules(kind: str | None = None, q: str | None = None, min_docs: int = 1):
    """공통 모듈 목록(많은 문서에 쓰인 순)."""
    with SessionLocal() as s:
        stmt = select(Module).where(Module.n_docs >= min_docs).order_by(Module.n_docs.desc(), Module.id)
        if kind:
            stmt = stmt.where(Module.kind == kind)
        if q:
            hit = select(ModuleVariant.module_id).where(ModuleVariant.text.like(f"%{q}%"))
            stmt = stmt.where(or_(Module.title.like(f"%{q}%"), Module.id.in_(hit)))
        return [{"id": m.id, "kind": m.kind, "title": m.title, "n_docs": m.n_docs, "n_occurrences": m.n_occurrences,
                 "n_variants": m.n_variants, "n_slots": m.n_slots, "doc_types": m.doc_types} for m in s.scalars(stmt)]


@app.get("/api/modules/summary")
def module_summary():
    """모듈화 범위: 전체 단위 중 몇 %가 여러 문서 공통 모듈로 표현되는가(글자 수 기준)."""
    with SessionLocal() as s:
        rows = s.execute(select(Module.n_docs, func.sum(ModuleOccurrence.chars), func.count(ModuleOccurrence.id))
                         .join(ModuleVariant, ModuleVariant.module_id == Module.id)
                         .join(ModuleOccurrence, ModuleOccurrence.variant_id == ModuleVariant.id)
                         .group_by(Module.id)).all()
        total = sum(r[1] for r in rows) or 1
        shared = sum(r[1] for r in rows if r[0] >= 2)
        return {"modules": len(rows), "shared_modules": sum(1 for r in rows if r[0] >= 2),
                "units": sum(r[2] for r in rows), "chars": total, "shared_char_ratio": round(shared / total, 4),
                "variants": s.scalar(select(func.count()).select_from(ModuleVariant))}


@app.get("/api/modules/{module_id}")
def get_module(module_id: int):
    with SessionLocal() as s:
        m = s.get(Module, module_id)
        if m is None:
            raise HTTPException(404, "모듈을 찾을 수 없습니다")
        docs = {d.id: d for d in s.scalars(select(Document).where(Document.id.in_(
            select(ModuleOccurrence.document_id).join(ModuleVariant).where(ModuleVariant.module_id == module_id))))}
        return {"id": m.id, "kind": m.kind, "title": m.title, "n_docs": m.n_docs, "n_occurrences": m.n_occurrences,
                "doc_types": m.doc_types,
                "variants": [{"id": v.id, "ordinal": v.ordinal, "n_docs": v.n_docs, "n_occurrences": v.n_occurrences,
                              "text": v.text, "parts": v.parts, "slots": v.slots,
                              "occurrences": [{"doc_id": o.document_id, "seq": o.block_seq, "label": o.label,
                                               "values": o.values, "doc_type": docs[o.document_id].doc_type,
                                               "company": docs[o.document_id].company,
                                               "product": docs[o.document_id].product,
                                               "revised_at": docs[o.document_id].revised_at}
                                              for o in sorted(v.occurrences, key=lambda o: (
                                                  docs[o.document_id].company_code or "", docs[o.document_id].form_no or ""))]}
                             for v in m.variants]}


@app.get("/api/documents/{doc_id}/modules")
def doc_modules(doc_id: int):
    """문서의 블록 → 모듈 대응(구조 보기의 배지, 문서별 모듈화 비율)."""
    with SessionLocal() as s:
        rows = s.execute(select(ModuleOccurrence.block_seq, Module.id, Module.title, Module.n_docs,
                                ModuleVariant.ordinal, Module.n_variants)
                         .join(ModuleVariant, ModuleVariant.id == ModuleOccurrence.variant_id)
                         .join(Module, Module.id == ModuleVariant.module_id)
                         .where(ModuleOccurrence.document_id == doc_id)).all()
        return {str(seq): {"module_id": mid, "title": title, "n_docs": n, "variant": ordinal, "n_variants": nv}
                for seq, mid, title, n, ordinal, nv in rows}


@app.post("/api/assets")
async def upload_asset(request: Request):
    """편집기에서 넣는 이미지. 본문을 PNG 로 바꿔 내용 해시 이름으로 data/assets 에 저장한다."""
    import hashlib

    raw = await request.body()
    if not raw or len(raw) > 15 * 1024 * 1024:
        raise HTTPException(413, "이미지는 15MB 이하만 올릴 수 있습니다")
    try:
        pix = pymupdf.Pixmap(raw)
        if pix.colorspace is not None and pix.colorspace.n != 3:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        png = pix.tobytes("png")
    except Exception:
        raise HTTPException(415, "이미지 파일(PNG·JPG 등)만 올릴 수 있습니다")
    sha = hashlib.sha256(png).hexdigest()
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    path = ASSET_DIR / f"{sha}.png"
    if not path.exists():
        path.write_bytes(png)
    with SessionLocal() as s:
        if s.get(Asset, sha) is None:
            s.add(Asset(sha256=sha, width=pix.width, height=pix.height, bytes=len(png), use_count=0))
            s.commit()
    return {"src": f"/api/assets/{sha}.png", "width": pix.width, "height": pix.height}


MAX_PDF_BYTES = 100 * 1024 * 1024
UPLOAD_DIR = DOCS_DIR / "uploads"
_ingest_lock = threading.Lock()     # SQLite 는 쓰기 하나씩: 적재·모듈 계산을 직렬화
# stale: 마지막 모듈 계산 뒤 새 문서가 들어옴(서버 재시작 시 False 로 시작)
_rebuild = {"state": "idle", "started_at": None, "finished_at": None, "message": None, "stale": False}


def _safe_pdf_name(name: str) -> str:
    """경로 성분을 떼고 파일 이름에 쓸 수 없는 글자를 바꾼다. 확장자는 .pdf 로 맞춘다."""
    name = unicodedata.normalize("NFC", name or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "_", name).strip(" .")
    stem = re.sub(r"\.pdf$", "", name, flags=re.I)[:180] or "문서"
    return f"{stem}.pdf"


@app.post("/api/documents/upload")
async def upload_document(request: Request, filename: str = Query(..., min_length=1, max_length=255)):
    """PDF 한 건(요청 본문 = 파일 바이트)을 docs/uploads 에 저장하고 바로 데이터화한다.

    - 같은 내용(해시 동일)이 이미 있으면 파일을 저장하지 않고 기존 문서를 돌려준다.
    - 적재에 실패하면 이번에 저장한 파일을 지우고 오류를 그대로 알린다(조용히 넘기지 않음).
    - 공통 모듈은 다시 계산하지 않는다. 필요할 때 POST /api/module-rebuild.
    """
    size = int(request.headers.get("content-length") or 0)
    if size > MAX_PDF_BYTES:
        raise HTTPException(413, f"PDF 는 {MAX_PDF_BYTES // 1024 // 1024}MB 이하만 올릴 수 있습니다")
    raw = await request.body()
    return await run_in_threadpool(_ingest_upload, raw, filename)


def _ingest_upload(raw: bytes, filename: str) -> dict:
    if not raw:
        raise HTTPException(400, "빈 파일입니다")
    if len(raw) > MAX_PDF_BYTES:
        raise HTTPException(413, f"PDF 는 {MAX_PDF_BYTES // 1024 // 1024}MB 이하만 올릴 수 있습니다")
    if not raw.lstrip()[:5].startswith(b"%PDF-"):
        raise HTTPException(415, "PDF 파일만 올릴 수 있습니다")
    try:
        with pymupdf.open(stream=raw, filetype="pdf") as probe:
            if probe.needs_pass:
                raise HTTPException(415, "암호가 걸린 PDF 는 처리할 수 없습니다")
            if len(probe) == 0:
                raise HTTPException(415, "쪽이 없는 PDF 입니다")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(415, "PDF 를 열 수 없습니다(손상된 파일)")

    sha = hashlib.sha256(raw).hexdigest()
    name = _safe_pdf_name(filename)
    with _ingest_lock, SessionLocal() as s:
        existing = s.scalar(select(Document).where(Document.sha256 == sha))
        if existing:
            return {"status": "duplicate", "document": _doc_row(existing)}
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        ASSET_DIR.mkdir(parents=True, exist_ok=True)
        path, n = UPLOAD_DIR / name, 1
        while path.exists():          # 이름이 같고 내용이 다른 파일은 덮어쓰지 않는다
            n += 1
            path = UPLOAD_DIR / f"{name[:-4]} ({n}).pdf"
        path.write_bytes(raw)
        try:
            with s.begin_nested():
                result = ingest_file(s, path, DOCS_DIR, False, {})
            s.commit()
        except Exception as e:
            s.rollback()
            path.unlink(missing_ok=True)
            raise HTTPException(422, f"데이터화 실패: {e!r}")
        d = s.scalar(select(Document).where(Document.sha256 == sha))
        if result != "ingested" or d is None:
            raise HTTPException(500, f"적재 결과를 확인할 수 없습니다({result})")
        _rebuild["stale"] = True
        return {"status": "ingested", "document": _doc_row(d)}


def _run_rebuild():
    from ..modules.build import main as build_modules
    try:
        with _ingest_lock:
            code = build_modules()
        _rebuild.update(state="done" if code == 0 else "error",
                        message=None if code == 0 else "대사 불일치(단위 수 ≠ 사용처 수). 서버 로그를 확인하세요",
                        stale=False if code == 0 else _rebuild.get("stale", False))
    except Exception as e:
        _rebuild.update(state="error", message=repr(e))
    finally:
        _rebuild["finished_at"] = datetime.now().isoformat(timespec="seconds")


@app.post("/api/module-rebuild")
def rebuild_modules():
    """공통 모듈을 전체 문서 기준으로 다시 계산한다(백그라운드). 편집 문서는 자체 JSON 이라 영향 없음."""
    if _rebuild["state"] == "running":
        return _rebuild
    _rebuild.update(state="running", started_at=datetime.now().isoformat(timespec="seconds"),
                    finished_at=None, message=None)
    threading.Thread(target=_run_rebuild, name="module-rebuild", daemon=True).start()
    return _rebuild


@app.get("/api/module-rebuild")
def rebuild_status():
    return _rebuild


def _draft_row(d: Draft) -> dict:
    return {"id": d.id, "title": d.title, "doc_type": d.doc_type, "base_document_id": d.base_document_id,
            "updated_at": d.updated_at.isoformat(timespec="seconds")}


@app.get("/api/drafts")
def list_drafts():
    with SessionLocal() as s:
        return [_draft_row(d) for d in s.scalars(select(Draft).order_by(Draft.updated_at.desc()))]


@app.post("/api/drafts")
def create_draft(payload: dict = Body(default={})):
    """기존 문서를 복제해 편집 문서를 만든다(document_id 가 없으면 빈 문서). 원본 데이터는 바뀌지 않는다."""
    with SessionLocal() as s:
        doc_id = payload.get("document_id")
        if doc_id is None:
            dtype = "brochure" if payload.get("doc_type") == "brochure" else "terms"
            d = Draft(title=payload.get("title") or "새 문서", doc_type=dtype,
                      content={"type": "doc", "content": [
                          {"type": "box", "attrs": {"border": True, "round": True, "pad": 7}, "content": [
                              {"type": "heading", "attrs": {"level": 1}, "content": [{"type": "text", "text": "제목"}]},
                              {"type": "paragraph"}]}]},
                      # 상품설명서는 본문(유의사항)이 작고 표 글자는 따로 키우는 구성이라 기본 글자를 작게 둔다
                      style={"body_pt": 4.8 if dtype == "brochure" else 8.4,
                             "line_height": 1.2 if dtype == "brochure" else 1.14,
                             "page_w": 595.3, "page_h": 841.9, "pad": 14.2, "doc_type": dtype})
            stats = {}
        else:
            doc = _get_doc(s, int(doc_id))
            content, stats, style = document_to_editor(s, doc)
            kind = "약관" if doc.doc_type == "terms" else "상품설명서"
            d = Draft(title=f"{doc.product or doc.filename} {kind} (편집본)", doc_type=doc.doc_type,
                      base_document_id=doc.id, content=content, style=style)
        s.add(d)
        s.commit()
        return {**_draft_row(d), "content": d.content, "style": d.style or {}, "stats": stats}


@app.get("/api/drafts/{draft_id}")
def get_draft(draft_id: int):
    with SessionLocal() as s:
        d = s.get(Draft, draft_id)
        if d is None:
            raise HTTPException(404, "편집 문서를 찾을 수 없습니다")
        return {**_draft_row(d), "content": d.content, "style": d.style or {}}


@app.put("/api/drafts/{draft_id}")
def save_draft(draft_id: int, payload: dict = Body(...)):
    with SessionLocal() as s:
        d = s.get(Draft, draft_id)
        if d is None:
            raise HTTPException(404, "편집 문서를 찾을 수 없습니다")
        if "title" in payload:
            d.title = str(payload["title"])[:200] or d.title
        if isinstance(payload.get("style"), dict):
            d.style = {**(d.style or {}), **{k: v for k, v in payload["style"].items()
                                             if k in ("body_pt", "line_height") and isinstance(v, (int, float))}}
        if "content" in payload:
            if not isinstance(payload["content"], dict) or payload["content"].get("type") != "doc":
                raise HTTPException(422, "content 는 편집기 문서 JSON 이어야 합니다")
            d.content = payload["content"]
        d.updated_at = datetime.utcnow()
        s.commit()
        return {**_draft_row(d), "style": d.style or {}}


@app.delete("/api/drafts/{draft_id}")
def delete_draft(draft_id: int):
    with SessionLocal() as s:
        d = s.get(Draft, draft_id)
        if d is None:
            raise HTTPException(404, "편집 문서를 찾을 수 없습니다")
        s.delete(d)
        s.commit()
        return {"deleted": draft_id}


_dist = ROOT / "frontend" / "dist"
if _dist.exists():   # 빌드된 화면이 있으면 같은 서버에서 제공
    app.mount("/", StaticFiles(directory=_dist, html=True), name="web")
