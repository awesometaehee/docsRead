"""적재된 문서 → 편집기 문서(ProseMirror JSON).

- 조·항목은 moduleBlock 으로 감싸고, 모듈 템플릿의 변수 자리는 variable 노드(값은 이 문서의 값)가 된다.
- 문단 나눔·굵게·색(붉은 글씨)·표 위치는 원본 블록에서 가져온다.
- 템플릿을 채운 글자가 원문과 한 글자라도 다르면 변수 없이 원문 그대로 넣는다(조용히 틀리지 않기 위해).
"""
from __future__ import annotations

import re
from collections import defaultdict

from sqlalchemy import select

from ..db import Block, Document, Module, ModuleOccurrence, ModuleVariant
from ..modules.build import HEADING, clean

_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
PAPER_PX = 724


def _marks(run: dict) -> list[dict]:
    marks = []
    if run.get("b"):
        marks.append({"type": "bold"})
    style = {}
    if run.get("c"):
        style["color"] = run["c"]
    if run.get("s"):
        style["fontSize"] = run["s"]      # 본문 크기와 다른 글자만 크기를 갖는다(pt)
    if style:
        marks.append({"type": "textStyle", "attrs": style})
    return marks


def _text_nodes(runs: list[dict]) -> list[dict]:
    out = []
    for r in runs:
        text = _CTRL.sub("", r["t"]).replace("\n", " ")
        if text:
            node = {"type": "text", "text": text}
            if _marks(r):
                node["marks"] = _marks(r)
            out.append(node)
    return out


HANG = re.compile(r"^\s*(?:[•※▶◆\-]|\d{1,2}\.\s)")


def _paragraph(nodes: list[dict]) -> dict:
    if not nodes:
        return {"type": "paragraph"}
    first = nodes[0].get("text", "") if nodes[0]["type"] == "text" else ""
    node = {"type": "paragraph", "content": nodes}
    if HANG.match(first):
        node["attrs"] = {"role": "hang"}   # 글머리 기호 뒤 내어쓰기
    return node


def _plain_paragraphs(runs: list[dict]) -> list[dict]:
    """run 안의 줄바꿈을 문단으로 나눈다(표 칸·꼬리말)."""
    lines: list[list[dict]] = [[]]
    for r in runs:
        for i, piece in enumerate(r["t"].split("\n")):
            if i:
                lines.append([])
            if piece:
                lines[-1].append(dict(r, t=piece))
    return [_paragraph(_text_nodes(line)) for line in lines if line] or [_paragraph([])]


def table_node(content: dict, bbox=None, images: list[dict] | None = None, attrs: dict | None = None) -> dict:
    total = sum(content.get("col_w", [])) or 1
    xs, ys = [0.0], [0.0]
    for w in content.get("col_w", []):
        xs.append(xs[-1] + w)
    for h in content.get("row_h", []):
        ys.append(ys[-1] + h)
    rows = []
    for r in range(content["rows"]):
        cells = []
        for c in sorted((c for c in content["cells"] if c["r"] == r), key=lambda c: c["c"]):
            # 열 너비(px): 편집 용지의 글 영역 폭에 원본 비율대로 나눈다
            widths = [max(4, round(w / total * PAPER_PX)) for w in content["col_w"][c["c"]:c["c"] + c["cs"]]]
            body = _plain_paragraphs(c["runs"])
            if bbox and images:     # 칸 안에 놓인 그림(예: 상품 사진)
                x0, x1 = bbox[0] + xs[c["c"]], bbox[0] + xs[c["c"] + c["cs"]]
                y0, y1 = bbox[1] + ys[c["r"]], bbox[1] + ys[c["r"] + c["rs"]]
                inside = [im for im in images if x0 <= (im["bbox"][0] + im["bbox"][2]) / 2 <= x1
                          and y0 <= (im["bbox"][1] + im["bbox"][3]) / 2 <= y1]
                pics = [{"type": "image", "attrs": {"src": f"/api/assets/{im['asset']}.png", "alt": "",
                                                    "widthPt": round(im["bbox"][2] - im["bbox"][0], 1)}} for im in inside]
                if pics:
                    body = pics + ([] if not c["text"] else body)
            cells.append({"type": "tableCell",
                          "attrs": {"colspan": c["cs"], "rowspan": c["rs"], "background": c.get("fill"),
                                    "align": c.get("align", "left"), "colwidth": widths,
                                    "ln": c.get("ln") or c.get("bd") or [1, 1, 1, 1]},
                          "content": body})
        rows.append({"type": "tableRow", "attrs": {"h": round(content["row_h"][r], 1)}, "content": cells})
    line = content.get("line") or {}
    return {"type": "table", "attrs": {"lineColor": line.get("c", "#231f20"), "lineW": line.get("w", 0.5), **(attrs or {})},
            "content": rows}


