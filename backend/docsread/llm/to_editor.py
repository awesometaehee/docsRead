"""정형 JSON(PageOut 목록) + PDF 에서 잰 값 → 편집기 문서(ProseMirror JSON). 모양은 전부 여기서 정한다.

역할 나눔
- LLM: 무엇인지(제목·조·문단·표 구조·색 구분·정렬·두 단 여부)
- PDF(기존 데이터화 결과): 치수와 그림(표 열 너비·칸 배경·괘선, 사진 묶음 배치·캡션, 꼬리말 로고·띠)
- 이 파일: 그 둘을 정해진 부품(박스·표·단·그림)으로 조립. 색 값 등은 아래 상수로만 정해진다
"""
from __future__ import annotations

import re
from collections import Counter

from ..db import Block, Document
from ..editor.convert import (HANG, PAD, PAPER_PX, box, classify_figures, footer_nodes, gallery_for, page_style,
                              table_node)

COLOR = {"default": None, "red": "#ed1c27", "gray": "#4d4d4f"}      # 원본 문서에서 가장 많이 쓰인 값
SHADE = {"none": None, "light": "#f1f1f2", "medium": "#dadbdc", "dark": "#8d9d9f", "cream": "#fffef3"}
NOTICE = {"red": {"border": True, "borderColor": "#ed1c27", "pad": 5},
          "gray": {"border": False, "bg": "#f1f1f2", "pad": 5},
          "plain": {"border": True, "pad": 5}}
LINE = {"lineColor": "#231f20", "lineW": 0.5}
TABLE_MATCH = 0.9      # LLM 표와 PDF 표의 글자 일치율이 이 이상이면 PDF 표의 치수·배경·괘선을 쓴다

_WS = re.compile(r"[\s|]+")      # PDF 표 글자는 칸 사이를 " | " 로 이어 두었다


def _text(text: str, bold=False, color="default") -> dict | None:
    text = text.replace("\n", " ")
    if not text:
        return None
    node: dict = {"type": "text", "text": text}
    marks = []
    if bold:
        marks.append({"type": "bold"})
    if COLOR.get(color):
        marks.append({"type": "textStyle", "attrs": {"color": COLOR[color]}})
    if marks:
        node["marks"] = marks
    return node


def _inline(segments: list[dict]) -> list[dict]:
    out: list[dict] = []
    for s in segments:
        n = _text(s["text"], s["bold"], s["color"])
        if n is None:
            continue
        if out and out[-1].get("marks") == n.get("marks"):      # 같은 서식은 한 덩이로
            out[-1]["text"] += n["text"]
        else:
            out.append(n)
    return out


def _paragraph(p: dict) -> dict:
    content = _inline(p["segments"])
    node: dict = {"type": "paragraph"}
    if content:
        node["content"] = content
    first = content[0]["text"] if content else ""
    align = p.get("align", "left")
    if align != "left":
        node["attrs"] = {"textAlign": align}       # 가운데 줄은 내어쓰기하지 않는다
    elif p.get("style") in ("bullet", "numbered") or HANG.match(first):
        node["attrs"] = {"role": "hang"}
    return node


def _heading(level: int, b: dict) -> dict:
    n = _text(b["text"], color=b.get("color", "default"))
    return {"type": "heading", "attrs": {"level": level}, "content": [n] if n else []}


def _plain(text: str) -> dict:
    return _paragraph({"segments": [{"text": text, "bold": False, "color": "default"}], "style": "body"})


# ---- 표 ----

def _table_text(t: dict) -> str:
    return "".join(s["text"] for r in t["rows"] for c in r["cells"] for s in c["segments"])


def _overlap(a: str, b: str) -> float:
    ca, cb = Counter(_WS.sub("", a)), Counter(_WS.sub("", b))
    if not ca or not cb:
        return 0.0
    hit = sum((ca & cb).values())
    return min(hit / sum(ca.values()), hit / sum(cb.values()))


def _cell_paragraphs(cell: dict) -> list[dict]:
    """칸 안의 줄바꿈은 문단 나눔."""
    lines: list[list[dict]] = [[]]
    for s in cell["segments"]:
        for i, piece in enumerate(s["text"].split("\n")):
            if i:
                lines.append([])
            if piece:
                lines[-1].append({**s, "text": piece, "bold": s["bold"] or cell["header"]})
    paras = [_paragraph({"segments": l, "style": "body"}) for l in lines if l]
    return paras or [{"type": "paragraph"}]


