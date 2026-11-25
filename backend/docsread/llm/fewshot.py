"""검수된 편집 문서 → few-shot 예시(쪽 이미지 + 정답 JSON).

실행: uv run python -m docsread.llm.fewshot <편집문서 id> <원본 문서 id> <이름>
  → data/llm_fewshot/<문서종류>_<이름>_p<쪽>.png / .json

- 편집 문서의 쪽 나눔(pageBreak)으로 원본 쪽과 짝짓는다. 쪽 수가 다르면 멈춘다
- 쪽마다 원본 PDF 글자와 대조해 97% 미만이면 저장하지 않는다(틀린 정답을 가르치지 않기 위해)
- 표 칸 안의 그림은 규격에 자리가 없어 빠진다(글만 남는다)
"""
from __future__ import annotations

import argparse
import re
import sys

from ..db import Document, Draft, SessionLocal
from . import extract
from .job import _page_text, pdf_path
from .schema import PageOut

RED = re.compile(r"^#(?:e[0-9a-f]|d[0-9a-f]|f[0-9a-f])[0-3][0-9a-f][0-3][0-9a-f]$", re.I)   # #ed1c24 · #e60016 등
GRAY = {"#4d4d4f", "#6d6e71", "#808285", "#939598", "#58595b"}
NUMBERED = re.compile(r"^\s*(?:[①-⑳➀-➉]|\d{1,2}[.)]|[가-하][.)])")
BULLET = re.compile(r"^\s*[•※▶◆\-·]")


def _color(marks: list[dict]) -> str:
    for m in marks:
        if m["type"] == "textStyle":
            c = (m.get("attrs") or {}).get("color") or ""
            if RED.match(c):
                return "red"
            if c.lower() in GRAY:
                return "gray"
    return "default"


def _segments(node: dict, bold_all=False) -> list[dict]:
    out: list[dict] = []

    def walk(n):
        if n["type"] == "text":
            marks = n.get("marks") or []
            seg = {"text": n["text"], "bold": bold_all or any(m["type"] == "bold" for m in marks), "color": _color(marks)}
            if out and out[-1]["bold"] == seg["bold"] and out[-1]["color"] == seg["color"]:
                out[-1]["text"] += seg["text"]
            else:
                out.append(seg)
        elif n["type"] == "variable":
            text = (n.get("attrs") or {}).get("value") or ""
            if text:
                walk({"type": "text", "text": text})
        elif n["type"] == "hardBreak":
            if out:
                out[-1]["text"] += "\n"
        else:
            for k in n.get("content") or []:
                walk(k)

    walk(node)
    return out


def _style(text: str) -> str:
    return "numbered" if NUMBERED.match(text) else "bullet" if BULLET.match(text) else "body"


def _paragraph(n: dict) -> dict | None:
    segs = _segments(n)
    if not "".join(s["text"] for s in segs).strip():
        return None
    align = (n.get("attrs") or {}).get("textAlign") or "left"
    return {"type": "paragraph", "style": _style(segs[0]["text"]),
            "align": align if align in ("left", "center", "right") else "left", "segments": segs}


def _shade(bg: str | None) -> str:
    """칸 배경 hex → 규격의 배경 단계."""
    if not bg or bg.lower() == "#ffffff":
        return "none"
    r, g, b = (int(bg[i:i + 2], 16) / 255 for i in (1, 3, 5))
    if r > 0.95 and g > 0.95 and b < r - 0.03:
        return "cream"
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    return "light" if lum > 0.92 else "medium" if lum > 0.72 else "dark"


def _col_widths(n: dict) -> list[int]:
    """칸의 colwidth(px)로 열 비율(%)을 만든다. 모르는 열이 있으면 같은 너비."""
    widths: dict[int, float] = {}
    taken: set[tuple[int, int]] = set()
    ncol = 0
    for r, row in enumerate(n.get("content") or []):
        c = 0
        for cell in row.get("content") or []:
            while (r, c) in taken:
                c += 1
            a = cell.get("attrs") or {}
            cs, rs = a.get("colspan") or 1, a.get("rowspan") or 1
            for dr in range(rs):
                for dc in range(cs):
                    taken.add((r + dr, c + dc))
            cw = a.get("colwidth") or []
            if len(cw) == cs:
                for k, w in enumerate(cw):
                    widths.setdefault(c + k, w)
            c += cs
        ncol = max(ncol, c)
    if ncol == 0 or len(widths) < ncol:
        return [round(100 / ncol)] * ncol if ncol else []
    total = sum(widths[k] for k in range(ncol))
    pct = [max(1, round(widths[k] / total * 100)) for k in range(ncol)]
    return pct if min(pct) >= 3 else [round(100 / ncol)] * ncol     # 칸 너비 기록이 깨진 표는 같은 너비로