def is_variable(slot: dict, value: str) -> bool:
    """편집기에서 변수 칩으로 보일 자리인가.

    날짜·금액·회사명처럼 유형이 분명하거나 문서마다 값이 여러 가지인 짧은 자리만 변수로 본다.
    두세 가지 표현 차이(조사·띄어쓰기·문장 추가)나 긴 문장은 일반 글자로 넣어 직접 고치게 한다.
    """
    if not re.search(r"[0-9A-Za-z가-힣]", value):   # 이 문서에서 비어 있거나 기호뿐인 자리
        return False
    return len(value) <= 40 and (slot["type"] != "문구" or slot["n_values"] >= 4)


def _fill(parts: list[dict], slots: list[dict], values: list[str]) -> str:
    return "".join(p["t"] if "t" in p else values[p["v"]] for p in parts)


def _key(text: str) -> str:
    return re.sub(r"\s+", "", _CTRL.sub("", text))


def module_paragraphs(paras: list[list[dict]], variant: ModuleVariant, values: list[str], module: Module,
                      strip_heading: bool) -> list[list[dict]] | None:
    """원본 문단들(paras: 문단별 run 목록)의 나눔·서식을 유지하면서 변수 자리를 variable 노드로 바꾼다.

    반환: 문단별 인라인 노드 목록. 템플릿과 원문이 맞지 않으면 None.
    """
    styles: list[list[list[dict]]] = []     # 문단 → 글자(공백 제외)별 marks
    for runs in paras:
        st = []
        for r in runs:
            st.extend([_marks(r)] * len(_key(r["t"])))
        styles.append(st)
    stream = "".join(_key("".join(r["t"] for r in runs)) for runs in paras)
    joined = clean(" ".join(clean("".join(r["t"] for r in runs).replace("\n", " ")) for runs in paras))
    prefix = len(_key(joined)) - len(_key(HEADING.sub("", joined))) if strip_heading else 0
    if _key(_fill(variant.parts, variant.slots, values)) != stream[prefix:]:
        return None
    out: list[list[dict]] = [[] for _ in paras]
    p, c = 0, prefix
    while p < len(paras) and c >= len(styles[p]) and p + 1 < len(paras):
        c -= len(styles[p])
        p += 1

    def push(pi: int, text: str, marks: list[dict]):
        last = out[pi][-1] if out[pi] else None
        if last and last["type"] == "text" and last.get("marks", []) == marks:
            last["text"] += text
        else:
            node = {"type": "text", "text": text}
            if marks:
                node["marks"] = marks
            out[pi].append(node)

    def advance():
        nonlocal p, c
        while c >= len(styles[p]) and p + 1 < len(paras):
            c -= len(styles[p])
            p += 1

    for part in variant.parts:
        if "t" in part:
            for ch in part["t"]:
                if ch.isspace():
                    if out[p]:      # 문단 첫머리 공백은 버린다
                        push(p, " ", out[p][-1].get("marks", []) if out[p][-1]["type"] == "text" else [])
                    continue
                advance()
                push(p, ch, styles[p][c] if c < len(styles[p]) else [])
                c += 1
        else:
            slot, value = variant.slots[part["v"]], values[part["v"]]
            n = len(_key(value))
            if n:
                advance()
            if not is_variable(slot, value) or c + n > len(styles[p]):
                # 변수로 두지 않는 자리, 또는 문단 경계를 넘는 값은 일반 글자로 넣는다
                for ch in value:
                    if ch.isspace():
                        if out[p]:
                            push(p, " ", out[p][-1].get("marks", []) if out[p][-1]["type"] == "text" else [])
                        continue
                    advance()
                    push(p, ch, styles[p][c] if c < len(styles[p]) else [])
                    c += 1
                continue
            marks = styles[p][c] if c < len(styles[p]) else []
            node = {"type": "variable",
                    "attrs": {"name": f"{module.title.split(' › ')[-1][:10].strip()}·{slot['name']}", "value": value,
                              "options": [v for v, _ in slot["top"]][:8], "vtype": slot["type"]}}
            if marks:
                node["marks"] = marks
            out[p].append(node)
            c += n
    for nodes in out:   # 문단 끝 공백 정리
        if nodes and nodes[-1]["type"] == "text":
            nodes[-1]["text"] = nodes[-1]["text"].rstrip()
            if not nodes[-1]["text"]:
                nodes.pop()
    return out