def _llm_table(t: dict, warn: list[str], where: str, cell_pics: dict | None = None) -> dict | None:
    """PDF 에서 맞는 표를 못 찾았을 때: LLM 의 구조·열 비율·배경으로 만든다.
    칸 격자를 직접 채워 보며 행마다 너비를 맞추고, 모자란 칸은 빈 칸으로 채워 알린다."""
    rows = t["rows"]
    if not rows:
        return None
    taken: set[tuple[int, int]] = set()
    width = 0
    placed: list[list[tuple[int, dict]]] = []
    for r, row in enumerate(rows):
        c, cur = 0, []
        for cell in row["cells"]:
            while (r, c) in taken:
                c += 1
            cs, rs = max(1, cell["colspan"]), max(1, min(cell["rowspan"], len(rows) - r))
            for dr in range(rs):
                for dc in range(cs):
                    taken.add((r + dr, c + dc))
            cur.append((c, {**cell, "colspan": cs, "rowspan": rs}))
            c += cs
        placed.append(cur)
        width = max(width, max((x for (rr, x) in taken if rr == r), default=-1) + 1)
    pct = [max(1, w) for w in t.get("col_widths") or []]
    if len(pct) != width:
        if pct:
            warn.append(f"{where}: 표 열 비율 수({len(pct)})가 열 수({width})와 달라 같은 너비로 둠")
        pct = [1] * width
    px = [w / sum(pct) * PAPER_PX * 0.98 for w in pct]
    out_rows, padded = [], 0
    for r, cur in enumerate(placed):
        cells = []
        for k, (c, cell) in enumerate(cur):
            bg = SHADE.get(cell.get("shade", "none"))
            paras = _cell_paragraphs(cell)
            pic = (cell_pics or {}).get((r, k))
            if pic is not None:           # 사진 칸: 배정된 PDF 그림을 칸 맨 위에(글이 없으면 그림만)
                paras = [pic] + ([] if not _cell_text(cell) else paras)
            if cell.get("shade") == "dark":         # 진한 배경 칸은 흰 글씨
                for p in paras:
                    for n in p.get("content", []):
                        n["marks"] = [m for m in n.get("marks", []) if m["type"] != "textStyle"] + \
                                     [{"type": "textStyle", "attrs": {"color": "#ffffff"}}]
            cells.append({"type": "tableCell",
                          "attrs": {"colspan": cell["colspan"], "rowspan": cell["rowspan"], "background": bg,
                                    "align": cell["align"], "colwidth": [round(w) for w in px[c:c + cell["colspan"]]],
                                    "ln": [1, 1, 1, 1]},
                          "content": paras})
        missing = sum(1 for c in range(width) if (r, c) not in taken)
        if missing:
            padded += missing
            cells += [{"type": "tableCell", "attrs": {"colspan": 1, "rowspan": 1, "align": "left", "ln": [1, 1, 1, 1]},
                       "content": [{"type": "paragraph"}]} for _ in range(missing)]
        if cells:
            out_rows.append({"type": "tableRow", "attrs": {"h": None}, "content": cells})
    if padded:
        warn.append(f"{where}: 표 칸 수가 행마다 맞지 않아 빈 칸 {padded}개를 채움")
    return {"type": "table", "attrs": dict(LINE), "content": out_rows} if out_rows else None


# ---- 쪽 조립 ----

_KEY = re.compile(r"[\s|·•※()\[\]]+")


def _key(text: str) -> str:
    return _KEY.sub("", text or "")


def _cell_text(cell: dict) -> str:
    return "".join(s["text"] for s in cell["segments"]).strip()