def _has_image(n: dict) -> bool:
    return n.get("type") in ("image", "figure") or any(_has_image(k) for k in n.get("content") or [])


def _table(n: dict) -> dict:
    rows = []
    for r in n.get("content") or []:
        cells = []
        for c in r.get("content") or []:
            a = c.get("attrs") or {}
            header = bool(a.get("background")) and a["background"].lower() not in ("#ffffff", "#fffef3")
            segs: list[dict] = []
            for i, p in enumerate(c.get("content") or []):
                ps = _segments(p, bold_all=False)
                if not ps:
                    continue
                if segs:
                    segs[-1]["text"] += "\n"
                segs.extend(ps)
            if header:      # 제목 칸 글자는 굵게(어두운 배경의 흰 글자는 색 없음으로)
                for s in segs:
                    s["bold"] = True
            cells.append({"segments": segs or [{"text": "", "bold": False, "color": "default"}], "header": header,
                          "shade": _shade(a.get("background")),
                          "image": _has_image(c),
                          "colspan": a.get("colspan") or 1, "rowspan": a.get("rowspan") or 1,
                          "align": a.get("align") if a.get("align") in ("left", "center", "right") else "left"})
        rows.append({"cells": cells})
    return {"type": "table", "col_widths": _col_widths(n), "rows": rows}


def _text(n: dict) -> str:
    return "".join(s["text"] for s in _segments(n)).strip()


def blocks_of(nodes: list[dict]) -> list[dict]:
    out: list[dict] = []
    for n in nodes:
        t = n["type"]
        a = n.get("attrs") or {}
        if t == "heading":
            kind = {1: "title", 2: "section"}.get(a.get("level"), "article_title")
            if _text(n):
                segs = _segments(n)
                head = {"type": kind, "text": _text(n), "color": "red" if any(x["color"] == "red" for x in segs) else "default"}
                if kind == "section":
                    head["columns"] = 1
                out.append(head)
        elif t == "paragraph":
            imgs = [k for k in n.get("content") or [] if k["type"] == "image"]
            for im in imgs:
                out.append({"type": "image", "caption": (im.get("attrs") or {}).get("alt") or ""})
            p = _paragraph(n)
            if p:
                out.append(p)
        elif t == "table":
            out.append(_table(n))
        elif t == "figure":
            out.append({"type": "image", "caption": _text(n)})
        elif t == "box" and a.get("pin") == "bottom":
            out.append({"type": "page_artifact", "text": _text(n)})
        elif t == "box" and (a.get("bg") or RED.match(a.get("borderColor") or "")) and not a.get("round"):
            paras = [p for p in (_paragraph(k) for k in n.get("content") or [] if k["type"] == "paragraph") if p]
            tone = "red" if RED.match(a.get("borderColor") or "") else "gray" if a.get("bg") else "plain"
            out.append({"type": "notice", "tone": tone, "paragraphs": paras})
        elif t == "columns" and (n.get("content") or [{}])[0].get("type") == "table" and len(n["content"]) > 1:
            # 표 | 안내문처럼 서로 다른 내용이 두 단에 놓인 것 → 좌우 나란히 영역
            out.append({"type": "side_start", "widths": [40, 60]})
            out.extend(blocks_of(n["content"][:1]))
            out.append({"type": "side_next"})
            out.extend(blocks_of(n["content"][1:]))
            out.append({"type": "side_end"})
        elif t == "split":
            cols = [c for c in n.get("content") or [] if c.get("type") == "splitCol"]
            ws = [float((c.get("attrs") or {}).get("w") or 1) for c in cols]
            out.append({"type": "side_start", "widths": [round(w / sum(ws) * 100) for w in ws]})
            for k, c in enumerate(cols):
                if k:
                    out.append({"type": "side_next"})
                out.extend(blocks_of(c.get("content") or []))
            out.append({"type": "side_end"})
        elif t == "columns":
            sec = next((b for b in reversed(out) if b["type"] in ("section", "title")), None)
            if sec is not None:     # 제목 아래 두 단 → 그 항목 제목에 columns=2
                sec["type"], sec["columns"] = "section", 2
            out.extend(blocks_of(n.get("content") or []))
        elif t in ("box", "splitCol", "moduleBlock", "region", "gallery"):
            out.extend(blocks_of(n.get("content") or []))      # 틀·단·나란히는 읽기 순서대로 펼친다
        # columnBreak 등 모양 전용 노드는 건너뛴다
    return out


