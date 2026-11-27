"""원본 PDF 쪽 위에서 글자·그림을 고친다(다시 조판하지 않는다).

- 원본 PDF 는 읽기만 한다. 고친 내용(edits)은 따로 저장하고, 볼 때마다 원본에 다시 입힌다.
- 글자: 고칠 조각(span)만 지우고 같은 자리에 다시 쓴다. 조각마다 원본에서 잰 값을 그대로 쓴다.
    · 글꼴·굵기   PDF 글꼴 이름 → 받아 둔 전체 글꼴(data/fonts/ttf). 없는 글꼴은 가까운 굵기의 본고딕으로 대체(표시함)
    · 크기·색     조각의 값 그대로
    · 장평         글자 상자 폭 ÷ 글꼴의 글자 폭(가로 비율). 원본은 대개 94~98%
    · 자간         (다음 글자 시작 - 이 글자 시작) - 장평 × 글꼴 글자 폭 의 중앙값
    · 띄어쓰기 폭  빈칸에서 잰 값(양쪽 맞춤 문단은 줄마다 다르다)
    · 기준선       조각의 기준선(origin) 그대로
- 길이가 달라지면 같은 줄의 뒤 조각을 밀고(왼쪽 맞춤), 가운데·오른쪽 맞춤이면 줄 전체를 다시 놓는다.
- 칸을 넘으면 자간 → 장평 순으로 조금 줄여 맞추고(fit), 그래도 넘으면 경고한다.
- 그림: 같은 자리·같은 크기·같은 기울기로 바꿔 끼운다(비율이 다르면 가운데를 잘라 맞춘다. 원본의 오려 낸 모양은 버린다).
"""
from __future__ import annotations

import statistics
from pathlib import Path

import pymupdf

from ..config import ASSET_DIR
from .fonts import TTF_DIR

FLAGS = pymupdf.TEXTFLAGS_RAWDICT | pymupdf.TEXT_PRESERVE_WHITESPACE
WEIGHTS = ("Light", "Normal", "Regular", "Medium", "Bold", "Heavy")
# 칸을 넘을 때 줄일 수 있는 한도(글자 크기 대비 자간, 장평 배율)
FIT_TRACK = 0.05
FIT_SCALE = 0.88

_fonts: dict[str, pymupdf.Font] = {}


def fonts_ready() -> list[str]:
    return [w for w in WEIGHTS if (TTF_DIR / f"SourceHanSansKR-{w}.ttf").exists()]


def _weight_of(name: str) -> tuple[str, bool]:
    """PDF 글꼴 이름 → (본고딕 굵기, 대체 여부)."""
    tail = name.split("-")[-1]
    if "SourceHanSans" in name and tail in WEIGHTS:
        return tail, False
    low = name.lower()
    for key, w in (("heavy", "Heavy"), ("black", "Heavy"), ("extrabold", "Heavy"), ("semibold", "Bold"), ("bold", "Bold"),
                   ("medium", "Medium"), ("extralig", "Light"), ("light", "Light"), ("thin", "Light"),
                   ("regular", "Regular"), ("normal", "Normal")):
        if key in low:
            return w, not ("sourcehansans" in low or "notosans" in low)     # Noto Sans KR/CJK 는 본고딕과 같은 글꼴
    if low.endswith(("otfb", "-b")):
        return "Bold", True
    if low.endswith(("otfl", "-l", "clt")):
        return "Light", True
    return "Regular", True


def font_for(name: str) -> tuple[pymupdf.Font | None, bool]:
    w, subst = _weight_of(name)
    path = TTF_DIR / f"SourceHanSansKR-{w}.ttf"
    if not path.exists():
        return None, subst
    if w not in _fonts:
        _fonts[w] = pymupdf.Font(fontfile=str(path))
    return _fonts[w], subst


def _measure(chars: list[dict], font: pymupdf.Font | None, size: float) -> tuple[float, float, float | None]:
    """(장평, 자간, 띄어쓰기 한 칸의 실제 폭) — 원본 글자 위치에서 잰다."""
    if font is None or not chars:
        return 1.0, 0.0, None
    ratios = []
    for c in chars:
        adv = font.text_length(c["c"], size)
        if c["c"].strip() and adv > 0:
            ratios.append((c["bbox"][2] - c["bbox"][0]) / adv)
    k = statistics.median(ratios) if ratios else 1.0
    if not 0.5 <= k <= 1.5:
        k = 1.0
    gaps, spaces = [], []
    for a, b in zip(chars, chars[1:]):
        step = b["origin"][0] - a["origin"][0]
        if a["c"] == " ":
            spaces.append(step)
        else:
            gaps.append(step - k * font.text_length(a["c"], size))
    t = statistics.median(gaps) if gaps else 0.0
    if abs(t) > size * 0.3:
        t = 0.0
    return k, t, (statistics.median(spaces) if spaces else None)


