"""레이아웃 요소 → 의미 블록(제목·섹션·조·문단·표·그림·꼬리말).

블록은 문서 순서(seq)대로 나열되고 parent_seq 로 '조/섹션 → 문단·표' 계층을 만든다.
문단의 content.runs 는 [{t, b?, c?, s?}] 형태의 서식 조각이며 편집기 문서 모델의 입력이 된다.
"""
from __future__ import annotations

import re
from collections import Counter

from .extract import Line, PageData
from .tables import Table

ARTICLE = re.compile(r"^\s*(제\s*\d+\s*조(?:\s*의\s*\d+)?)\s*[\(\[（【〔]\s*(.*?)\s*[\)\]）】〕]\s*(.*)$")
ADDENDUM = re.compile(r"^\s*부\s*칙\s*$")
MARKER = re.compile(r"^\s*(?:[•※▶◆■□▷★·\-–]|\(?\d{1,2}[.)]\s|[①-⑳➀-➓❶-❿]|\[[^\]]{1,12}\])")
FOOTER = re.compile(r"대표이사|www\.|E-?mail|고객상담|장례행사|장례접수|웨딩상담|가입\s*및\s*상담")
CIRCLED = re.compile(r"[①-⑳]")
_DINGBAT = {chr(0x2780 + i): chr(0x2460 + i) for i in range(10)} | {chr(0x2776 + i): chr(0x2460 + i) for i in range(10)}
DEFAULT_COLORS = {"#231f20", "#000000", "#221e1f", "#58595b"}


def norm(text: str) -> str:
    """검색·비교용 정규화: 장식용 원문자를 표준 원문자로, 공백 정리."""
    text = "".join(_DINGBAT.get(ch, ch) for ch in text if ch >= " " or ch in "\n\t")
    return re.sub(r"[ \t ]+", " ", text).strip()


def _runs(lines: list[Line], body_size: float, pairs=None) -> list[dict]:
    """글줄들을 이어 붙여 서식 run 목록으로 만든다. 줄바꿈은 원본 글자 그대로 잇는다(한글 글자 단위 줄바꿈)."""
    runs: list[dict] = []
    prev_y = None
    seq = pairs if pairs is not None else [(ln, s) for ln in lines for s in ln.spans]
    for ln, s in seq:
        text = "".join(_DINGBAT.get(ch, ch) for ch in s["t"])
        if pairs is not None and prev_y is not None and abs(ln.bbox[1] - prev_y) > 2:
            text = "\n" + text      # 표 칸 안에서는 줄바꿈을 보존
        prev_y = ln.bbox[1]
        style = {}
        if s["w"] >= 600:
            style["b"] = 1
        if s["color"] not in DEFAULT_COLORS:
            style["c"] = s["color"]
        if abs(s["size"] - body_size) > 0.6:
            style["s"] = s["size"]
        if runs and {k: v for k, v in runs[-1].items() if k != "t"} == style:
            runs[-1]["t"] += text
        else:
            runs.append({"t": text, **style})
    return runs


def _plain(runs: list[dict]) -> str:
    return norm("".join(r["t"] for r in runs).replace("\n", " "))


def _clauses(text: str) -> list[dict]:
    """①②… 로 항을 나눈다. 원문자가 없으면 빈 목록."""
    marks = list(CIRCLED.finditer(text))
    if not marks:
        return []
    out = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out.append({"no": ord(m.group()) - 0x245F, "text": text[m.end():end].strip()})
    # 번호가 1부터 순서대로일 때만 항으로 인정(본문 중 '제①항' 같은 인용 방지)
    return out if [c["no"] for c in out] == list(range(1, len(out) + 1)) else []


def _table_block(t: Table, body_size: float) -> tuple[dict, str]:
    cells, texts = [], []
    for c in t.cells:
        pairs = sorted(c.lines, key=lambda p: (round(p[0].bbox[1] / 2), p[1]["bbox"][0]))
        runs = _runs([], body_size, pairs)
        text = norm("".join(r["t"] for r in runs).replace("\n", " "))
        align = "left"
        if pairs:
            x0 = min(p[1]["bbox"][0] for p in pairs)
            x1 = max(p[1]["bbox"][2] for p in pairs)
            if abs((x0 + x1) / 2 - (c.bbox[0] + c.bbox[2]) / 2) < max(3.0, (c.bbox[2] - c.bbox[0]) * 0.04):
                align = "center"
        cell = {"r": c.r, "c": c.c, "rs": c.rs, "cs": c.cs, "runs": runs, "text": text, "align": align,
                "bd": [int(b) for b in c.borders], "ln": [int(b) for b in c.ruled]}
        if c.fill:
            cell["fill"] = c.fill
        cells.append(cell)
        if text:
            texts.append(text)
    content = {
        "rows": len(t.ys) - 1, "cols": len(t.xs) - 1,
        "col_w": [round(b - a, 2) for a, b in zip(t.xs, t.xs[1:])],
        "row_h": [round(b - a, 2) for a, b in zip(t.ys, t.ys[1:])],
        "cells": cells,
        "line": {"c": t.line_color, "w": t.line_width},
        "records": _records(t, cells),
    }
    return content, " | ".join(texts)


