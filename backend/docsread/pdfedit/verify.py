"""검증: 고칠 수 있는 글자를 전부 '같은 글자'로 다시 썼을 때 원본과 얼마나 같은가.

  실행: uv run python -m docsread.pdfedit.verify [PDF 경로 ...] [--save]
        (경로를 안 주면 docs/ 의 상품설명서를 12개 건너 하나씩)

글꼴·크기·장평·자간·기준선이 맞으면 다시 쓴 쪽이 원본과 화소 단위로 거의 같다.
- 어긋난 화소 비율: 한쪽에만 있는 글자·선 화소의 비율(200dpi, 가장자리 1화소 번짐은 같은 것으로 봄)
- 위치 오차: 다시 쓴 뒤 각 글자의 시작 위치가 원본에서 벗어난 거리(pt)
--save 를 주면 data/pdfedit_verify/ 에 다시 쓴 PDF 를 남긴다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pymupdf

from ..config import DATA_DIR, DOCS_DIR
from . import engine

OUT = DATA_DIR / "pdfedit_verify"


def _gray(page, dpi=200):
    p = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    return np.frombuffer(p.samples, dtype=np.uint8).reshape(p.h, p.w).astype(int)


def _near(m: np.ndarray) -> np.ndarray:
    """잉크 화소를 상하좌우·대각 1화소 넓힌 것."""
    out = m.copy()
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            out |= np.roll(np.roll(m, dy, 0), dx, 1)
    return out


def _diff(a: np.ndarray, b: np.ndarray) -> float:
    """한쪽에만 있는 잉크 화소의 비율. 가장자리 번짐(1화소 이내)은 같은 것으로 본다."""
    ia, ib = a < 128, b < 128
    only = (ia & ~_near(b < 200)) | (ib & ~_near(a < 200))
    return float(only.sum() / max(1, (ia | ib).sum()))


def check(path: Path, save: bool = False) -> dict:
    src = pymupdf.open(path)
    pdf = pymupdf.open(path)
    rows = []
    for pno in range(len(src)):
        spans = engine.page_spans(src[pno])
        mine = [s for s in spans if s["editable"] and not s["subst"]]
        edits = [{"page": pno, "span": s["id"], "text": s["t"], "was": s["t"]} for s in mine]
        report = engine.apply(pdf, edits)
        a, b = _gray(src[pno]), _gray(pdf[pno])
        bad = _diff(a, b)
        # 위치 오차: 다시 쓴 글자의 시작 위치 비교(같은 순서로 나온다고 보지 않고 글자+가까운 위치로 짝짓는다)
        after = {}
        for s in engine.page_spans(pdf[pno]):
            for c in s["chars"]:
                after.setdefault(c["c"], []).append(c["origin"])
        errs = []
        for s in mine:
            for c in s["chars"]:
                if not c["c"].strip():
                    continue
                cand = after.get(c["c"], [])
                if cand:
                    errs.append(min(abs(o[0] - c["origin"][0]) + abs(o[1] - c["origin"][1]) for o in cand))
        n_all = sum(len(s["t"].strip()) for s in spans)
        rows.append({"page": pno, "spans": len(spans), "rewritten": len(mine),
                     "char_share": round(sum(len(s["t"].strip()) for s in mine) / max(1, n_all), 4),
                     "pixel_diff": round(bad, 4),
                     "pos_err_median": round(float(np.median(errs)), 3) if errs else None,
                     "pos_err_p99": round(float(np.percentile(errs, 99)), 3) if errs else None,
                     "errors": [r for r in report if r["level"] == "error"][:3]})
    if save:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f"{path.stem}_다시쓰기.pdf").write_bytes(engine.finish(pdf))
    return {"file": path.name, "pages": rows}


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    save = "--save" in args
    paths = [Path(a) for a in args if a != "--save"]
    if not paths:
        paths = sorted(p for p in DOCS_DIR.rglob("*.pdf") if "상품설명서" in p.name)[::12]
    if not engine.fonts_ready():
        print("글꼴이 없습니다. README 의 글꼴 준비를 먼저 하세요(python -m docsread.pdfedit.fonts)")
        return 1
    worst = 0.0
    for p in paths:
        r = check(p, save)
        for row in r["pages"]:
            worst = max(worst, row["pixel_diff"])
            print(f"{r['file'][:44]:44} {row['page'] + 1}쪽 | 다시 쓴 글자 {row['char_share']:.0%} | 어긋난 화소 {row['pixel_diff']:.2%} | "
                  f"위치 오차 중앙 {row['pos_err_median']}pt · 99% {row['pos_err_p99']}pt" + (f" | 오류 {row['errors']}" if row["errors"] else ""))
    print(f"\n가장 나쁜 쪽의 어긋난 화소: {worst:.2%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
