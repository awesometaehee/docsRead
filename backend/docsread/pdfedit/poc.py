"""시험: 원본 PDF 의 글자를 같은 글꼴·크기·색·자간으로 바꿔 넣을 수 있는가.

  실행: uv run python -m docsread.pdfedit.poc <문서 id> "원래 글자=>새 글자" ["원래=>새" ...]
  예)   uv run python -m docsread.pdfedit.poc 957 "NEW580DR=>NEW990DR (방송전용)"

- 인자가 문서 id 뿐이면 '같은 글자 다시 쓰기'만 해서 원본과 화소 비교한다(글꼴이 맞는지 확인).
- 원본 PDF 는 읽기만 하고 결과는 data/poc/ 에 따로 쓴다.
- 먼저 `python -m docsread.pdfedit.fonts` 로 data/fonts/ttf 를 만들어 두어야 한다.
- 시험용 코드다. 본고딕(SourceHanSansKR) 글자만 다루고, 글자가 칸을 넘는 경우는 처리하지 않는다.
"""
from __future__ import annotations

import statistics
import sys

import numpy as np
import pymupdf

from ..config import DATA_DIR, DOCS_DIR
from ..db import Document, SessionLocal
from .fonts import TTF_DIR

OUT = DATA_DIR / "poc"
_cache: dict[str, pymupdf.Font] = {}


def font_for(name: str) -> pymupdf.Font | None:
    """PDF 글꼴 이름(SourceHanSansKR-Bold 등) → 받아 둔 전체 글꼴."""
    weight = name.split("-")[-1]
    path = TTF_DIR / f"SourceHanSansKR-{weight}.ttf"
    if "SourceHanSans" not in name or not path.exists():
        return None
    if weight not in _cache:
        _cache[weight] = pymupdf.Font(fontfile=str(path))
    return _cache[weight]


def spans_of(page) -> list[dict]:
    flags = pymupdf.TEXTFLAGS_DICT | pymupdf.TEXT_PRESERVE_WHITESPACE
    return [sp for b in page.get_text("dict", flags=flags)["blocks"] if b["type"] == 0
            for line in b["lines"] for sp in line["spans"] if sp["text"].strip()]


def erase(page, spans: list[dict]) -> None:
    """글자만 지운다(그림·선은 그대로). 범위는 글줄 가운데의 얇은 띠 — 글자 상자 전체로 지우면 위아래 줄까지 지워진다."""
    for sp in spans:
        mid = sp["origin"][1] - sp["size"] * 0.35
        page.add_redact_annot(pymupdf.Rect(sp["bbox"][0] + 0.2, mid - 0.4, sp["bbox"][2] - 0.2, mid + 0.4))
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)


def write(page, sp: dict, text: str, font: pymupdf.Font, center: bool = False) -> tuple[float, float]:
    """원래 글자의 자간을 재서 같은 자간으로 쓴다. 자간 = (원래 폭 - 글꼴 기본 폭) / 글자 수. 반환: (자간, 새 폭)"""
    size, old = sp["size"], sp["text"]
    width = sp["bbox"][2] - sp["bbox"][0]
    track = (width - font.text_length(old, size)) / max(1, len(old.rstrip())) if len(old.strip()) > 1 else 0.0
    if abs(track) > size * 0.25:      # 비정상 값(여러 칸에 걸친 조각 등)은 버린다
        track = 0.0
    c = sp["color"]
    tw = pymupdf.TextWriter(page.rect, color=((c >> 16 & 255) / 255, (c >> 8 & 255) / 255, (c & 255) / 255))
    new_w = font.text_length(text, size) + track * len(text)
    x = (sp["bbox"][0] + sp["bbox"][2]) / 2 - new_w / 2 if center else sp["origin"][0]
    for ch in text:
        tw.append((x, sp["origin"][1]), ch, font=font, fontsize=size)
        x += font.text_length(ch, size) + track
    tw.write_text(page)
    return track, new_w


def main(argv=None) -> int:
    args = argv or sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    with SessionLocal() as s:
        d = s.get(Document, int(args[0]))
        if d is None:
            print("문서를 찾을 수 없습니다:", args[0])
            return 1
        path, name = DOCS_DIR / d.rel_path, (d.product or f"doc{d.id}")
    OUT.mkdir(parents=True, exist_ok=True)

    # (A) 같은 글자 다시 쓰기 → 원본과 화소 비교
    doc = pymupdf.open(path)
    page = doc[0]
    every = spans_of(page)
    mine = [sp for sp in every if font_for(sp["font"])]
    if not mine:
        print("본고딕 글자가 없거나 글꼴이 준비되지 않았습니다(python -m docsread.pdfedit.fonts)")
        return 1
    print(f"글자 조각 {len(every)} 중 본고딕 {len(mine)} "
          f"(글자 수 {sum(len(s['text']) for s in mine) / sum(len(s['text']) for s in every):.0%})")
    erase(page, mine)
    tracks = [write(page, sp, sp["text"], font_for(sp["font"]))[0] / sp["size"] for sp in mine]
    print(f"자간(글자 크기 대비) 중앙값 {statistics.median(tracks):.4f}")
    doc.subset_fonts()                      # 넣은 글꼴에서 쓴 글자만 남긴다(안 하면 파일이 수십 MB)
    same = OUT / f"{name}_같은글자_다시쓰기.pdf"
    doc.save(same, garbage=3, deflate=True)
    a = pymupdf.open(path)[0].get_pixmap(dpi=200, colorspace=pymupdf.csGRAY)
    b = pymupdf.open(same)[0].get_pixmap(dpi=200, colorspace=pymupdf.csGRAY)
    pa = np.frombuffer(a.samples, dtype=np.uint8).reshape(a.h, a.w).astype(int)
    pb = np.frombuffer(b.samples, dtype=np.uint8).reshape(b.h, b.w).astype(int)
    ink = (pa < 200) | (pb < 200)
    print(f"화소 비교(200dpi): 글자·선 화소 중 크게 다른 비율 {((abs(pa - pb) > 80) & ink).sum() / ink.sum():.1%} → {same}")

    # (B) 내용 바꾸기
    jobs = [a.split("=>", 1) for a in args[1:] if "=>" in a]
    if jobs:
        doc = pymupdf.open(path)
        page = doc[0]
        sps = spans_of(page)
        todo = []
        for old, new in jobs:
            sp = next((sp for sp in sps if old in sp["text"] and font_for(sp["font"])), None)
            if sp is None:
                print("못 찾음:", old)
                continue
            todo.append((sp, new))
        erase(page, [sp for sp, _ in todo])
        for sp, new in todo:
            _, w = write(page, sp, new, font_for(sp["font"]), center=True)
            print(f"{sp['text'][:20]!r} → {new[:24]!r} | {sp['font'].split('-')[-1]} {sp['size']:.2f}pt | "
                  f"폭 {sp['bbox'][2] - sp['bbox'][0]:.1f} → {w:.1f}")
        doc.subset_fonts()
        out = OUT / f"{name}_글자교체_시험.pdf"
        doc.save(out, garbage=3, deflate=True)
        print("→", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