def page_spans(page) -> list[dict]:
    """쪽의 글자 조각. id 는 원본 PDF 에서의 순번이라 원본이 같으면 늘 같다."""
    out = []
    for bi, b in enumerate(page.get_text("rawdict", flags=FLAGS)["blocks"]):
        if b["type"] != 0:
            continue
        for li, ln in enumerate(b["lines"]):
            horizontal = abs(ln["dir"][0] - 1) < 1e-3 and abs(ln["dir"][1]) < 1e-3
            for sp in ln["spans"]:
                chars = sp["chars"]
                text = "".join(c["c"] for c in chars)
                if not text.strip():
                    continue
                font, subst = font_for(sp["font"])
                k, t, space = _measure(chars, font, sp["size"])
                c = sp["color"]
                out.append({
                    "id": len(out), "line": (bi, li), "t": text, "font": sp["font"], "size": sp["size"],
                    "color": c, "o": tuple(sp["origin"]), "b": tuple(sp["bbox"]),
                    "x0": chars[0]["origin"][0],
                    # 끝 = 마지막 글자 시작 + 그 글자 폭(뒤따르는 자간은 넣지 않는다)
                    "x1": chars[-1]["origin"][0] + (k * font.text_length(chars[-1]["c"], sp["size"]) if font else chars[-1]["bbox"][2] - chars[-1]["origin"][0]),
                    "k": k, "track": t, "space": space, "chars": chars,
                    "editable": horizontal and font is not None, "subst": subst,
                })
    return out


def _advance(sp: dict, font, ch: str, k: float, track: float) -> float:
    if ch == " " and sp["space"] is not None:
        return sp["space"] * (k / sp["k"])
    return k * font.text_length(ch, sp["size"]) + track


def _width(sp: dict, font, text: str, k: float, track: float) -> float:
    """글자열의 폭(마지막 글자 뒤 자간 제외)."""
    if not text:
        return 0.0
    w = sum(_advance(sp, font, ch, k, track) for ch in text[:-1])
    return w + k * font.text_length(text[-1], sp["size"])


def _write(page, sp: dict, text: str, x: float, k: float, track: float, exact: bool = True) -> None:
    """exact: 원래 글자와 앞부분이 같은 동안은 원본의 글자 위치를 그대로 쓴다(커닝 등 글자별 미세 조정까지 보존)."""
    font, _ = font_for(sp["font"])
    c = sp["color"]
    tw = pymupdf.TextWriter(page.rect, color=((c >> 16 & 255) / 255, (c >> 8 & 255) / 255, (c & 255) / 255))
    y = sp["o"][1]
    chars = sp["chars"]
    pos = 0.0     # 장평을 morph 로 주므로 writer 안에서는 장평을 나눈 좌표로 놓는다
    same = exact
    for i, ch in enumerate(text):
        same = same and i < len(chars) and chars[i]["c"] == ch
        if same:
            pos = chars[i]["origin"][0] - sp["x0"]
        tw.append((x + pos / k, y), ch, font=font, fontsize=sp["size"])
        pos += _advance(sp, font, ch, k, track)
    tw.write_text(page, morph=(pymupdf.Point(x, y), pymupdf.Matrix(k, 1)) if abs(k - 1) > 1e-4 else None)


def _edges(page) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]]]:
    """글자가 넘으면 안 되는 경계. (세로 괘선·칸 가장자리 [(x, y0, y1)], 가로 괘선 [(y, x0, x1)])"""
    vert, horiz = [], []
    for d in page.get_drawings():
        r = d.get("rect")
        if d.get("fill") is not None and r is not None and r.height > 2 and r.width > 4:
            vert += [(r.x0, r.y0, r.y1), (r.x1, r.y0, r.y1)]      # 색을 채운 도형(둥근 띠 등)의 좌우 끝
        for it in d["items"]:
            if it[0] == "re":
                r = it[1]
                if r.height > 2:
                    vert += [(r.x0, r.y0, r.y1), (r.x1, r.y0, r.y1)]
                elif r.width > 8:
                    horiz.append(((r.y0 + r.y1) / 2, r.x0, r.x1))
            elif it[0] == "l":
                p, q = it[1], it[2]
                if abs(p.x - q.x) < 0.3 and abs(p.y - q.y) > 2:
                    vert.append((p.x, min(p.y, q.y), max(p.y, q.y)))
                elif abs(p.y - q.y) < 0.3 and abs(p.x - q.x) > 8:
                    horiz.append((p.y, min(p.x, q.x), max(p.x, q.x)))
    return vert, horiz


def _limits(page, spans: list[dict], group: list[dict], edges) -> tuple[float, float]:
    """이 줄이 쓸 수 있는 가로 범위(왼쪽 한계, 오른쪽 한계)."""
    vert, horiz = edges
    x0, x1 = group[0]["x0"], group[-1]["x1"]
    size = group[0]["size"]
    y = group[0]["o"][1] - size * 0.35
    ids = {s["id"] for s in group}
    left, right = 0.0, page.rect.width
    for ex, ey0, ey1 in vert:
        if ey0 - 0.5 <= y <= ey1 + 0.5:
            if ex >= x1 - 0.6:
                right = min(right, ex)
            elif ex <= x0 + 0.6:
                left = max(left, ex)
    # 세로줄 없는 표: 바로 위아래 가로줄의 끝이 칸의 끝이다
    for hy, hx0, hx1 in horiz:
        if abs(hy - y) <= size * 2.2 and hx0 <= x0 + 0.6 and hx1 >= x1 - 0.6:
            left, right = max(left, hx0), min(right, hx1)
    for s in spans:
        if s["id"] in ids or not (s["b"][1] < y < s["b"][3]):
            continue
        if s["x0"] >= x1 - 0.3:
            right = min(right, s["x0"])
        elif s["x1"] <= x0 + 0.3:
            left = max(left, s["x1"])
    return left, right