def split_pages(doc: dict) -> list[list[dict]]:
    pages: list[list[dict]] = [[]]
    for n in doc.get("content") or []:
        if n["type"] == "pageBreak":
            pages.append([])
        else:
            pages[-1].append(n)
    return [p for p in pages if p]


FOOTER = re.compile(r"대표이사\s*:|E-?mail\s*:")      # 꼬리말 회사 정보 줄


def _cell_text(c: dict) -> str:
    return "".join(s["text"] for s in c["segments"]).strip()


def normalize(blocks: list[dict], pdf_tables: list) -> list[dict]:
    """편집 문서에서 옮긴 블록을 LLM 이 배울 모양으로 다듬는다.
    - 사진 칸 + 캡션 칸으로 된 표(사진 묶음을 표로 만든 것) → 안내 문단 + image(캡션)
    - 꼬리말 줄(대표이사·주소·누리집) 문단, 꼬리말 바로 앞의 설명 없는 그림(로고) → page_artifact / 제외
    - 표 열 비율은 글자가 맞는 PDF 표의 실제 열 너비로
    """
    out: list[dict] = []
    for b in blocks:
        if b["type"] == "table":
            rows = b["rows"]
            empty = [r for r in rows if all(not _cell_text(c) for c in r["cells"])]
            if empty and len(empty) * 2 >= len(rows) - 1:
                for r in rows:
                    if r in empty:
                        continue
                    if len(r["cells"]) == 1:
                        out.append({"type": "paragraph", "style": "bullet" if _cell_text(r["cells"][0])[:1] in "※•" else "body",
                                    "align": "left", "segments": r["cells"][0]["segments"]})
                    else:
                        out.extend({"type": "image", "caption": _cell_text(c)} for c in r["cells"])
                continue
            text = "".join(_cell_text(c) for r in rows for c in r["cells"])
            from .to_editor import _overlap
            best = max(pdf_tables, key=lambda t: _overlap(text, t.text), default=None)
            if best is not None and _overlap(text, best.text) >= 0.9 and len(best.content.get("col_w", [])) == len(b["col_widths"]):
                cw = best.content["col_w"]
                b["col_widths"] = [max(1, round(w / sum(cw) * 100)) for w in cw]
        if b["type"] == "paragraph" and FOOTER.search("".join(x["text"] for x in b["segments"])):
            out.append({"type": "page_artifact", "text": "".join(x["text"] for x in b["segments"]).strip()})
            continue
        out.append(b)
    # 꼬리말(page_artifact) 바로 앞이나 쪽 끝의 설명 없는 그림은 로고 → 뺀다
    cleaned: list[dict] = []
    for k, b in enumerate(out):
        nxt = out[k + 1] if k + 1 < len(out) else None
        if b["type"] == "image" and not b["caption"] and (nxt is None or nxt["type"] == "page_artifact"):
            continue
        cleaned.append(b)
    return cleaned