def _clusters(figs: list[Block]) -> list[list[Block]]:
    """맞닿거나 겹친 그림 조각을 한 장면으로(로고·사진이 여러 조각으로 나뉜 경우). 위→아래, 왼쪽→오른쪽 순."""
    parent = list(range(len(figs)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for a in range(len(figs)):
        for b in range(a + 1, len(figs)):
            p, q = figs[a].bbox, figs[b].bbox
            if p[0] - 2 <= q[2] and q[0] - 2 <= p[2] and p[1] - 2 <= q[3] and q[1] - 2 <= p[3]:
                parent[find(a)] = find(b)
    groups: dict[int, list[Block]] = {}
    for k, f in enumerate(figs):
        groups.setdefault(find(k), []).append(f)
    return sorted(groups.values(), key=lambda g: (min(f.bbox[1] for f in g), min(f.bbox[0] for f in g)))


def _bbox(group: list[Block]) -> tuple[float, float, float, float]:
    return (min(f.bbox[0] for f in group), min(f.bbox[1] for f in group),
            max(f.bbox[2] for f in group), max(f.bbox[3] for f in group))


class _Page:
    """쪽 하나의 PDF 측 재료와, LLM 블록 ↔ PDF 재료(표·그림) 짝짓기.

    - 표: 글자가 90% 이상 맞는 PDF 표가 있으면 그 표의 치수·배경·괘선·칸 안 그림을 쓴다
    - 그림: 짝지은 PDF 표 안의 것과 꼬리말 영역을 뺀 PDF 그림을 장면(맞닿은 조각 묶음)으로 나눈 뒤
        1) 캡션이 있는 그림 자리 → PDF 글자에서 그 캡션을 찾아 바로 위의 장면
        2) 캡션 없는 그림 자리·사진 칸 → 남은 장면을 위→아래 순서대로
    """

    def __init__(self, llm_page: dict, tables: list[Block], figures: list[Block], in_table,
                 page, footer, footer_top, content_w: float, body_pt: float, pdf_path=None):
        self.page = page
        self.content_w = content_w
        self.body_pt = body_pt
        self.in_table = in_table
        self.match: dict[int, Block] = {}
        free = list(tables)
        blocks = llm_page["blocks"]
        for t in (b for b in blocks if b["type"] == "table"):
            text = _table_text(t)
            best = max(free, key=lambda b: _overlap(text, b.text), default=None)
            if best is not None and _overlap(text, best.text) >= TABLE_MATCH:
                self.match[id(t)] = best
                free.remove(best)
        used_boxes = [b.bbox for b in self.match.values() if b.bbox]

        def inside(f, bb):
            cx, cy = (f.bbox[0] + f.bbox[2]) / 2, (f.bbox[1] + f.bbox[3]) / 2
            return bb[0] <= cx <= bb[2] and bb[1] <= cy <= bb[3]

        pool = [f for f in figures if (footer_top is None or f.bbox[3] <= footer_top + 4)
                and not any(inside(f, bb) for bb in used_boxes)]
        scenes = _clusters(pool)
        self.pics: dict = {}            # id(그림 블록) 또는 (id(표), 행, 칸) → 장면
        # 1) 캡션으로 찾기: 캡션 글줄 바로 위(40pt 안), 가로로 겹치는 장면
        texts = [(it, _key("".join(sp["t"] for sp in it.data["spans"]))) for it in page.items if it.kind == "text"]
        for b in blocks:
            if b["type"] != "image" or len(_key(b["caption"])) < 2:
                continue
            cap = _key(b["caption"])
            hits = [it for it, k in texts if k and (cap in k or (len(k) >= 3 and k in cap))]
            best, gap = None, 1e9
            for it in hits:
                cx = (it.x0 + it.x1) / 2
                for sc in scenes:
                    x0, y0, x1, y1 = _bbox(sc)
                    d = it.y0 - y1
                    if -3 <= d < 40 and x0 - 6 <= cx <= x1 + 6 and d < gap:
                        best, gap = sc, d
            if best is not None:
                self.pics[id(b)] = best
                scenes.remove(best)
        # 2) 캡션 없는 자리·사진 칸: 남은 장면을 위→아래로
        slots: list = []
        for b in blocks:
            if b["type"] == "image" and id(b) not in self.pics:
                slots.append(id(b))
            elif b["type"] == "table" and id(b) not in self.match:
                for r, row in enumerate(b["rows"]):
                    for k, cell in enumerate(row["cells"]):
                        if cell.get("image"):
                            slots.append((id(b), r, k))
        for slot, sc in zip(slots, scenes):
            self.pics[slot] = sc
        self.unplaced = scenes[len(slots):]
        self.footer = footer_nodes(page, footer, footer_top, pdf_path, [b.bbox for b in tables if b.bbox]) \
            if footer is not None and footer_top is not None else []

    def scene_node(self, sc: list[Block], caption: str) -> dict:
        """장면 하나 → 그림 노드. 조각이 하나면 이미지, 여럿이면 겹쳐 놓은 그림(figure)."""
        if len(sc) == 1:
            f = sc[0]
            return {"type": "paragraph", "content": [{"type": "image", "attrs": {
                "src": f"/api/assets/{f.content['asset']}.png", "alt": caption, "widthPt": round(f.bbox[2] - f.bbox[0], 1)}}]}
        x0, y0, x1, y1 = _bbox(sc)
        return {"type": "figure", "attrs": {"w": round(x1 - x0, 1), "h": round(y1 - y0, 1), "plain": True, "images": [
            {"src": f"/api/assets/{f.content['asset']}.png", "x": round(f.bbox[0] - x0, 1), "y": round(f.bbox[1] - y0, 1),
             "w": round(f.bbox[2] - f.bbox[0], 1), "h": round(f.bbox[3] - f.bbox[1], 1)} for f in sc]}}

    def table(self, t: dict, warn: list[str], where: str) -> dict | None:
        best = self.match.get(id(t))
        if best is not None:        # PDF 표의 열 너비·칸 배경·괘선·행 높이·칸 안 그림을 그대로
            share = (best.bbox[2] - best.bbox[0]) / self.content_w if best.bbox else 1.0
            return table_node(best.content, best.bbox, self.in_table.get(best.seq), None, share)
        pics = {}
        for r, row in enumerate(t["rows"]):
            for k, cell in enumerate(row["cells"]):
                sc = self.pics.get((id(t), r, k))
                if sc is not None:
                    pics[(r, k)] = self.scene_node(sc, "")
                elif cell.get("image"):
                    warn.append(f"{where}: 표의 사진 칸에 넣을 PDF 그림이 없음")
        return _llm_table(t, warn, where, pics)


def _render(blocks: list[dict], src: _Page, warn: list[str], where: str, in_column: bool) -> list[dict]:
    """블록 목록(나란히 영역 표시가 짝 맞춰 들어 있음: validate.normalize) → 편집기 노드."""
    out: list[dict] = []
    last_section = None          # 마지막 section 제목 노드의 위치(옆으로 띄우는 사진 묶음을 둘 자리)
    i = 0
    while i < len(blocks):
        b = blocks[i]
        t = b["type"]
        if t == "side_start":
            # 짝이 맞는 끝까지 열별로 모아 '나란히 놓기' 부품으로
            cols: list[list[dict]] = [[]]
            i += 1
            while i < len(blocks) and blocks[i]["type"] != "side_end":
                if blocks[i]["type"] == "side_next":
                    cols.append([])
                else:
                    cols[-1].append(blocks[i])
                i += 1
            gap = 6.0
            usable = src.content_w - gap * (len(cols) - 1)
            ws = b["widths"] if len(b["widths"]) == len(cols) else [100 / len(cols)] * len(cols)
            content = []
            for w, col in zip(ws, cols):
                nodes = _frame(_render(col, src, warn, where, True))
                content.append({"type": "splitCol", "attrs": {"w": round(usable * w / sum(ws), 1)},
                                "content": nodes or [{"type": "paragraph"}]})
            out.append({"type": "split", "attrs": {"gap": gap}, "content": content})
        elif t == "title":
            out.append(_heading(1, b))
        elif t == "section":
            last_section = len(out)
            h = _heading(2, b)
            h["_columns"] = b.get("columns", 1)
            out.append(h)
        elif t == "article_title":
            out.append(_heading(3, b))
        elif t == "paragraph":
            out.append(_paragraph(b))
        elif t == "notice":
            out.append(box([_paragraph(p) for p in b["paragraphs"]], **NOTICE[b["tone"]]))
        elif t == "table":
            n = src.table(b, warn, where)
            if n:
                out.append(n)
        elif t == "image":
            # 이어진 그림 자리들: 둘 이상이면 PDF 원본 배치·캡션으로 사진 묶음 하나, 하나면 그림 하나
            run = []
            while i < len(blocks) and blocks[i]["type"] == "image":
                run.append(blocks[i])
                i += 1
            i -= 1
            placed = [(x, src.pics[id(x)]) for x in run if id(x) in src.pics]
            for x in run:
                if id(x) not in src.pics:
                    warn.append(f"{where}: 그림 자리에 맞는 PDF 그림이 없어 넣지 않음({x['caption'] or '설명 없음'})")
            if len(placed) >= 2:
                figs = [f for _, sc in placed for f in sc]
                g, _ = gallery_for(src.page, figs, src.content_w, src.body_pt)
                if in_column:
                    g["attrs"].pop("float", None)
                    g["attrs"].pop("widthPct", None)
                if g["attrs"].get("float") and last_section is not None:
                    out.insert(last_section + 1, g)     # 옆으로 띄우는 묶음은 그 항목 글보다 앞에
                else:
                    out.append(g)
            elif placed:
                x, sc = placed[0]
                out.append(src.scene_node(sc, x["caption"]))
                if x["caption"]:
                    out.append(_plain(x["caption"]))
        i += 1
    return out


def _heads_into_columns(blocks: list[dict], pdf_page) -> list[dict]:
    """좌우 배치 바로 앞의 제목(title·section)이 원본에서 한쪽 열 위에만 걸린 제목이면 그 열 맨 앞으로 옮긴다.
    (예: '상품 설명서' 제목 띠는 왼쪽 본표 위에만 있고 오른쪽은 '비아젬 상품 정보'가 따로 시작)
    판단: PDF 에서 제목 글자를 찾아 가로 가운데가 쪽 가운데에서 글 영역 폭의 8% 넘게 벗어나면 그 위치의 열."""
    out = list(blocks)
    texts = [(it, _key("".join(sp["t"] for sp in it.data["spans"]))) for it in pdf_page.items if it.kind == "text"]
    left, right = PAD, pdf_page.width - PAD
    width = right - left
    k = 0
    while k + 1 < len(out):
        h, st = out[k], out[k + 1]
        if h["type"] in ("title", "section") and st["type"] == "side_start":
            key = _key(h["text"])
            hits = [it for it, t in texts if key and t and (key == t or key in t)]
            if hits:
                cx = min(hits, key=lambda it: it.y0)
                cx = (cx.x0 + cx.x1) / 2
                if abs(cx - (left + right) / 2) > width * 0.08:
                    ws = st["widths"]
                    edges, acc = [], 0.0
                    for w in ws:
                        acc += w / sum(ws) * width
                        edges.append(left + acc)
                    col = next((c for c, e in enumerate(edges) if cx <= e), len(ws) - 1)
                    # side_start 뒤에서 col 번째 열의 시작 위치를 찾아 제목을 넣는다
                    j, cur = k + 2, 0
                    while cur < col and j < len(out) and out[j]["type"] != "side_end":
                        if out[j]["type"] == "side_next":
                            cur += 1
                        j += 1
                    if cur == col:
                        del out[k]
                        out.insert(j - 1, h)
                        continue
        k += 1
    return out


def page_nodes(page: dict, src: _Page, warn: list[str], where: str) -> list[dict]:
    out = _render(_heads_into_columns(page["blocks"], src.page), src, warn, where, False)
    big = [sc for sc in src.unplaced if _bbox(sc)[2] - _bbox(sc)[0] > 40]     # 작은 장식 조각은 알리지 않는다
    if big:
        warn.append(f"{where}: 자리를 찾지 못한 PDF 그림 {len(big)}개는 넣지 않음")
    return out


def _frame(nodes: list[dict]) -> list[dict]:
    """열 안: 큰 항목(section) 제목부터 항목마다 둥근 머리 박스. 그 앞(문서 제목·본표)은 틀 없이."""
    first = next((k for k, n in enumerate(nodes) if n["type"] == "heading" and n["attrs"]["level"] == 2), len(nodes))
    _strip(nodes[:first])
    return nodes[:first] + (_sectioned(nodes[first:]) if first < len(nodes) else [])


def _sectioned(nodes: list[dict], round_first=True) -> list[dict]:
    """section 제목마다 박스로 감싼다. columns=2 면 제목 아래 내용을 두 단으로."""
    out: list[dict] = []
    cur: list[dict] = []

    def close():
        nonlocal cur
        if not cur:
            return
        head = cur[0] if cur[0]["type"] == "heading" else None
        cols = head.pop("_columns", 1) if head else 1
        body = cur[1:] if head else cur
        if cols == 2 and body:
            body = [{"type": "columns", "attrs": {"count": 2}, "content": body}]
        out.append(box(([head] if head else []) + body, border=True, round=bool(head) and head["attrs"]["level"] <= 2 and round_first, pad=7))
        # 둥근 머리는 문서 제목·장 제목으로 시작하는 틀만. 조 제목으로 시작하는 틀은 앞 쪽에서 이어지는 틀이라 각지게
        cur = []

    for n in nodes:
        if n["type"] == "heading" and n["attrs"]["level"] <= 2 and cur:
            close()
        cur.append(n)
    close()
    return out


def _strip(nodes: list[dict]):
    for n in nodes:
        n.pop("_columns", None)


def to_editor(pages: list[dict], doc: Document, pdf_path=None) -> tuple[dict, list[str], dict]:
    blocks = list(doc.blocks)
    style = page_style(doc, blocks)
    content_w = style["page_w"] - 2 * PAD
    footer_top, in_table, gallery = classify_figures(doc, blocks)
    footers = {b.page_no: b for b in blocks if b.kind == "footer"}
    pdf_pages = {p.page_no: p for p in doc.pages}
    warn: list[str] = []
    # 1) 쪽마다 노드·꼬리말을 만든다
    per_page: list[list[dict]] = []
    feet: list[list[dict]] = []
    for i, page in enumerate(pages):
        src = _Page(page, [b for b in blocks if b.kind == "table" and b.page_no == i and b.content.get("cells") and b.bbox],
                    [b for b in blocks if b.kind == "figure" and b.page_no == i and b.bbox and b.content.get("asset")],
                    in_table, pdf_pages[i], footers.get(i), footer_top.get(i), content_w, style["body_pt"], pdf_path)
        nodes = page_nodes(page, src, warn, f"{i + 1}쪽")
        if not src.footer:
            # PDF 에서 꼬리말을 못 찾은 쪽: LLM 이 읽은 반복 머리말·꼬리말 글을 작은 띠로 남긴다(쪽 번호만인 줄은 뺀다)
            arts = [b["text"] for b in page["blocks"] if b["type"] == "page_artifact"
                    and not re.fullmatch(r"[\s\-–—]*\d+[\s\-–—/\d]*", b["text"])]
            if arts:
                src.footer = [box([{**_plain(t), "attrs": {"textAlign": "center"}} for t in arts],
                                  border=False, bleed=True, pad=0)]
        for n in src.footer:      # 법인 정보·꼬리말은 본문이 짧아도 쪽 맨 아래에 붙는다(편집기의 '쪽 아래 고정')
            n["attrs"]["pin"] = "bottom"
        per_page.append(nodes)
        feet.append(src.footer)

    # 2) 쪽을 넘어 이어지는 조는 앞 쪽의 그 조에 합친다(조가 쪽 경계에서 둘로 갈리지 않게).
    #    다음 쪽 첫머리의 제목 없는 내용(문단·표·안내 상자)은, 앞 쪽이 조 안에서 끝났으면 그 조의 나머지다.
    #    합친 조가 쪽 경계에 걸리면 편집기가 조째 다음 쪽으로 넘긴다
    for i in range(1, len(per_page)):
        prev, cur = per_page[i - 1], per_page[i]
        if pages[i].get("starts_mid_paragraph") and cur and cur[0]["type"] == "paragraph" and cur[0].get("content")                 and prev and prev[-1]["type"] == "paragraph":
            prev[-1].setdefault("content", []).extend(cur.pop(0)["content"])    # 문장 중간에서 이어지는 문단은 한 문단으로
        last_head = next((n for n in reversed(prev) if n["type"] == "heading"), None)
        if last_head is None or last_head["attrs"]["level"] != 3:
            continue
        k = next((x for x, n in enumerate(cur) if n["type"] == "heading"), len(cur))
        prev.extend(cur[:k])
        del cur[:k]

    # 3) 쪽마다 틀을 짜고 원본 쪽 경계에 부드러운 쪽 나눔을 둔다
    content: list[dict] = []
    for i, nodes in enumerate(per_page):
        if doc.doc_type == "terms":
            # 약관: 쪽·장마다 테두리 틀, 제목으로 시작하는 틀은 둥근 위 모서리(기존 변환과 같은 모양)
            body = _sectioned(nodes)
        else:
            # 상품설명서: 문서 제목 아래(본표)는 틀 없이, 큰 항목(section)부터 항목마다 둥근 머리 박스
            first = next((k for k, n in enumerate(nodes) if n["type"] == "heading" and n["attrs"]["level"] == 2), len(nodes))
            _strip(nodes[:first])
            body = nodes[:first] + _sectioned(nodes[first:])
        if i > 0 and (body or feet[i]):
            # 원본 쪽 경계. 앞 쪽이 넘쳐 이미 다음 쪽으로 흘렀으면 새 쪽을 또 만들지 않는 '부드러운' 쪽 나눔
            content.append({"type": "pageBreak", "attrs": {"soft": True}})
        content.extend(body)
        content.extend(feet[i])
    # 원본 쪽 맞춤(강제): 편집기가 원본 쪽마다 한 쪽에 들어가도록 본문 크기·줄 간격을 줄인다(한도: 이 크기의 85%)
    style = {**style, "autofit": True, "base_pt": style["body_pt"]}
    return {"type": "doc", "content": content or [{"type": "paragraph"}]}, warn, style