def _records(t: Table, cells: list[dict]) -> list[dict]:
    """행마다 '분류 경로 → 값' 레코드로 평탄화(병합 칸을 행마다 펼침). 상품 구성 비교의 기본 자료."""
    nr = len(t.ys) - 1
    out = []
    for r in range(nr):
        row = sorted((c for c in cells if c["r"] <= r < c["r"] + c["rs"]), key=lambda c: c["c"])
        row = [c for c in row if c["text"]]
        if len(row) < 2 or not any(c["r"] == r for c in row):
            continue
        out.append({"row": r, "path": [c["text"] for c in row[:-1]], "value": row[-1]["text"]})
    return out


def _xy_order(units: list[tuple[tuple, object]]) -> list:
    """XY-cut 읽기 순서. PDF 내부 순서는 마스터 페이지 요소가 먼저 오는 등 읽기 순서와 다를 수 있다.

    가장 넓은 빈 띠로 영역을 재귀 분할한다(가로 띠 → 위에서 아래, 세로 띠 → 왼쪽 단에서 오른쪽 단).
    """
    if len(units) <= 1:
        return [u for _, u in units]

    def gaps(lo, hi, min_gap, slack=0.0):
        """가장 넓은 빈 띠(폭, 자를 위치). slack 만큼 각 상자를 안쪽으로 줄여 잰다(살짝 겹친 단 경계 허용)."""
        ivs = sorted((b[lo] + slack, max(b[lo] + slack, b[hi] - slack)) for b, _ in units)
        best, cut, end = 0.0, None, ivs[0][1]
        for a, b in ivs[1:]:
            if a - end >= min_gap and a - end > best:
                best, cut = a - end, (a + end) / 2
            end = max(end, b)
        return best - 2 * slack, cut

    gy, cy = gaps(1, 3, 2.0)
    # 단 사이 여백: 상품설명서 유의사항처럼 단 간격이 2~5pt 로 좁거나, 글줄 끝이 1~2pt 넘어 맞닿는 경우가 있다
    gx, cx = gaps(0, 2, 4.0, slack=1.5)
    if cx is not None and cy is not None:
        # 왼쪽·오른쪽이 각각 영역 높이 대부분을 차지하면 진짜 단 경계다: 단부터 나눈다(왼쪽 단 → 오른쪽 단).
        # 그렇지 않으면 더 넓은 빈 띠를 먼저 자른다.
        y0, y1 = min(b[1] for b, _ in units), max(b[3] for b, _ in units)
        sides = ([b for b, _ in units if (b[0] + b[2]) / 2 < cx], [b for b, _ in units if (b[0] + b[2]) / 2 >= cx])
        tall = all(s and (max(b[3] for b in s) - min(b[1] for b in s)) >= 0.6 * (y1 - y0) for s in sides)
        if tall and min(len(s) for s in sides) >= 2:
            cy = None
    if cy is not None and (cx is None or gy >= gx):   # 더 넓은 빈 띠를 먼저 자른다(단 사이 여백 > 글머리 간격)
        top = [u for u in units if (u[0][1] + u[0][3]) / 2 < cy]
        return _xy_order(top) + _xy_order([u for u in units if (u[0][1] + u[0][3]) / 2 >= cy])
    if cx is not None:
        left = [u for u in units if (u[0][0] + u[0][2]) / 2 < cx]
        return _xy_order(left) + _xy_order([u for u in units if (u[0][0] + u[0][2]) / 2 >= cx])
    return [u for _, u in sorted(units, key=lambda u: (round(u[0][1] / 3), u[0][0]))]


def reading_order(pd: PageData) -> list[Line]:
    units: list[tuple[tuple, object]] = []
    by_table: dict[int, list[Line]] = {}
    for ln in pd.lines:
        if ln.table is None:
            # 글줄 상자는 글꼴의 위·아래 여유까지 들어가 실제 글자보다 훨씬 높다(10pt 제목이 34pt).
            # 그대로 쓰면 제목이 바로 아래 본문과 겹쳐 빈 띠를 못 찾으므로, 글자 크기만 한 높이로 줄여 잰다.
            size = max((s["size"] for s in ln.spans), default=ln.bbox[3] - ln.bbox[1])
            cy, h = (ln.bbox[1] + ln.bbox[3]) / 2, min(size * 0.5, (ln.bbox[3] - ln.bbox[1]) / 2)
            units.append(((ln.bbox[0], cy - h, ln.bbox[2], cy + h), [ln]))
        else:
            by_table.setdefault(ln.table, []).append(ln)
    for ti, lines in by_table.items():
        units.append((pd.tables[ti].bbox, lines))   # 표는 쪼개지 않는 한 덩어리
    return [ln for group in _xy_order(units) for ln in group]


