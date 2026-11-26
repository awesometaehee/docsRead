"""원본 PDF 에 글자를 다시 써 넣을 때 쓰는 글꼴 준비.

  실행: uv run python -m docsread.pdfedit.fonts        # data/fonts/*.otf → data/fonts/ttf/*.ttf

원본 문서의 글꼴(본고딕 = Source Han Sans KR)은 PDF 안에 쓰인 글자 조각만 들어 있어 새 글자를 쓸 수 없다.
그래서 공개 배포판(Adobe, SIL OFL)을 data/fonts 에 받아 두고 쓴다.
배포판은 CID 방식 OTF 인데, MuPDF 가 이 방식의 글자 번호를 잘못 적어 한글이 다른 글자로 찍힌다.
윤곽선을 TrueType 으로 바꾼 사본(ttf)을 만들어 쓰면 번호가 어긋나지 않는다(모양 오차 1/1000 em 이하).
"""
from __future__ import annotations

import sys
from pathlib import Path

from fontTools.pens.cu2quPen import Cu2QuPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont, newTable

from ..config import DATA_DIR

FONT_DIR = DATA_DIR / "fonts"
TTF_DIR = FONT_DIR / "ttf"
WEIGHTS = ("Light", "Normal", "Regular", "Medium", "Bold", "Heavy")


def otf_to_ttf(src: Path, dst: Path, max_err: float = 1.0) -> int:
    font = TTFont(str(src))
    order = font.getGlyphOrder()
    glyph_set = font.getGlyphSet()
    glyphs = {}
    for name in order:
        pen = TTGlyphPen(glyph_set)
        glyph_set[name].draw(Cu2QuPen(pen, max_err, reverse_direction=True))
        glyphs[name] = pen.glyph()
    font["loca"] = newTable("loca")
    font["glyf"] = glyf = newTable("glyf")
    glyf.glyphOrder = order
    glyf.glyphs = glyphs
    del font["CFF "]
    if "VORG" in font:
        del font["VORG"]
    glyf.compile(font)
    font["maxp"] = maxp = newTable("maxp")
    maxp.tableVersion = 0x00010000
    maxp.maxZones = 1
    maxp.maxTwilightPoints = maxp.maxStorage = maxp.maxFunctionDefs = maxp.maxInstructionDefs = 0
    maxp.maxStackElements = maxp.maxSizeOfInstructions = 0
    maxp.maxComponentElements = max((len(g.components) for g in glyphs.values() if hasattr(g, "components")), default=0)
    maxp.compile(font)
    post = font["post"]
    post.formatType = 3.0          # 글리프 이름 표는 필요 없다
    post.extraNames, post.mapping, post.glyphOrder = [], {}, order
    font.sfntVersion = "\000\001\000\000"
    dst.parent.mkdir(parents=True, exist_ok=True)
    font.save(str(dst))
    return len(order)


def main(argv=None) -> int:
    only = set(argv or sys.argv[1:])
    missing = []
    for w in WEIGHTS:
        if only and w not in only:
            continue
        src = FONT_DIR / f"SourceHanSansKR-{w}.otf"
        dst = TTF_DIR / f"SourceHanSansKR-{w}.ttf"
        if not src.exists():
            missing.append(src.name)
            continue
        if dst.exists():
            print(f"{dst.name}: 이미 있음")
            continue
        print(f"{dst.name}: 글리프 {otf_to_ttf(src, dst)}개 변환", flush=True)
    for name in missing:
        print("없음:", name, "→ data/fonts 에 받아 두세요 (github.com/adobe-fonts/source-han-sans, SubsetOTF/KR)")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