def line_groups(spans: list[dict]) -> dict[int, list[dict]]:
    """조각 id → 같은 줄에서 서로 붙어 있는 조각 묶음(사이가 글자 크기보다 좁음). 길이가 바뀌면 묶음째 다시 놓는다."""
    lines: dict[tuple, list[dict]] = {}
    for s in spans:
        lines.setdefault(s["line"], []).append(s)
    out: dict[int, list[dict]] = {}
    for line in lines.values():
        line.sort(key=lambda s: s["x0"])
        cur = [line[0]]
        for s in line[1:] + [None]:
            if s is not None and s["x0"] - cur[-1]["x1"] < max(s["size"], cur[-1]["size"]) * 1.2:
                cur.append(s)
                continue
            for c in cur:
                out[c["id"]] = cur
            cur = [s]
    return out


def guess_aligns(page, spans: list[dict], groups: dict[int, list[dict]], edges) -> dict[int, str]:
    """조각 id → 원본의 맞춤(left·center·right) 짐작.

    같은 글 상자의 여러 줄이 왼쪽 끝이 같으면 왼쪽, 가운데가 같으면 가운데. 한 줄뿐이면 칸 안에서의 좌우 여백으로 본다.
    """
    blocks: dict[int, list[list[dict]]] = {}
    seen = set()
    for s in spans:
        g = groups[s["id"]]
        if id(g) not in seen:
            seen.add(id(g))
            blocks.setdefault(s["line"][0], []).append(g)
    out: dict[int, str] = {}
    every = [g for gs in blocks.values() for g in gs]
    for gs in blocks.values():
        align = None
        if len(gs) >= 2:
            x0 = [g[0]["x0"] for g in gs]
            x1 = [g[-1]["x1"] for g in gs]
            mid = [(a + b) / 2 for a, b in zip(x0, x1)]
            spread = lambda v: max(v) - min(v)
            if spread(x0) < 0.6:
                align = "left"
            elif spread(mid) < 0.8:
                align = "center"
            elif spread(x1) < 0.6:
                align = "right"
        for g in gs:
            a = align
            if a is None:       # 같은 열에 쌓인 다른 줄들과 견준다: 가운데가 같고 왼쪽 끝이 다르면 가운데 맞춤
                x0, x1 = g[0]["x0"], g[-1]["x1"]
                vote = {"left": 0, "center": 0, "right": 0}
                for o in every:
                    if o is g or abs(o[0]["o"][1] - g[0]["o"][1]) > 120:
                        continue
                    ox0, ox1 = o[0]["x0"], o[-1]["x1"]
                    if abs((ox1 - ox0) - (x1 - x0)) < 1.5:
                        continue        # 길이가 같으면 구분이 안 된다
                    if abs((ox0 + ox1) / 2 - (x0 + x1) / 2) < 0.5:
                        vote["center"] += 1
                    elif abs(ox0 - x0) < 0.3:
                        vote["left"] += 1
                    elif abs(ox1 - x1) < 0.3:
                        vote["right"] += 1
                best = max(vote, key=vote.get)
                if vote[best] >= 2:
                    a = best
            if a is None:
                left, right = _limits(page, spans, g, edges)
                gl, gr = g[0]["x0"] - left, right - g[-1]["x1"]
                if gl > 1.5 and abs(gl - gr) <= max(1.0, 0.03 * (right - left)):
                    a = "center"
                elif gr < 3 and gl > gr * 4 + 4:
                    a = "right"
                else:
                    a = "left"
            for s in g:
                out[s["id"]] = a
    return out