def place_photos(blocks: list[dict], page) -> list[dict]:
    """사진 묶음(이어진 image 와 바로 앞 ※ 안내 문단)을 PDF 위치상 속한 항목(section) 안, 그 항목 글 뒤로 옮긴다.
    편집 문서가 사진 묶음을 다른 곳(예: 본표 바로 뒤)에 두었더라도 예시는 원본 위치를 가르치게."""
    from .to_editor import _key
    texts = [(it, _key("".join(sp["t"] for sp in it.data["spans"]))) for it in page.items if it.kind == "text"]

    def y_of(text: str):
        k = _key(text)
        ys = [it.y0 for it, t in texts if k and t and (k in t or (len(t) >= 3 and t in k))]
        return min(ys) if ys else None

    pics = [it for it in page.items if it.kind == "image"]

    def cap_y(text: str):
        """캡션 글줄의 위치. 같은 글이 표 안에도 있을 수 있어, 바로 위(40pt 안)에 그림이 있는 줄만 본다."""
        k = _key(text)
        ys = [it.y0 for it, t in texts if k and t and (k in t or (len(t) >= 3 and t in k))
              and any(0 <= it.y0 - im.y1 < 40 and im.x0 - 6 <= (it.x0 + it.x1) / 2 <= im.x1 + 6 for im in pics)]
        return min(ys) if ys else None

    out = list(blocks)
    k = 0
    while k < len(out):
        if out[k]["type"] != "image":
            k += 1
            continue
        s = k - 1 if k > 0 and out[k - 1]["type"] == "paragraph" and \
            "".join(x["text"] for x in out[k - 1]["segments"]).lstrip().startswith("※") else k
        e = k
        while e < len(out) and out[e]["type"] == "image":
            e += 1
        ys = [y for y in (cap_y(b["caption"]) for b in out[k:e] if b["caption"]) if y is not None]
        secs = [(j, y_of(b["text"])) for j, b in enumerate(out) if b["type"] == "section"]
        above = [(j, y) for j, y in secs if y is not None and ys and y < min(ys)]
        if not above:
            k = e
            continue
        j = max(above, key=lambda t: t[1])[0]           # 사진 바로 위에 있는 항목
        end = next((x for x in range(j + 1, len(out)) if out[x]["type"] in ("section", "side_start")), len(out))
        if s <= j < e or s < end <= e or (j < s and e <= end):
            k = e                                       # 이미 그 항목 안
            continue
        run = out[s:e]
        del out[s:e]
        if j > s:
            j -= len(run)
            end -= len(run)
        out[end:end] = run
        k = 0 if end < s else end + len(run)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("draft_id", type=int)
    ap.add_argument("document_id", type=int)
    ap.add_argument("name")
    args = ap.parse_args(argv)
    if not re.fullmatch(r"[\w가-힣-]+", args.name):
        print("이름은 글자·숫자·_·- 만 쓸 수 있습니다")
        return 2
    with SessionLocal() as s:
        draft, doc = s.get(Draft, args.draft_id), s.get(Document, args.document_id)
        if draft is None or doc is None:
            print("편집 문서 또는 원본 문서를 찾을 수 없습니다")
            return 2
        pages = split_pages(draft.content)
        if len(pages) != doc.page_count:
            print(f"쪽 수가 다릅니다: 편집 문서 {len(pages)}쪽, 원본 {doc.page_count}쪽")
            return 1
        extract.FEWSHOT_DIR.mkdir(parents=True, exist_ok=True)
        failed = 0
        for i, nodes in enumerate(pages):
            blocks = normalize(blocks_of(nodes), [b for b in doc.blocks if b.kind == "table" and b.page_no == i])
            blocks = place_photos(blocks, doc.pages[i])
            for j, b in enumerate(blocks):     # 문서 제목은 첫 쪽 맨 위 하나뿐. 나머지 큰 제목은 장·큰 항목 제목
                if b["type"] == "title" and (i, j) != (0, 0):
                    b["type"] = "section"
                if b["type"] == "section":
                    b.setdefault("columns", 1)
                elif b["type"] == "title":
                    b.pop("columns", None)
            page = PageOut.model_validate({"starts_mid_paragraph": False, "blocks": blocks}).model_dump()
            from .validate import normalize as enforce
            page, fixes = enforce(page, i == 0)
            if fixes:
                print(f"{i + 1}쪽 예시 교정: {fixes}")
            f = extract.fidelity(_page_text(doc, i), extract.output_text(page))
            stem = extract.FEWSHOT_DIR / f"{doc.doc_type}_{args.name}_p{i + 1}"
            if f is None or f["recall"] < 0.97 or f["precision"] < 0.97:
                print(f"{i + 1}쪽: 원본과 글자가 맞지 않아 저장하지 않음 {f}")
                failed += 1
                continue
            stem.with_suffix(".png").write_bytes(extract.render_page(pdf_path(doc), i))
            stem.with_suffix(".json").write_text(PageOut.model_validate(page).model_dump_json(indent=1), encoding="utf-8")
            kinds = {}
            for b in page["blocks"]:
                kinds[b["type"]] = kinds.get(b["type"], 0) + 1
            print(f"{i + 1}쪽 저장: {stem.name} · 글자 대조 {f} · 블록 {kinds}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
