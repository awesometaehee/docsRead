"""문서 한 건을 쪽별로 LLM 변환해 편집 문서(drafts)를 만드는 백그라운드 작업.

- 쪽 결과는 llm_pages 에 저장한다. 같은 모델·프롬프트 판이면 다시 부르지 않는다(비용·재현성)
- 한 쪽이라도 실패하면 편집 문서를 만들지 않고 실패한 쪽과 이유를 알린다(조용히 빠뜨리지 않음)
- 글자 대조(PDF 글자 레이어 ↔ LLM 결과)가 기준 미만인 쪽은 경고로 남긴다
- 원본 PDF·문서 데이터는 읽기만 한다
"""
from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from ..config import DOCS_DIR
from ..db import Block, Document, Draft, LlmPage, SessionLocal
from . import extract, validate
from .to_editor import to_editor

WORKERS = 4
MIN_RECALL, MIN_PRECISION = 0.97, 0.97

jobs: dict[str, dict] = {}
_lock = threading.Lock()


def pdf_path(d: Document) -> Path:
    p = DOCS_DIR.parent / d.rel_path
    return p if p.exists() else DOCS_DIR / d.rel_path


def _page_text(doc: Document, page_no: int) -> str:
    page = next(p for p in doc.pages if p.page_no == page_no)
    return "".join(sp["t"] for it in page.items if it.kind == "text" for sp in it.data["spans"])


def _update(job_id: str, **kw):
    with _lock:
        jobs[job_id].update(kw)


def _one_page(job_id: str, doc_id: int, doc_type: str, path: Path, page_no: int, version: str, force: bool) -> dict:
    with SessionLocal() as s:
        row = None if force else s.scalar(select(LlmPage).where(
            LlmPage.document_id == doc_id, LlmPage.page_no == page_no,
            LlmPage.model == extract.MODEL, LlmPage.prompt_version == version).order_by(LlmPage.id.desc()))
        if row is not None:
            with _lock:
                jobs[job_id]["cached"] += 1
            return row.output
    png = extract.render_page(path, page_no)
    with SessionLocal() as s:
        pdf_text = _page_text(s.get(Document, doc_id), page_no)
    total = {"input": 0, "output": 0}

    def ask(retry=None):
        out, usage = extract.call_llm(png, doc_type, retry)
        for k in total:
            total[k] += usage.get(k, 0)
        with _lock:
            j = jobs[job_id]
            j["tokens"]["input"] += usage.get("input", 0)
            j["tokens"]["output"] += usage.get("output", 0)
        # 코드로 강제하는 규칙: 고칠 수 있는 것은 고치고(notes), 못 고치는 것은 문제로(problems)
        out, notes = validate.normalize(out, page_no == 0)
        return out, notes, validate.problems(out, pdf_text)

    out, notes, probs = ask()
    attempts = 1
    if probs and validate.RETRY:    # 크게 틀린 경우만 이전 응답과 문제를 붙여 한 번 더(few-shot 없이 보내 비용을 줄인다)
        out2, notes2, probs2 = ask((out, probs))
        attempts = 2
        if len(probs2) <= len(probs):
            out, notes, probs = out2, notes2, probs2
    usage = {**total, "attempts": attempts, "fixed": notes, "problems": probs}
    with SessionLocal() as s:
        s.add(LlmPage(document_id=doc_id, page_no=page_no, model=extract.MODEL, prompt_version=version,
                      output=out, usage=usage))
        s.commit()
    with _lock:
        jobs[job_id]["fixed"] += len(notes)
        jobs[job_id]["retried"] += attempts - 1
    return out


def _run(job_id: str, doc_id: int, force: bool):
    try:
        with SessionLocal() as s:
            doc = s.get(Document, doc_id)
            path, n, doc_type = pdf_path(doc), doc.page_count, doc.doc_type
        version = extract.prompt_version(doc_type)
        if not path.exists():
            raise RuntimeError("원본 PDF 가 없습니다")
        results: dict[int, dict] = {}
        errors: list[str] = []

        def work(pno: int):
            try:
                results[pno] = _one_page(job_id, doc_id, doc_type, path, pno, version, force)
            except Exception as e:
                errors.append(f"{pno + 1}쪽: {e}")
            with _lock:
                jobs[job_id]["done"] += 1

        with ThreadPoolExecutor(WORKERS) as ex:
            list(ex.map(work, range(n)))
        if errors:
            raise RuntimeError("일부 쪽을 변환하지 못했습니다(성공한 쪽은 저장돼 다시 실행 시 재사용) — " + "; ".join(sorted(errors)))

        pages = [results[i] for i in range(n)]
        with SessionLocal() as s:
            doc = s.get(Document, doc_id)
            content, warn, style = to_editor(pages, doc, path)
            checks = []
            for i, page in enumerate(pages):
                f = extract.fidelity(_page_text(doc, i), extract.output_text(page))
                if f is None:
                    warn.append(f"{i + 1}쪽: PDF 글자 레이어가 없어 글자 대조를 못 함")
                    continue
                checks.append({"page": i + 1, **f})
                if f["recall"] < MIN_RECALL:
                    warn.append(f"{i + 1}쪽: 원문 글자 중 {100 * (1 - f['recall']):.1f}% 를 옮기지 않음(빠뜨림 확인 필요)")
                if f["precision"] < MIN_PRECISION:
                    warn.append(f"{i + 1}쪽: 옮긴 글자 중 {100 * (1 - f['precision']):.1f}% 가 원문에 없음(오독·지어냄 확인 필요)")
            kind = "약관" if doc.doc_type == "terms" else "상품설명서"
            d = Draft(title=f"{doc.product or doc.filename} {kind} (AI 변환)", doc_type=doc.doc_type,
                      base_document_id=doc.id, content=content, style=style)
            s.add(d)
            s.commit()
            _update(job_id, state="done", draft_id=d.id, warnings=warn, checks=checks)
    except Exception as e:
        _update(job_id, state="error", message=str(e))
    finally:
        _update(job_id, finished_at=datetime.now().isoformat(timespec="seconds"))


def start(doc_id: int, force: bool = False) -> dict:
    with SessionLocal() as s:
        doc = s.get(Document, doc_id)
        if doc is None:
            raise LookupError("문서를 찾을 수 없습니다")
        total = doc.page_count
    with _lock:
        running = next((j for j in jobs.values() if j["document_id"] == doc_id and j["state"] == "running"), None)
        if running:
            return dict(running)
        job_id = uuid.uuid4().hex[:12]
        jobs[job_id] = {"id": job_id, "document_id": doc_id, "state": "running", "total": total, "done": 0,
                        "cached": 0, "fixed": 0, "retried": 0, "tokens": {"input": 0, "output": 0}, "model": extract.MODEL,
                        "started_at": datetime.now().isoformat(timespec="seconds"), "finished_at": None,
                        "draft_id": None, "warnings": [], "checks": [], "message": None}
    threading.Thread(target=_run, args=(job_id, doc_id, force), name=f"llm-{job_id}", daemon=True).start()
    return dict(jobs[job_id])


def status(job_id: str) -> dict | None:
    with _lock:
        j = jobs.get(job_id)
        return dict(j) if j else None