def _apply_text(page, edits: dict[int, dict], report: list[dict]) -> None:
    spans = page_spans(page)
    by_id = {s["id"]: s for s in spans}
    todo: list[tuple[dict, str, float, float, float]] = []      # (조각, 글자, x, 장평, 자간)
    groups_of = line_groups(spans)
    edges = _edges(page)
    aligns = None
    seen_groups = set()
    for sid, e in edits.items():
        sp = by_id.get(sid)
        if sp is None or not sp["editable"]:
            report.append({"span": sid, "level": "error", "msg": "고칠 수 없는 글자입니다(세로쓰기이거나 글꼴이 없음)"})
            continue
        if "was" in e and e["was"] != sp["t"]:
            report.append({"span": sid, "level": "error", "msg": "원본 글자가 저장 때와 다릅니다"})
            continue
        group = groups_of[sid]
        if id(group) in seen_groups:
            continue
        seen_groups.add(id(group))
        changed = [s for s in group if s["id"] in edits]
        if any(not s["editable"] for s in group[group.index(changed[0]):]):
            report.append({"span": changed[0]["id"], "level": "warn", "msg": "같은 줄에 옮길 수 없는 글자가 있어 그 글자는 제자리에 둡니다"})
        align = next((edits[s["id"]].get("align") for s in changed if edits[s["id"]].get("align")), None)
        if align is None:
            if aligns is None:
                aligns = guess_aligns(page, spans, groups_of, edges)
            align = aligns[changed[0]["id"]]
        texts = {s["id"]: (edits[s["id"]]["text"] if s["id"] in edits else s["t"]) for s in group}
        gaps = [b["x0"] - a["x1"] for a, b in zip(group, group[1:])]
        left, right = _limits(page, spans, group, edges)
        old_x0, old_x1 = group[0]["x0"], group[-1]["x1"]

        olds = {s["id"]: s["t"] for s in group}

        def total(dt: float, ds: float, tx=None) -> float:
            tx = texts if tx is None else tx
            return sum(_width(s, font_for(s["font"])[0], tx[s["id"]], s["k"] * ds, s["track"] - dt * s["size"])
                       for s in group if s["editable"]) + sum(gaps)

        # 길이 변화는 '같은 방식으로 잰 원래 길이'와 견준다(글자를 안 바꾸면 변화 0 → 제자리)
        base, span_w = total(0.0, 1.0, olds), old_x1 - old_x0

        def start(grow: float) -> float:
            if align == "center":
                return old_x0 - grow / 2
            return old_x0 - grow if align == "right" else old_x0

        dt, ds = 0.0, 1.0
        grow = total(dt, ds) - base
        if align == "center":       # 가운데 맞춤은 양쪽으로 같은 만큼만 늘 수 있다
            room = 2 * min((old_x0 + old_x1) / 2 - left, right - (old_x0 + old_x1) / 2) - 1.0
        else:
            room = right - 0.5 - old_x0 if align == "left" else old_x1 - left - 0.5
        fitted = False
        if grow > 0.05 and span_w + grow > room:
            for step in range(1, 11):
                dt = FIT_TRACK * step / 10
                if span_w + total(dt, ds) - base <= room:
                    break
            else:
                for step in range(1, 13):
                    ds = 1 - (1 - FIT_SCALE) * step / 12
                    if span_w + total(dt, ds) - base <= room:
                        break
            grow = total(dt, ds) - base
            fitted = True
        first = changed[0]["id"]
        if grow > 0.05 and span_w + grow > room + 0.05:
            report.append({"span": first, "level": "warn", "msg": f"칸을 {span_w + grow - room:.1f}pt 넘습니다. 문구를 줄이세요"})
        elif fitted:
            report.append({"span": first, "level": "info", "msg": f"칸에 맞추려고 자간 -{dt * 100:.0f}%" + (f", 장평 {ds * 100:.0f}%" if ds < 1 else "") + " 줄였습니다"})
        x = start(grow)
        for i, s in enumerate(group):
            if s["editable"]:
                k, tr = s["k"] * ds, s["track"] - dt * s["size"]
                moved = abs(x - s["x0"]) > 0.01 or s["id"] in edits or fitted
                if moved:
                    todo.append((s, texts[s["id"]], x, k, tr))
                same = texts[s["id"]] == s["t"] and not fitted
                x += (s["x1"] - s["x0"]) if same else _width(s, font_for(s["font"])[0], texts[s["id"]], k, tr)
            else:
                x = s["x1"]
            if i < len(gaps):
                x += gaps[i]
    if not todo:
        return
    for sp, *_ in todo:      # 글자만 지운다. 범위는 글줄 가운데의 얇은 띠(글자 상자 전체면 위아래 줄까지 지워진다)
        mid = sp["o"][1] - sp["size"] * 0.35
        page.add_redact_annot(pymupdf.Rect(sp["b"][0] + 0.2, mid - 0.4, sp["b"][2] - 0.2, mid + 0.4), fill=False, cross_out=False)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
    for sp, text, x, k, tr in todo:
        if text:
            _write(page, sp, text, x, k, tr, exact=abs(k - sp["k"]) < 1e-6 and abs(tr - sp["track"]) < 1e-6)
        if sp["subst"] and sp["id"] in edits:
            report.append({"span": sp["id"], "level": "info", "msg": f"원본 글꼴({sp['font']})이 없어 본고딕으로 썼습니다"})


def page_images(page) -> list[dict]:
    """쪽의 그림. b 는 화면에서 차지하는 범위, ratio 는 그림 틀의 가로÷세로(돌려 놓은 그림은 범위와 다르다)."""
    out = []
    for img in page.get_images(full=True):
        xref = img[0]
        for r, m in page.get_image_rects(xref, transform=True):
            if r.width > 4 and r.height > 4:
                w, h = (m.a ** 2 + m.b ** 2) ** 0.5, (m.c ** 2 + m.d ** 2) ** 0.5
                out.append({"xref": xref, "b": (r.x0, r.y0, r.x1, r.y1), "w": img[2], "h": img[3],
                            "ratio": w / h if h else 1.0, "rotated": abs(m.b) > 0.01 or abs(m.c) > 0.01})
    return out


