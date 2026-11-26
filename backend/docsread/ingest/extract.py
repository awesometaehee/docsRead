"""PDF 페이지 → 레이아웃 요소(글자·도형·이미지) + 표 격자.

대상 PDF 는 전부 InDesign 에서 내보낸 디지털 원본이라 글자가 벡터로 들어 있다.
따라서 OCR·레이아웃 추정 모델 없이 PDF 내부 값을 그대로 읽어 손실 없이 옮긴다.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from .tables import Table, _hex, find_tables

TEXT_FLAGS = (pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES) | pymupdf.TEXT_PRESERVE_WHITESPACE


@dataclass
class Line:
    bbox: tuple[float, float, float, float]
    spans: list[dict]
    block_no: int
    text: str
    table: int | None = None      # 속한 표 번호
    used: bool = False            # 그림 캡션 등으로 이미 소비됨


@dataclass
class PageData:
    width: float
    height: float
    items: list[dict] = field(default_factory=list)   # 레이아웃 요소
    lines: list[Line] = field(default_factory=list)   # PDF 내부 순서(=대체로 읽기 순서)
    tables: list[Table] = field(default_factory=list)
    images: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _weight(font: str, flags: int) -> int:
    f = font.lower()
    for key, w in (("heavy", 900), ("black", 900), ("extrabold", 800), ("bold", 700), ("medium", 500),
                   ("demilight", 350), ("light", 300), ("thin", 200)):
        if key in f:
            return w
    return 700 if flags & 16 else 400


def _path_d(items) -> str:
    out, cur = [], None
    for it in items:
        op = it[0]
        if op == "l":
            p, q = it[1], it[2]
            if cur is None or abs(cur.x - p.x) > 0.01 or abs(cur.y - p.y) > 0.01:
                out.append(f"M{p.x:.2f} {p.y:.2f}")
            out.append(f"L{q.x:.2f} {q.y:.2f}")
            cur = q
        elif op == "c":
            p, a, b, q = it[1], it[2], it[3], it[4]
            if cur is None or abs(cur.x - p.x) > 0.01 or abs(cur.y - p.y) > 0.01:
                out.append(f"M{p.x:.2f} {p.y:.2f}")
            out.append(f"C{a.x:.2f} {a.y:.2f} {b.x:.2f} {b.y:.2f} {q.x:.2f} {q.y:.2f}")
            cur = q
        elif op == "re":
            r = it[1]
            out.append(f"M{r.x0:.2f} {r.y0:.2f}H{r.x1:.2f}V{r.y1:.2f}H{r.x0:.2f}Z")
            cur = None
        elif op == "qu":
            q = it[1]
            out.append(f"M{q.ul.x:.2f} {q.ul.y:.2f}L{q.ur.x:.2f} {q.ur.y:.2f}"
                       f"L{q.lr.x:.2f} {q.lr.y:.2f}L{q.ll.x:.2f} {q.ll.y:.2f}Z")
            cur = None
    return "".join(out)


def _save_image(doc: pymupdf.Document, xref: int, asset_dir: Path, cache: dict) -> dict | None:
    if xref in cache:
        return cache[xref]
    try:
        pix = pymupdf.Pixmap(doc, xref)
        smask = doc.extract_image(xref).get("smask", 0)
        if pix.colorspace is None:      # 스텐실 마스크는 건너뛴다
            cache[xref] = None
            return None
        if pix.colorspace.n != 3:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        if smask:
            mask = pymupdf.Pixmap(doc, smask)
            if pix.alpha:
                pix = pymupdf.Pixmap(pix, 0)
            if (mask.width, mask.height) == (pix.width, pix.height):
                pix = pymupdf.Pixmap(pix, mask)
        data = pix.tobytes("png")
    except Exception:  # 손상·미지원 이미지는 경고로 남기고 계속한다
        cache[xref] = None
        return None
    sha = hashlib.sha256(data).hexdigest()
    path = asset_dir / f"{sha}.png"
    if not path.exists():
        path.write_bytes(data)
    cache[xref] = {"sha": sha, "w": pix.width, "h": pix.height, "bytes": len(data)}
    return cache[xref]


def extract_page(doc: pymupdf.Document, page: pymupdf.Page, asset_dir: Path, img_cache: dict) -> PageData:
    pd = PageData(page.rect.width, page.rect.height)
    drawings = page.get_drawings()

    # 1) 도형
    for g in drawings:
        d = _path_d(g["items"])
        if not d:
            continue
        r = g["rect"]
        kind = g.get("type") or ""
        data = {"d": d}
        if "f" in kind and g.get("fill") is not None:
            data["fill"] = _hex(g["fill"])
            if (g.get("fill_opacity") or 1) < 1:
                data["fo"] = round(g["fill_opacity"], 3)
            if g.get("even_odd"):
                data["eo"] = 1
        if "s" in kind and g.get("color") is not None:
            data["stroke"] = _hex(g["color"])
            data["sw"] = round(g.get("width") or 1, 3)
            if (g.get("stroke_opacity") or 1) < 1:
                data["so"] = round(g["stroke_opacity"], 3)
            dashes = (g.get("dashes") or "").strip()
            if dashes and dashes != "[] 0":
                data["dash"] = dashes
        if g.get("closePath"):
            data["d"] += "Z"
        pd.items.append({"z": g.get("seqno", 0), "kind": "path", "bbox": (r.x0, r.y0, r.x1, r.y1), "data": data})

    # 2) 이미지 (그리기 순서는 bboxlog 에서 찾는다)
    img_seq: dict[tuple, list[int]] = {}
    for seq, (typ, bb) in enumerate(page.get_bboxlog()):
        if typ == "fill-image":
            img_seq.setdefault(tuple(round(v) for v in bb), []).append(seq)
    for info in page.get_image_info(xrefs=True):
        bb = info["bbox"]
        if not info.get("xref"):
            pd.warnings.append("inline-image-skipped")
            continue
        asset = _save_image(doc, info["xref"], asset_dir, img_cache)
        if asset is None:
            pd.warnings.append("image-unreadable")
            continue
        seqs = img_seq.get(tuple(round(v) for v in bb))
        z = seqs.pop(0) if seqs else 0
        item = {"z": z, "kind": "image", "bbox": tuple(bb),
                "data": {"asset": asset["sha"], "m": [round(v, 4) for v in info["transform"]]}}
        pd.items.append(item)
        pd.images.append({"bbox": tuple(bb), **asset})

    # 3) 글자 (그리기 순서는 texttrace 의 seqno)
    seq_by_origin: dict[tuple, int] = {}
    trace = page.get_texttrace()
    for t in trace:
        if t.get("chars"):
            o = t["chars"][0][2]
            seq_by_origin[(round(o[0]), round(o[1]))] = t["seqno"]
    max_seq = max([i["z"] for i in pd.items] + [t["seqno"] for t in trace] + [0])
    td = page.get_text("dict", flags=TEXT_FLAGS)
    for block in td["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            spans = []
            for s in line["spans"]:
                if not s["text"]:
                    continue
                o = s["origin"]
                z = seq_by_origin.get((round(o[0]), round(o[1])), max_seq + 1)
                spans.append({"t": s["text"], "font": s["font"], "size": round(s["size"], 2),
                              "w": _weight(s["font"], s["flags"]), "color": f"#{s['color']:06x}",
                              "bbox": tuple(round(v, 2) for v in s["bbox"]),
                              "o": (round(o[0], 2), round(o[1], 2)), "z": z})
            if not spans or not "".join(s["t"] for s in spans).strip():
                continue
            lb = line["bbox"]
            direction = tuple(round(v, 3) for v in line.get("dir", (1, 0)))
            pd.lines.append(Line(tuple(lb), spans, block["number"], "".join(s["t"] for s in spans)))
            data = {"spans": [{k: s[k] for k in ("t", "font", "size", "w", "color", "o")}
                              | {"x1": s["bbox"][2]} for s in spans]}
            if direction != (1.0, 0.0):
                data["dir"] = direction
            pd.items.append({"z": min(s["z"] for s in spans), "kind": "text", "bbox": tuple(lb), "data": data})

    # 4) 표: 격자 복원 후 글줄을 칸에 배정
    for t in find_tables(drawings, pd.width, pd.height):
        ti = len(pd.tables)
        hit = 0
        for ln in pd.lines:
            cx, cy = (ln.bbox[0] + ln.bbox[2]) / 2, (ln.bbox[1] + ln.bbox[3]) / 2
            if ln.table is not None or not (t.bbox[0] - 1 <= cx <= t.bbox[2] + 1 and t.bbox[1] - 1 <= cy <= t.bbox[3] + 1):
                continue
            # 글줄이 여러 칸에 걸칠 수 있어 span 중심으로 칸을 찾는다
            for s in ln.spans:
                sx, sy = (s["bbox"][0] + s["bbox"][2]) / 2, (s["bbox"][1] + s["bbox"][3]) / 2
                for c in t.cells:
                    if c.bbox[0] - 0.5 <= sx <= c.bbox[2] + 0.5 and c.bbox[1] - 0.5 <= sy <= c.bbox[3] + 0.5:
                        c.lines.append((ln, s))
                        break
            ln.table = ti
            hit += 1
        # 본표 바로 아래의 사진 묶음(격자 모양 패널)이 본표와 한 표로 붙어 잡힌 경우: 사진이 시작하는 행에서 표를 끊는다.
        # 끊긴 아래쪽 글줄은 표에서 풀려 섹션 제목·문단·사진 캡션으로 다시 읽힌다
        pics = [im["bbox"] for im in pd.images
                if im["bbox"][2] - im["bbox"][0] > 12 and im["bbox"][3] - im["bbox"][1] > 12
                and t.bbox[0] - 2 <= (im["bbox"][0] + im["bbox"][2]) / 2 <= t.bbox[2] + 2
                and t.bbox[1] - 2 <= (im["bbox"][1] + im["bbox"][3]) / 2 <= t.bbox[3] + 2]
        if len(pics) >= 4:
            top, bottom = min(b[1] for b in pics), max(b[3] for b in pics)
            # 사진 그림 상자는 투명 여백 때문에 본표 끝과 조금 겹치기도 한다.
            # 사진 위쪽 근처에서 '어느 칸도 걸치지 않는' 첫 행 경계를 끊는 자리로 삼는다
            cut = next((i for i in range(2, len(t.ys) - 1)
                        if top - 16 <= t.ys[i] <= top + 25 and not any(c.r < i < c.r + c.rs for c in t.cells)), None)
            if cut is not None:
                y_cut = t.ys[cut]
                band_h = t.ys[-1] - y_cut
                pic_area = sum((b[2] - b[0]) * (b[3] - b[1]) for b in pics)
                # 끊긴 아래쪽이 정말 사진 묶음인가: 사진이 그 높이의 대부분에 걸쳐 있고 넓이도 충분해야 한다
                # (상품 사진 몇 장이 든 표는 사진이 일부 행에만 있어 여기서 걸러진다)
                if band_h > 0 and bottom - max(top, y_cut) >= band_h * 0.6 and pic_area >= (t.bbox[2] - t.bbox[0]) * band_h * 0.12:
                    t.cells = [c for c in t.cells if c.r < cut]
                    t.ys = t.ys[:cut + 1]
                    t.bbox = (t.bbox[0], t.bbox[1], t.bbox[2], y_cut)
                    for ln in pd.lines:
                        if ln.table == ti and (ln.bbox[1] + ln.bbox[3]) / 2 > y_cut:
                            ln.table = None
        filled = sum(1 for c in t.cells if c.lines)
        inside = sum(1 for im in pd.images
                     if im["bbox"][2] - im["bbox"][0] > 12 and im["bbox"][3] - im["bbox"][1] > 12
                     and t.bbox[0] - 2 <= (im["bbox"][0] + im["bbox"][2]) / 2 <= t.bbox[2] + 2
                     and t.bbox[1] - 2 <= (im["bbox"][1] + im["bbox"][3]) / 2 <= t.bbox[3] + 2)
        # 사진이 차지하는 넓이: 사진 묶음은 칸 대부분이 사진이고, 사진이 든 표(상품 사진 몇 장)는 넓이가 작다
        t_area = max(1.0, (t.bbox[2] - t.bbox[0]) * (t.bbox[3] - t.bbox[1]))
        img_area = sum((im["bbox"][2] - im["bbox"][0]) * (im["bbox"][3] - im["bbox"][1]) for im in pd.images
                       if im["bbox"][2] - im["bbox"][0] > 12 and im["bbox"][3] - im["bbox"][1] > 12
                       and t.bbox[0] - 2 <= (im["bbox"][0] + im["bbox"][2]) / 2 <= t.bbox[2] + 2
                       and t.bbox[1] - 2 <= (im["bbox"][1] + im["bbox"][3]) / 2 <= t.bbox[3] + 2)
        # 글자가 거의 없는 격자(장식)나 사진 묶음(사진이 글칸만큼 많거나, 4장 이상이 넓이의 20% 이상)은 표가 아니다.
        # 사진 묶음 옆의 안내 글까지 한 격자로 잡히면 글이 칸마다 쪼개지므로 표로 두지 않는다
        if filled < 2 or inside >= filled * 0.8 or (inside >= 4 and img_area >= t_area * 0.2):
            for ln in pd.lines:
                if ln.table == ti:
                    ln.table = None
            continue
        pd.tables.append(t)
    return pd