def build_blocks(pages: list[PageData], doc_type: str) -> list[dict]:
    sizes: Counter = Counter()
    for pd in pages:
        for ln in pd.lines:
            if ln.table is None:
                for s in ln.spans:
                    sizes[s["size"]] += len(s["t"])
    body = sizes.most_common(1)[0][0] if sizes else 8.0

    blocks: list[dict] = []
    container: int | None = None       # 현재 조/섹션의 seq
    para: list[Line] = []
    para_page = 0
    side_heads: set[int] = set()       # 같은 줄에 나란히 놓인 제목들(왼쪽 단 제목 · 오른쪽 단 제목)
    page_w = 595.3

    def add(kind, page_no, parent=None, **kw) -> int:
        blocks.append({"seq": len(blocks), "parent_seq": parent, "page_no": page_no, "kind": kind,
                       "label": None, "title": None, "text": "", "content": {}, "bbox": None, **kw})
        return len(blocks) - 1

    def owner(bbox) -> int | None:
        """나란한 제목 아래에 있다가 쪽 폭 전체를 차지하는 내용이 나오면 그 단 제목에서 벗어난다."""
        nonlocal container
        if container in side_heads and bbox and bbox[0] < page_w * 0.15 and bbox[2] > page_w * 0.85:
            container = None
        return container

    def flush():
        nonlocal para
        if not para:
            return
        runs = _runs(para, body)
        bb = [min(l.bbox[0] for l in para), min(l.bbox[1] for l in para),
              max(l.bbox[2] for l in para), max(l.bbox[3] for l in para)]
        add("paragraph", para_page, owner(bb), text=_plain(runs), content={"runs": runs},
            bbox=[round(v, 1) for v in bb])
        para = []

    for pno, pd in enumerate(pages):
        page_w = pd.width
        pd.lines = reading_order(pd)
        # 그림 캡션: 이미지 바로 아래, 가로로 겹치는 짧은 글줄
        figures = []
        for im in pd.images:
            x0, y0, x1, y1 = im["bbox"]
            if (x1 - x0) < 12 or (y1 - y0) < 12:
                continue
            cap = None
            for ln in pd.lines:
                if ln.used or ln.table is not None or len(ln.text.strip()) > 40:
                    continue
                if ln.spans[0]["w"] >= 600 and ln.spans[0]["size"] >= max(body * 1.18, 9.0):
                    continue    # 제목 크기의 굵은 글은 캡션이 아니다(큰 사진 띠 바로 아래 섹션 제목)
                lx = (ln.bbox[0] + ln.bbox[2]) / 2
                # 캡션 글줄 상자가 그림 아래 끝과 몇 pt 겹치는 경우가 많다(글줄 상자의 위 여유)
                if x0 - 4 <= lx <= x1 + 4 and -6 <= ln.bbox[1] - y1 <= 14 and (cap is None or ln.bbox[1] < cap.bbox[1]):
                    cap = ln
            if cap is not None and doc_type == "brochure":
                cap.used = True
            figures.append((im, cap if doc_type == "brochure" else None))

        emitted: set[int] = set()
        block_right: dict[int, float] = {}
        for ln in pd.lines:
            if ln.table is None:
                block_right[ln.block_no] = max(block_right.get(ln.block_no, 0), ln.bbox[2])
        prev: Line | None = None
        for ln in pd.lines:
            if ln.used:
                continue
            if ln.table is not None:
                if ln.table not in emitted:
                    flush()
                    emitted.add(ln.table)
                    t = pd.tables[ln.table]
                    content, text = _table_block(t, body)
                    # 표 맨 윗줄이 전체 폭의 굵은 제목이면 섹션 제목으로 분리한다
                    head = next((c for c in content["cells"] if c["r"] == 0 and c["c"] == 0), None)
                    if (head and head["cs"] == content["cols"] and head["rs"] == 1 and content["rows"] > 2
                            and head["runs"] and all(r.get("b") and r.get("s", 0) >= max(body * 1.18, 9.0)
                                                     for r in head["runs"] if r["t"].strip())):
                        container = add("section", pno, None, title=head["text"], text=head["text"])
                        content["cells"] = [dict(c, r=c["r"] - 1) for c in content["cells"] if c is not head]
                        content["rows"] -= 1
                        content["row_h"] = content["row_h"][1:]
                        content["records"] = [dict(r, row=r["row"] - 1) for r in content["records"] if r["row"] > 0]
                        text = " | ".join(c["text"] for c in content["cells"] if c["text"])
                    add("table", pno, owner(t.bbox), text=text, content=content, bbox=[round(v, 1) for v in t.bbox])
                prev = None
                continue
            text = ln.text.strip()
            first = ln.spans[0]
            bold = first["w"] >= 600
            # 꼬리말(회사 정보·전화번호)
            if ln.bbox[1] > pd.height * 0.925 and (FOOTER.search(text) or first["size"] > body * 1.5):
                flush()
                runs = _runs([ln], body)
                if blocks and blocks[-1]["kind"] == "footer" and blocks[-1]["page_no"] == pno:
                    blocks[-1]["content"]["runs"].append({"t": "\n"})
                    blocks[-1]["content"]["runs"].extend(runs)
                    blocks[-1]["text"] += " " + _plain(runs)
                else:
                    container = None
                    add("footer", pno, None, text=_plain(runs), content={"runs": runs})
                prev = None
                continue
            m = ARTICLE.match(norm(text))
            if m and (bold or doc_type == "terms") and len(m.group(2)) <= 40:
                flush()
                container = add("article", pno, None, label=re.sub(r"\s+", "", m.group(1)), title=m.group(2),
                                bbox=[round(v, 1) for v in ln.bbox])
                if m.group(3):
                    para, para_page = [ln], pno   # 제목 줄에 본문이 이어진 경우
                prev = ln
                continue
            if ADDENDUM.match(text) and doc_type == "terms":
                flush()
                container = add("article", pno, None, label="부칙", title=None, bbox=[round(v, 1) for v in ln.bbox])
                prev = ln
                continue
            if bold and first["size"] >= max(body * 1.18, 9.0) and len(text) <= 40:
                flush()
                kind = "title" if not any(b["kind"] in ("title", "section", "article") for b in blocks) else "section"
                seq = add(kind, pno, None, title=norm(text), text=norm(text), bbox=[round(v, 1) for v in ln.bbox])
                # 같은 쪽·같은 높이에 가로로 떨어진 제목이 이미 있으면 둘 다 단 제목으로 표시한다
                for other in blocks[:-1]:
                    ob = other["bbox"]
                    if (other["kind"] in ("title", "section") and other["page_no"] == pno and ob
                            and abs(ob[1] - ln.bbox[1]) < 4 and (ob[2] < ln.bbox[0] or ln.bbox[2] < ob[0])):
                        side_heads.update({other["seq"], seq})
                container = seq
                prev = None
                continue
            # 문단 경계: 글상자가 바뀌거나, 글머리 기호로 시작하거나, 앞줄이 오른쪽 끝까지 차지 않았을 때
            line_h = prev.bbox[3] - prev.bbox[1] if prev else 0
            if prev is None or not para:
                new_para = True
            elif doc_type == "brochure":   # 글머리 기호 목록: 기호로 시작하거나 단·영역이 바뀔 때만 새 문단
                new_para = bool(MARKER.match(text)) or ln.bbox[1] < prev.bbox[1] - 2 or ln.bbox[1] - prev.bbox[3] > line_h
            else:                          # 양쪽 정렬 본문: 앞줄이 오른쪽 끝까지 차지 않았으면 문단 끝
                short = prev.bbox[2] < block_right.get(prev.block_no, 0) - 8
                new_para = ((ln.block_no != prev.block_no and (bool(MARKER.match(text)) or short))
                            or (short and ln.bbox[1] > prev.bbox[1] + 1) or abs(ln.bbox[1] - prev.bbox[1]) > 40)
            if new_para:
                flush()
                para_page = pno
            para.append(ln)
            prev = ln
        flush()
        for im, cap in figures:
            add("figure", pno, None, text=norm(cap.text) if cap else "",
                content={"asset": im["sha"], "w": im["w"], "h": im["h"], "caption": norm(cap.text) if cap else None},
                bbox=[round(v, 1) for v in im["bbox"]])

    # 조 단위 전체 텍스트와 항 분리(유사 조항 비교의 기본 단위)
    children: dict[int, list[dict]] = {}
    for b in blocks:
        if b["parent_seq"] is not None:
            children.setdefault(b["parent_seq"], []).append(b)
    for b in blocks:
        if b["kind"] == "article":
            body_text = " ".join(c["text"] for c in children.get(b["seq"], []) if c["kind"] == "paragraph")
            b["text"] = body_text
            b["content"] = {"clauses": _clauses(body_text)}
    return blocks