PAD = 14.2     # 용지 가장자리 ~ 틀(표·패널)까지 여백(pt). 원본 공통값


def _item(it) -> dict:
    return {"k": it.kind, "b": [it.x0, it.y0, it.x1, it.y1], **it.data}


def region_node(page, rect, kind: str, **attrs) -> dict:
    """페이지의 한 영역을 원본 그리기 요소 그대로 담는 노드(사진 묶음·꼬리말). 좌표째 보존해 똑같이 그린다."""
    x0, y0, x1, y1 = rect
    items = []
    for it in page.items:
        cx, cy = (it.x0 + it.x1) / 2, (it.y0 + it.y1) / 2
        if it.kind == "image":
            ok = it.x1 > x0 and it.x0 < x1 and it.y1 > y0 and it.y0 < y1 and y0 - 2 <= cy
        else:
            ok = x0 - 1 <= cx <= x1 + 1 and y0 - 1 <= cy <= y1 + 1 and (it.x1 - it.x0) <= (x1 - x0) + 4
        if ok:
            items.append(_item(it))
    return {"type": "region", "attrs": {"x": round(x0, 2), "y": round(y0, 2), "w": round(x1 - x0, 2),
                                        "h": round(y1 - y0, 2), "kind": kind, "items": items, **attrs}}


def page_style(doc: Document, blocks: list[Block]) -> dict:
    """본문 글자 크기와 줄 간격을 원본에서 잰다."""
    from collections import Counter
    import statistics
    sizes: Counter = Counter()
    rows = []
    for page in doc.pages:
        tables = [b.bbox for b in blocks if b.kind == "table" and b.page_no == page.page_no and b.bbox]
        for it in page.items:
            if it.kind != "text":
                continue
            cx, cy = (it.x0 + it.x1) / 2, (it.y0 + it.y1) / 2
            if any(t[0] <= cx <= t[2] and t[1] <= cy <= t[3] for t in tables):
                continue
            for sp in it.data["spans"]:
                sizes[sp["size"]] += len(sp["t"])
            rows.append((page.page_no, round(it.x0), it.y0, it.data["spans"][0]["size"]))
    body = sizes.most_common(1)[0][0] if sizes else 8.4
    rows.sort()
    pitch = [b[2] - a[2] for a, b in zip(rows, rows[1:])
             if a[:2] == b[:2] and abs(a[3] - body) < 0.3 and abs(b[3] - body) < 0.3 and 0 < b[2] - a[2] < body * 2]
    lh = statistics.median(pitch) / body if pitch else 1.2
    w = doc.pages[0].width if doc.pages else 595.3
    h = doc.pages[0].height if doc.pages else 841.9
    return {"body_pt": round(body, 2), "line_height": round(min(1.6, max(1.05, lh)), 3),
            "page_w": round(w, 1), "page_h": round(h, 1), "pad": PAD, "doc_type": doc.doc_type}


