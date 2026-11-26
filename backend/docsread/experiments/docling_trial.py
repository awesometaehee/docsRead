"""Docling(IBM, MIT) 시험 적용: docs/ 의 PDF 몇 개를 Docling 으로 변환하고 docsRead 현재 추출과 비교한다.

실행(시험용 의존성 설치 후):
    uv sync --extra docling
    uv run python -m docsread.experiments.docling_trial [PDF 경로 ...]
    (경로를 안 주면 SAMPLES 를 쓴다)

결과: data/docling/<파일 이름>/ 에 doc.md · doc.html · doc.json, 그리고 data/docling/report.json.
- 모든 처리는 로컬에서 한다(외부 API 호출 없음). 모델 가중치는 처음 한 번 Hugging Face 에서 내려받는다.
- 글자가 있는 PDF 이므로 OCR 은 끈다(메모리·시간 절약). 표 구조 인식(TableFormer)은 켠다.
- 비교: 쪽별 글자 일치(PyMuPDF 글자 기준 recall/precision), 표·그림·제목 수, 처리 시간.
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import fitz

from ..config import DATA_DIR
from ..llm.extract import fidelity

ROOT = Path(__file__).resolve().parents[3]
SAMPLES = [
    "docs/uploads/101_20-3_약관_개발(보람580_2020.04.01~2021.07.31)_20261009.pdf",
    "docs/uploads/101_20-2_상품설명서_개발(보람580_2020.04.01~2021.07.31)_20261009.pdf",
    "docs/uploads/101_32-2_상품설명서_개발(하이브리드_2023.08.01~현재)_2026.09.18.pdf",
    "docs/uploads/101_35-6_약관(개발_헤리티지959_2025.11.01~현재)_2026.09.21.pdf",
]
OUT = DATA_DIR / "docling"


def _converter():
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions()
    opts.do_ocr = False                                   # 글자 레이어가 있는 PDF
    opts.do_table_structure = True
    opts.table_structure_options.mode = TableFormerMode.ACCURATE
    opts.table_structure_options.do_cell_matching = True  # 셀 글자는 PDF 글자를 그대로 쓴다
    opts.generate_picture_images = False
    opts.accelerator_options.num_threads = 4
    return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})


def _pdf_pages(path: Path) -> dict[int, str]:
    with fitz.open(path) as d:
        return {i + 1: p.get_text() for i, p in enumerate(d)}


def _docling_pages(doc) -> tuple[dict[int, str], dict[int, Counter]]:
    """Docling 문서에서 쪽별 글자와 요소 종류별 개수."""
    from docling_core.types.doc import TableItem

    text: dict[int, list[str]] = defaultdict(list)
    kinds: dict[int, Counter] = defaultdict(Counter)
    for item, _level in doc.iterate_items():
        prov = getattr(item, "prov", None) or []
        if not prov:
            continue
        pno = prov[0].page_no
        label = str(getattr(item, "label", "")).split(".")[-1].lower()
        kinds[pno][label] += 1
        if isinstance(item, TableItem):
            text[pno].extend(c.text for c in item.data.table_cells)
        elif getattr(item, "text", None):
            text[pno].append(item.text)
    return {k: "".join(v) for k, v in text.items()}, kinds


def run(paths: list[Path]) -> list[dict]:
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    conv = _converter()
    print(f"모델 준비 {time.perf_counter() - t0:.1f}s")
    report = []
    for path in paths:
        print(f"\n▶ {path.name}")
        t = time.perf_counter()
        res = conv.convert(path)
        sec = time.perf_counter() - t
        doc = res.document
        d = OUT / path.stem
        d.mkdir(parents=True, exist_ok=True)
        (d / "doc.md").write_text(doc.export_to_markdown(), encoding="utf-8")
        doc.save_as_html(d / "doc.html")
        doc.save_as_json(d / "doc.json")

        pdf, (dl, kinds) = _pdf_pages(path), _docling_pages(doc)
        pages = []
        for pno, ref in pdf.items():
            f = fidelity(ref, dl.get(pno, ""))
            pages.append({"page": pno, **(f or {"recall": None}), "kinds": dict(kinds.get(pno, {}))})
        total = Counter()
        for c in kinds.values():
            total.update(c)
        rec = [p["recall"] for p in pages if p.get("recall") is not None]
        row = {"file": path.name, "pages": len(pdf), "seconds": round(sec, 1),
               "sec_per_page": round(sec / max(len(pdf), 1), 2), "status": str(res.status),
               "min_recall": min(rec) if rec else None,
               "avg_recall": round(sum(rec) / len(rec), 4) if rec else None,
               "kinds": dict(total), "by_page": pages}
        report.append(row)
        print(f"  {len(pdf)}쪽 {sec:.1f}s ({row['sec_per_page']}s/쪽) · 글자 일치 평균 {row['avg_recall']} 최저 {row['min_recall']}")
        print(f"  요소: {dict(total)}")
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n결과: {OUT}")
    return report


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    paths = [Path(a) for a in args] or [ROOT / s for s in SAMPLES]
    missing = [p for p in paths if not p.exists()]
    if missing:
        print("없는 파일:", *missing, sep="\n  ")
        return 1
    run(paths)
    return 0


if __name__ == "__main__":
    sys.exit(main())