def _fit_image(path: Path, ratio: float) -> pymupdf.Pixmap:
    """바꿔 끼울 그림을 원래 틀의 가로세로 비율로 가운데 자른다."""
    pix = pymupdf.Pixmap(str(path))
    w, h = pix.width, pix.height
    if w / h > ratio * 1.01:
        nw = max(1, round(h * ratio))
        clip = pymupdf.IRect((w - nw) // 2, 0, (w - nw) // 2 + nw, h)
    elif w / h < ratio / 1.01:
        nh = max(1, round(w / ratio))
        clip = pymupdf.IRect(0, (h - nh) // 2, w, (h - nh) // 2 + nh)
    else:
        return pix
    out = pymupdf.Pixmap(pix.colorspace, clip, pix.alpha)
    out.copy(pix, clip)
    out.set_origin(0, 0)
    return out


def apply(pdf: pymupdf.Document, edits: list[dict], only_page: int | None = None) -> list[dict]:
    """edits 를 pdf(원본을 연 것, 메모리 안에서만 바뀐다)에 입힌다. 반환: 알림 목록.

    edit: {"page": 0, "span": 12, "text": "새 글자", "was": "원래 글자", "align": "left|center|right"}
          {"page": 0, "image": xref, "asset": "<sha256>"}
    """
    report: list[dict] = []
    pages = sorted({e["page"] for e in edits if only_page is None or e["page"] == only_page})
    for pno in pages:
        if not 0 <= pno < len(pdf):
            continue
        page = pdf[pno]
        mine = [e for e in edits if e["page"] == pno]
        text = {int(e["span"]): e for e in mine if "span" in e}
        before = len(report)
        if text:
            _apply_text(page, text, report)
        for e in mine:
            if "image" not in e:
                continue
            path = ASSET_DIR / f"{e['asset']}.png"
            xref = int(e["image"])
            target = next((im for im in page_images(page) if im["xref"] == xref), None)
            if not path.exists() or target is None:
                report.append({"image": e["image"], "level": "error", "msg": "그림을 찾을 수 없습니다"})
                continue
            pix = _fit_image(path, target["ratio"])
            page.replace_image(xref, pixmap=pix)
            if not pix.alpha:       # 원본의 투명 마스크(오려 낸 모양)가 새 그림에 남지 않게 한다. 새 그림이 투명 배경이면 그 모양을 쓴다
                page.parent.xref_set_key(xref, "SMask", "null")
        for r in report[before:]:
            r["page"] = pno
    return report


# ── 표 행 추가·삭제 ────────────────────────────────────────────────────────────────────
# 원본 쪽을 가로 띠로 잘라 다시 쌓는다. 행 추가 = 기존 행 띠를 복제해 바로 아래에 끼움(글자만 새로 씀),
# 행 삭제 = 그 띠를 뺌. 늘거나 준 높이는 정해진 범위(scope) 안에서 세로 비율을 조금 바꿔 흡수하므로
# 범위 밖은 전혀 움직이지 않는다.
#   scope "page"  표가 쪽 너비를 다 쓰는 경우: 쪽 전체에서 흡수(한 행에 약 1%)
#   scope "table" 표 옆에 다른 내용이 있는 경우: 그 표 안에서만 흡수
# 행 조작(op)도 원본 좌표로 저장한다: {"page", "op": "dup"|"del", "id", "y0", "y1", "x0", "x1", "ya", "yb", "scope"}
# 복제한 행의 글자 고침은 글자 edit 에 "row": <op id> 를 붙인다(span 은 본이 된 원본 조각).
SQUEEZE_WARN = 0.92


def _mid(sp: dict) -> float:
    return sp["o"][1] - sp["size"] * 0.35


def find_row(page, sp: dict, edges=None) -> dict | None:
    """조각이 놓인 표 행의 범위. 표의 가로줄 사이를 한 행으로 본다(병합 칸의 글자를 고르면 그 칸 높이 전체)."""
    _, horiz = edges or _edges(page)
    mid = _mid(sp)
    over = [h for h in horiz if h[1] < sp["x1"] and h[2] > sp["x0"]]
    ups, downs = [h[0] for h in over if h[0] < mid], [h[0] for h in over if h[0] > mid]
    if not ups or not downs:
        return None
    y0, y1 = max(ups), min(downs)
    if not 3 <= y1 - y0 <= 260:
        return None

    def extent(y: float):
        merged: list[list[float]] = []
        for a, b in sorted((h[1], h[2]) for h in horiz if abs(h[0] - y) < 0.3):
            if merged and a - merged[-1][1] <= 6:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        cx = (sp["x0"] + sp["x1"]) / 2
        return next(((a, b) for a, b in merged if a - 1 <= cx <= b + 1), None)

    ext = extent(y1) or extent(y0)
    if ext is None:
        return None
    xa, xb = ext
    ys = sorted({round(h[0], 1) for h in horiz if h[1] < xb and h[2] > xa})
    gaps = [b - a for a, b in zip(ys, ys[1:])]
    lim = max(3 * statistics.median(gaps), 30) if gaps else 30
    i0 = min(range(len(ys)), key=lambda i: abs(ys[i] - y0))
    i1 = min(range(len(ys)), key=lambda i: abs(ys[i] - y1))
    while i0 > 0 and ys[i0] - ys[i0 - 1] <= lim:
        i0 -= 1
    while i1 < len(ys) - 1 and ys[i1 + 1] - ys[i1] <= lim:
        i1 += 1
    ya, yb = ys[i0], ys[i1]
    # 행 띠는 표 전체 너비로 자른다(제목 칸처럼 이 행의 가로줄이 닿지 않는 열도 함께 늘어나야 한다)
    for _ in range(3):
        for h in horiz:
            if ya - 0.3 <= h[0] <= yb + 0.3 and h[1] <= xb + 6 and h[2] >= xa - 6:
                xa, xb = min(xa, h[1]), max(xb, h[2])
    return {"y0": round(y0, 2), "y1": round(y1, 2), "x0": round(xa, 2), "x1": round(xb, 2),
            "ya": ya, "yb": yb, "scope": "page" if xb - xa >= 0.85 * page.rect.width else "table"}


def _row_members(spans: list[dict], horiz, op: dict) -> tuple[list[dict], list[dict]]:
    """(그 행의 글자, 행을 지나가는 병합 칸의 글자) — 병합 칸 글자는 복제한 행에 따라오면 안 된다."""
    own, passing = [], []
    for s in spans:
        if not (op["y0"] < _mid(s) < op["y1"] and op["x0"] - 1 <= (s["x0"] + s["x1"]) / 2 <= op["x1"] + 1):
            continue
        over = [h[0] for h in horiz if h[1] < s["x1"] and h[2] > s["x0"]]
        up = max((y for y in over if y < _mid(s)), default=-1e9)
        down = min((y for y in over if y > _mid(s)), default=1e9)
        (own if up >= op["y0"] - 0.3 and down <= op["y1"] + 0.3 else passing).append(s)
    return own, passing


def _op_groups(rect, ops: list[dict]) -> list[tuple[pymupdf.Rect, list[dict]]]:
    """행 조작을 흡수 범위별로 묶는다. 표 범위 묶음을 먼저, 쪽 범위 묶음을 마지막에 처리한다."""
    groups: list[list] = []
    for o in (o for o in ops if o["scope"] != "page"):
        r = pymupdf.Rect(o["x0"], o["ya"], o["x1"], o["yb"])
        for g in groups:
            if g[0].intersects(r):
                g[0] |= r
                g[1].append(o)
                break
        else:
            groups.append([r, [o]])
    paged = [o for o in ops if o["scope"] == "page"]
    if paged:
        groups.append([pymupdf.Rect(rect), paged])
    return [(g[0], g[1]) for g in groups]


def _parts(R, ops: list[dict]) -> tuple[list[dict], float]:
    """범위 R 안을 위에서부터 다시 쌓은 띠 목록과 전체 높이."""
    cuts = sorted({R.y0, R.y1} | {min(max(v, R.y0), R.y1) for o in ops for v in (o["y0"], o["y1"])})
    dels = [o for o in ops if o["op"] == "del"]
    dups = [o for o in ops if o["op"] == "dup"]
    parts, off = [], 0.0
    for a, b in zip(cuts, cuts[1:]):
        if b - a < 0.01:
            continue
        m = (a + b) / 2
        if not any(o["y0"] < m < o["y1"] for o in dels):
            parts.append({"kind": "src", "a": a, "b": b, "off": off})
            off += b - a
        for o in dups:
            if abs(o["y1"] - b) < 0.01:
                parts.append({"kind": "dup", "op": o, "a": o["y0"], "b": o["y1"], "off": off})
                off += o["y1"] - o["y0"]
    return parts, off


class _Map:
    """한 묶음의 좌표 변환(원래 y → 다시 쌓은 뒤 y). 범위 밖은 그대로."""

    def __init__(self, R, ops):
        self.R, self.ops = R, ops
        self.parts, self.total = _parts(R, ops)
        self.k = R.height / self.total if self.total else 1.0

    def inside(self, cx: float, y: float) -> bool:
        return self.R.x0 - 1 <= cx <= self.R.x1 + 1 and self.R.y0 <= y <= self.R.y1

    def deleted(self, cx: float, y: float) -> bool:
        return self.inside(cx, y) and any(o["op"] == "del" and o["y0"] < y < o["y1"] for o in self.ops)

    def _part(self, y: float, op_id=None):
        for p in self.parts:
            if op_id is not None:
                if p["kind"] == "dup" and p["op"]["id"] == op_id:
                    return p
            elif p["kind"] == "src" and p["a"] - 0.01 <= y <= p["b"] + 0.01:
                return p
        return None

    def box(self, b, cx: float, y: float, op_id=None):
        """상자 b(x0,y0,x1,y1)를 옮긴다. y 는 상자가 속한 띠를 정하는 기준 높이."""
        if not self.inside(cx, y):
            return b
        p = self._part(y, op_id)
        if p is None:
            return None
        f = lambda v: self.R.y0 + (p["off"] + v - p["a"]) * self.k
        return (b[0], f(b[1]), b[2], f(b[3]))


def _map_pos(m: _Map, y: float) -> float:
    """원래 높이 y 가 다시 쌓은 뒤 어디에 오는가(쪽 좌표). 지운 띠 안의 점은 그 띠가 있던 자리로 모인다."""
    total = 0.0
    for p in m.parts:
        h = p["b"] - p["a"]
        if p["kind"] == "dup":
            total += h if p["op"]["y1"] <= y + 0.01 else 0.0
        elif y >= p["b"]:
            total += h
        elif y > p["a"]:
            total += y - p["a"]
    return m.R.y0 + total * m.k


def _labels(spans: list[dict], horiz, m: _Map) -> dict[int, tuple[float, float]]:
    """행 조작에 걸친 병합 칸의 글자(여러 행에 걸친 제목) → 그 칸의 (위, 아래) 가로줄.

    이런 글자는 띠와 함께 잘리거나 복제되면 안 되고, 조작 뒤의 칸 가운데로 옮겨야 한다.
    """
    out = {}
    for s in spans:
        cx, y = (s["x0"] + s["x1"]) / 2, _mid(s)
        if not m.inside(cx, y):
            continue
        over = [h[0] for h in horiz if h[1] < s["x1"] and h[2] > s["x0"]]
        ups, downs = [v for v in over if v < y], [v for v in over if v > y]
        if not ups or not downs:
            continue
        u, d = max(ups), min(downs)
        if d - u > 320:
            continue
        for o in m.ops:
            if not (o["x0"] - 1 <= cx <= o["x1"] + 1):
                continue
            overlaps = u < o["y1"] - 0.3 and d > o["y0"] + 0.3
            contained = u >= o["y0"] - 0.3 and d <= o["y1"] + 0.3
            if overlaps and not contained:
                out[s["id"]] = (u, d)
                break
    return out


def _label_move(m: _Map, sp: dict, u: float, d: float):
    """병합 칸 제목의 (원래 글자 상자, 옮긴 뒤 상자). 칸이 없어졌으면 None."""
    nu, nd = _map_pos(m, u), _map_pos(m, d)
    if nd - nu < sp["size"] * 0.9:
        return None
    c0, c1 = (u + d) / 2, (nu + nd) / 2
    top, bot = sp["o"][1] - sp["size"] * 0.95, sp["o"][1] + sp["size"] * 0.3
    return (top, bot), (c1 + (top - c0) * m.k, c1 + (bot - c0) * m.k)


def _row_ops(edits: list[dict], pno: int) -> list[dict]:
    return [e for e in edits if "op" in e and e["page"] == pno]


def layout(page, spans: list[dict], images: list[dict], edits: list[dict], pno: int) -> tuple[list[dict], list[dict]]:
    """화면에 놓을 글자 조각·그림(행 조작 뒤의 위치). 조각에 "row"(0 = 원본, 그 밖 = 복제한 행의 op id)와 "view" 상자를 붙인다."""
    ops = _row_ops(edits, pno)
    if not ops:
        return [{**s, "row": 0, "view": s["b"]} for s in spans], [{**im, "view": im["b"]} for im in images]
    _, horiz = _edges(page)
    maps = [_Map(R, g) for R, g in _op_groups(page.rect, ops)]
    out = []

    def through(b, cx, y, start=0):
        for m in maps[start:]:
            nb = m.box(b, cx, y)
            if nb is None:
                return None
            y += nb[1] - b[1]
            b = nb
        return b

    labels = [_labels(spans, horiz, m) for m in maps]
    for s in spans:
        cx, y = (s["x0"] + s["x1"]) / 2, _mid(s)
        b, gone = s["b"], False
        for m, lab in zip(maps, labels):
            if s["id"] in lab:
                mv = _label_move(m, s, *lab[s["id"]])
                if mv is None:
                    gone = True
                    break
                (top, _), (ntop, nbot) = mv
                dy = ntop - top
                b = (b[0], b[1] + dy, b[2], b[3] + dy)
                y += dy
            else:
                if m.deleted(cx, y):
                    gone = True
                    break
                nb = m.box(b, cx, y)
                if nb is None:
                    gone = True
                    break
                y += nb[1] - b[1]
                b = nb
        if not gone:
            out.append({**s, "row": 0, "view": b})
    for i, m in enumerate(maps):
        for o in m.ops:
            if o["op"] != "dup":
                continue
            for s in _row_members(spans, horiz, o)[0]:
                if s["id"] in labels[i]:
                    continue
                cx, y = (s["x0"] + s["x1"]) / 2, _mid(s)
                b = m.box(s["b"], cx, y, o["id"])
                y2 = y + b[1] - s["b"][1]
                b = through(b, cx, y2, i + 1)
                if b is not None:
                    out.append({**s, "row": o["id"], "view": b})
    ims = []
    for im in images:
        cx, y = (im["b"][0] + im["b"][2]) / 2, (im["b"][1] + im["b"][3]) / 2
        if any(m.deleted(cx, y) for m in maps):
            continue
        b = through(im["b"], cx, y)
        if b is not None:
            ims.append({**im, "view": b})
    return out, ims


def _compose(cur: pymupdf.Document, pno: int, m: _Map, dup_docs: dict[int, pymupdf.Document],
             bands: pymupdf.Document | None = None, patches=()) -> pymupdf.Document:
    """cur: 범위 밖을 그릴 문서, bands: 범위 안 띠를 그릴 문서(병합 칸 제목을 지운 것), patches: 옮겨 붙일 제목 조각."""
    R = m.R
    bands = bands or cur
    W, H = cur[pno].rect.width, cur[pno].rect.height
    out = pymupdf.open()
    if pno > 0:
        out.insert_pdf(cur, from_page=0, to_page=pno - 1)
    pg = out.new_page(width=W, height=H)
    for r in (pymupdf.Rect(0, 0, W, R.y0), pymupdf.Rect(0, R.y1, W, H),
              pymupdf.Rect(0, R.y0, R.x0, R.y1), pymupdf.Rect(R.x1, R.y0, W, R.y1)):
        if r.width > 0.01 and r.height > 0.01:
            pg.show_pdf_page(r, cur, pno, clip=r, keep_proportion=False)
    for p in m.parts:
        src = bands if p["kind"] == "src" else dup_docs[p["op"]["id"]]
        dst = pymupdf.Rect(R.x0, R.y0 + p["off"] * m.k, R.x1, R.y0 + (p["off"] + p["b"] - p["a"]) * m.k)
        pg.show_pdf_page(dst, src, pno, clip=pymupdf.Rect(R.x0, p["a"], R.x1, p["b"]), keep_proportion=False)
    for clip, dst in patches:
        pg.show_pdf_page(dst, cur, pno, clip=clip, keep_proportion=False)
    if pno < len(cur) - 1:
        out.insert_pdf(cur, from_page=pno + 1)
    return out


def render(path, edits: list[dict]) -> tuple[pymupdf.Document, list[dict], bool]:
    """원본에 모든 고침(글자·그림·행)을 입힌 문서, 알림, 다시 쌓았는지. 원본 파일은 읽기만 한다."""
    src = pymupdf.open(path)
    plain = [e for e in edits if "op" not in e and not e.get("row")]
    report = apply(src, plain)
    pages = sorted({e["page"] for e in edits if "op" in e and 0 <= e["page"] < len(src)})
    if not pages:
        return src, report, False
    src.subset_fonts()
    cur = src
    with pymupdf.open(path) as pristine:
        for pno in pages:
            page = pristine[pno]
            spans = page_spans(page)
            _, horiz = _edges(page)
            for R, ops in _op_groups(page.rect, _row_ops(edits, pno)):
                m = _Map(R, ops)
                # 병합 칸 제목: 띠에서는 지우고, 다시 쌓은 뒤 새 칸의 가운데에 옮겨 붙인다(첫 묶음에서만)
                labels = _labels(spans, horiz, m) if cur is src else {}
                by_id = {s["id"]: s for s in spans}
                erase = [{"page": pno, "span": i, "text": ""} for i in labels if by_id[i]["editable"]]
                bands, patches = None, []
                if erase:
                    bands = pymupdf.open(path)
                    apply(bands, [e for e in plain if e["page"] == pno and e.get("span") not in labels] + erase)
                    bands.subset_fonts()
                    edges = _edges(page)
                    groups = line_groups(spans)
                    for i, (u, d) in labels.items():
                        sp = by_id[i]
                        mv = _label_move(m, sp, u, d) if sp["editable"] else None
                        if mv is None:
                            continue
                        (top, bot), (ntop, nbot) = mv
                        left, right = _limits(page, spans, groups[i], edges)
                        x0, x1 = max(left + 0.4, sp["b"][0] - 30), min(right - 0.4, sp["b"][2] + 30)
                        patches.append((pymupdf.Rect(x0, top, x1, bot), pymupdf.Rect(x0, ntop, x1, nbot)))
                dup_docs = {}
                for o in ops:
                    if o["op"] != "dup":
                        continue
                    own, passing = _row_members(spans, horiz, o)
                    own = [s for s in own if s["id"] not in labels]
                    passing = passing + [by_id[i] for i in labels if by_id[i] not in passing]
                    own_ids = {s["id"] for s in own}
                    row_edits = [{"page": pno, "span": e["span"], "text": e["text"], **({"align": e["align"]} if e.get("align") else {})}
                                 for e in edits if e.get("row") == o["id"] and e["page"] == pno and e.get("span") in own_ids]
                    row_edits += [{"page": pno, "span": s["id"], "text": ""} for s in passing if s["editable"]]
                    row_edits += [e for e in plain if "image" in e and e["page"] == pno]
                    d = pymupdf.open(path)
                    for r in apply(d, row_edits):
                        if r.get("span") in own_ids:
                            report.append({**r, "row": o["id"]})
                    d.subset_fonts()
                    dup_docs[o["id"]] = d
                where = "쪽 전체" if R.height >= page.rect.height - 1 else "이 표"
                if m.k < SQUEEZE_WARN:
                    report.append({"page": pno, "level": "warn", "msg": f"행이 늘어 {where}가 세로로 {(1 - m.k) * 100:.0f}% 눌립니다. 다른 행을 지우거나 추가한 행을 줄이세요"})
                elif abs(m.k - 1) > 0.002:
                    report.append({"page": pno, "level": "info", "msg": f"{where}의 세로 비율을 {m.k * 100:.1f}% 로 맞췄습니다"})
                cur = _compose(cur, pno, m, dup_docs, bands, patches)
    return cur, report, True


def to_bytes(pdf: pymupdf.Document, composed: bool) -> bytes:
    """내려받을 PDF. 다시 쌓은 문서는 글꼴을 이미 줄여 두었다."""
    if not composed:
        pdf.subset_fonts()
    return pdf.tobytes(garbage=4, deflate=True)