def document_to_editor(session, doc: Document) -> tuple[dict, dict, dict]:
    blocks = list(doc.blocks)
    pages = {p.page_no: p for p in doc.pages}
    style = page_style(doc, blocks)
    content_w = style["page_w"] - 2 * PAD
    kids: dict[int, list[Block]] = defaultdict(list)
    for b in blocks:
        if b.parent_seq is not None:
            kids[b.parent_seq].append(b)
    occ = {o.block_seq: (o, v, m) for o, v, m in session.execute(
        select(ModuleOccurrence, ModuleVariant, Module)
        .join(ModuleVariant, ModuleVariant.id == ModuleOccurrence.variant_id)
        .join(Module, Module.id == ModuleVariant.module_id)
        .where(ModuleOccurrence.document_id == doc.id))}
    stats = {"modules": 0, "variables": 0, "fallback": 0}

    # 페이지별 그림: 꼬리말 영역 / 표 안 / 사진 묶음(갤러리)
    footer_top: dict[int, float] = {}
    for p in doc.pages:
        ys = [it.y0 for it in p.items if it.kind == "text" and it.y0 > p.height * 0.9
              and re.search(r"대표이사|www\.|E-?mail", "".join(sp["t"] for sp in it.data["spans"]))]
        if ys:
            footer_top[p.page_no] = min(ys) - 16
    figs: dict[int, list[Block]] = defaultdict(list)
    for b in blocks:
        if b.kind == "figure" and b.bbox and b.content.get("asset"):
            if b.page_no in footer_top and b.bbox[3] > footer_top[b.page_no] + 4:
                continue    # 꼬리말 영역의 로고·가림 그림은 꼬리말 노드가 함께 그린다
            figs[b.page_no].append(b)
    table_boxes = {(b.page_no, b.seq): b.bbox for b in blocks if b.kind == "table" and b.bbox}
    in_table: dict[int, list[dict]] = defaultdict(list)     # 표 seq → 그림
    gallery: dict[int, list[Block]] = defaultdict(list)
    for pno, fl in figs.items():
        for f in fl:
            cx, cy = (f.bbox[0] + f.bbox[2]) / 2, (f.bbox[1] + f.bbox[3]) / 2
            host = next((seq for (pg, seq), t in table_boxes.items()
                         if pg == pno and t[0] <= cx <= t[2] and t[1] <= cy <= t[3]), None)
            if host is not None:
                in_table[host].append({"bbox": f.bbox, "asset": f.content["asset"]})
            else:
                gallery[pno].append(f)

    def tnode(c: Block, attrs=None) -> dict:
        return table_node(c.content, c.bbox, in_table.get(c.seq), attrs)

    def wrap(seq: int, head: list[dict], children: list[Block], strip_heading=False) -> list[dict]:
        """블록 seq 가 모듈이면 moduleBlock 으로 감싼다. children 은 문단·표 블록."""
        paras = [c for c in children if c.kind in ("paragraph", "footer") and c.text]
        body: list[dict] = []
        filled = None
        if seq in occ:
            o, v, m = occ[seq]
            filled = module_paragraphs([c.content.get("runs", []) for c in paras], v, o.values, m, strip_heading) if paras else None
            if filled is None and paras:
                stats["fallback"] += 1
        it = iter(filled) if filled is not None else None
        floated: list[dict] = []
        for c in children:
            if c.kind == "table":
                beside = [p for p in paras if c.bbox and p.bbox and p.bbox[0] > c.bbox[2] - 2
                          and p.bbox[1] < c.bbox[3] and p.bbox[3] > c.bbox[1]]
                if beside and c.bbox[2] - c.bbox[0] < content_w * 0.6:
                    # 표 오른쪽에 글이 나란히 놓인 배치(선수금 관리방법): 표를 왼쪽에 띄운다
                    floated.append(tnode(c, {"float": "left", "widthPct": round((c.bbox[2] - c.bbox[0]) / content_w * 100, 1)}))
                else:
                    body.append(tnode(c))
            elif c.kind in ("paragraph", "footer") and c.text:
                nodes = next(it) if it is not None else _text_nodes(c.content.get("runs", []))
                if nodes:
                    body.append(_paragraph(nodes))
        body = floated + body
        if seq not in occ:
            return head + body
        o, v, m = occ[seq]
        stats["modules"] += 1
        stats["variables"] += sum(1 for b in body if b["type"] == "paragraph"
                                  for n in b.get("content", []) if n["type"] == "variable")
        return [{"type": "moduleBlock",
                 "attrs": {"moduleId": m.id, "variantId": v.id, "title": m.title, "kind": m.kind,
                           "nDocs": m.n_docs, "nVariants": m.n_variants, "ordinal": v.ordinal},
                 "content": (head + body) or [_paragraph([])]}]

    def heading(level: int, text: str) -> dict:
        return {"type": "heading", "attrs": {"level": level}, "content": [{"type": "text", "text": text}]}

    def two_columns(children: list[Block]) -> bool:
        xs = [c.bbox[0] for c in children if c.kind == "paragraph" and c.bbox]
        return sum(x > style["page_w"] * 0.4 for x in xs) >= 3 and sum(x < style["page_w"] * 0.2 for x in xs) >= 3

    out: list[tuple[int, float, dict]] = []     # (쪽, 세로 위치, 노드)

    def emit(b: Block, nodes: list[dict], y: float | None = None):
        y = y if y is not None else (b.bbox[1] if b.bbox else 1e9)
        out.extend((b.page_no, y, n) for n in nodes)

    for b in blocks:
        if b.parent_seq is not None:
            continue
        if b.kind in ("title", "section"):
            head = [heading(1 if b.kind == "title" else 2, b.title or "")]
            ys = [c.bbox[1] for c in kids[b.seq] if c.bbox]
            y = b.bbox[1] if b.bbox else (min(ys) - 12 if ys else None)
            if b.seq in occ:
                emit(b, wrap(b.seq, head, kids[b.seq]), y)
            else:
                body = [n for c in kids[b.seq] for n in wrap(c.seq, [], [c])]
                if body and two_columns(kids[b.seq]):
                    body = [{"type": "columns", "attrs": {"count": 2}, "content": body}]
                emit(b, head + body, y)
        elif b.kind == "article":
            label = f"{b.label} ({b.title})" if b.title else (b.label or "")
            emit(b, wrap(b.seq, [heading(3, label)], kids[b.seq], strip_heading=True))
        elif b.kind == "paragraph":
            emit(b, wrap(b.seq, [], [b]))
        elif b.kind == "table":
            emit(b, [tnode(b)])
        elif b.kind == "footer" and b.page_no in footer_top:
            page = pages[b.page_no]
            top = footer_top.pop(b.page_no)     # 쪽마다 한 번만
            emit(b, [region_node(page, (0, top, page.width, page.height), "footer")], 1e8)

    # 사진 묶음: 원본 위치의 세로 순서에 맞춰 끼워 넣는다. 옆에 글이 있으면 그쪽을 비워 띄운다.
    for pno, fl in gallery.items():
        page = pages[pno]
        if len(fl) < 3:
            for f in fl:
                out.append((pno, f.bbox[1], {"type": "image", "attrs": {
                    "src": f"/api/assets/{f.content['asset']}.png", "alt": f.content.get("caption") or "",
                    "widthPt": round(f.bbox[2] - f.bbox[0], 1)}}))
            continue
        ux0, uy0 = min(f.bbox[0] for f in fl), min(f.bbox[1] for f in fl)
        ux1, uy1 = max(f.bbox[2] for f in fl), max(f.bbox[3] for f in fl)
        rect = (ux0 - 3, uy0 - 3, min(ux1 + 3, page.width - PAD), uy1 + 12)
        panels = [it for it in page.items if it.kind == "path" and it.data.get("fill")
                  and it.x0 <= ux0 + 2 and it.y0 <= uy0 + 2 and it.x1 >= min(ux1, page.width - PAD) - 2 and it.y1 >= uy1 - 2
                  and (it.x1 - it.x0) * (it.y1 - it.y0) < page.width * page.height * 0.4]
        if panels:      # 사진들을 감싼 배경 패널이 있으면 그 범위를 쓴다
            pn = min(panels, key=lambda it: (it.x1 - it.x0) * (it.y1 - it.y0))
            rect = (max(pn.x0, PAD), pn.y0, min(pn.x1, page.width - PAD), pn.y1)
        width = rect[2] - rect[0]
        attrs = {}
        if width < content_w * 0.85:
            attrs = {"float": "right" if (rect[0] + rect[2]) / 2 > page.width / 2 else "left",
                     "widthPct": round(width / content_w * 100, 1)}
        out.append((pno, rect[1] - 14, region_node(page, rect, "gallery", **attrs)))

    order = sorted(range(len(out)), key=lambda i: (out[i][0], 0 if out[i][2]["type"] != "region" else 1, i))
    # 본문 블록은 읽기 순서 그대로 두고, 사진 묶음만 세로 위치에 맞는 자리로 옮긴다
    body_nodes = [o for o in out if not (o[2]["type"] == "region" and o[2]["attrs"]["kind"] == "gallery")]
    for g in (o for o in out if o[2]["type"] == "region" and o[2]["attrs"]["kind"] == "gallery"):
        at = next((i for i, o in enumerate(body_nodes) if o[0] == g[0] and o[1] >= g[1] and o[1] < 1e8), None)
        if at is None:
            at = next((i for i, o in enumerate(body_nodes) if o[0] > g[0] or (o[0] == g[0] and o[1] >= 1e8)), len(body_nodes))
        body_nodes.insert(at, g)
    content = [n for _, _, n in body_nodes]
    return {"type": "doc", "content": content or [_paragraph([])]}, stats, style
