"""LLM 응답 검증·교정. 출력 규격(JSON Schema)은 모양만 강제하므로, 프롬프트의 규칙 중 코드로 확인할 수 있는 것은 여기서 강제한다.

- 고칠 수 있는 것(정답이 하나로 정해짐)은 고치고, 무엇을 고쳤는지 notes 로 남긴다
- 고칠 수 없는 것(글자 빠뜨림·지어냄)은 problems 로 돌려주고, job 이 이전 응답과 함께 한 번 더 요청한다
"""
from __future__ import annotations

import os

from . import extract

# 다시 요청(비용 2배)은 크게 틀린 경우에만: 쪽 글자 대조가 이보다 낮을 때. 그 위(~97%)는 경고만 남긴다(job.MIN_RECALL)
MIN_TEXT_MATCH = 0.90
RETRY = os.environ.get("DOCSREAD_LLM_RETRY", "1").strip() not in ("0", "false", "no", "")


def _grid_width(rows: list[dict]) -> int:
    taken: set[tuple[int, int]] = set()
    width = 0
    for r, row in enumerate(rows):
        c = 0
        for cell in row["cells"]:
            while (r, c) in taken:
                c += 1
            for dr in range(cell["rowspan"]):
                for dc in range(cell["colspan"]):
                    taken.add((r + dr, c + dc))
            c += cell["colspan"]
        width = max(width, c)
    return width


def _percent(ws: list[int], n: int) -> list[int]:
    """열 비율을 n 개, 합 100 으로. 모자라거나 남으면 같은 너비."""
    if len(ws) != n or n == 0 or sum(max(0, w) for w in ws) <= 0:
        return [round(100 / n)] * n if n else []
    ws = [max(1, w) for w in ws]
    total = sum(ws)
    raw = [max(3.0, w / total * 100) for w in ws]
    out = [int(x) for x in raw]
    # 남는 몫은 소수점이 큰 열부터 1씩(한 열에 몰리지 않게)
    for k in sorted(range(n), key=lambda k: raw[k] - out[k], reverse=True)[:max(0, 100 - sum(out))]:
        out[k] += 1
    return out


def normalize(page: dict, first_page: bool) -> tuple[dict, list[str]]:
    notes: list[str] = []
    blocks: list[dict] = []

    # 1) 문서 제목은 첫 쪽 맨 위 하나뿐. 나머지 title 은 장·큰 항목 제목(section)
    seen_content = False
    for b in page["blocks"]:
        t = b["type"]
        if t == "title" and (not first_page or seen_content):
            notes.append(f"두 번째 문서 제목 '{b['text'][:20]}' → 장 제목")
            b = {"type": "section", "text": b["text"], "color": b.get("color", "default"), "columns": 1}
        if t == "paragraph" and not "".join(s["text"] for s in b["segments"]).strip():
            continue                                              # 빈 문단
        if t not in ("page_artifact", "side_start", "side_next", "side_end"):
            seen_content = True
        blocks.append(b)

    # 2) 나란히 표시: 시작·다음·끝 짝 맞추기(겹침 없음), 열 비율 = 열 수·합 100, 열이 하나뿐이면 풀기
    fixed: list[dict] = []
    region: list[list[dict]] | None = None
    start: dict | None = None

    def close(reason: str | None = None):
        nonlocal region, start
        if region is None:
            return
        if reason:
            notes.append(reason)
        cols = [c for c in region if c]
        if len(cols) < 2:
            notes.append("내용 있는 열이 하나뿐인 나란히 영역 → 풀어서 위아래로")
            for c in cols:
                fixed.extend(c)
        else:
            if len(cols) != len(region):
                notes.append("빈 열 제거")
            ws = _percent(start["widths"] if len(cols) == len(region) else [], len(cols))
            if ws != start["widths"]:
                notes.append(f"나란히 열 비율 {start['widths']} → {ws}")
            fixed.append({"type": "side_start", "widths": ws})
            for k, c in enumerate(cols):
                if k:
                    fixed.append({"type": "side_next"})
                fixed.extend(c)
            fixed.append({"type": "side_end"})
        region, start = None, None

    for b in blocks:
        t = b["type"]
        if t == "side_start":
            close("닫히지 않은 나란히 영역을 새 영역 앞에서 닫음")
            region, start = [[]], b
        elif t == "side_next":
            if region is None:
                notes.append("나란히 영역 밖의 '다음 열' 표시 제거")
            else:
                region.append([])
        elif t == "side_end":
            if region is None:
                notes.append("짝 없는 '나란히 끝' 표시 제거")
            else:
                close()
        elif t == "page_artifact" or region is None:
            fixed.append(b)
        else:
            region[-1].append(b)
    close("쪽 끝까지 닫히지 않은 나란히 영역을 닫음")

    # 3) 표: 병합 칸 수 ≥ 1, 세로 병합은 표 끝까지만, 열 비율 = 실제 열 수·합 100
    for b in fixed:
        if b["type"] != "table":
            continue
        b["rows"] = [r for r in b["rows"] if r["cells"]]
        n = len(b["rows"])
        for r, row in enumerate(b["rows"]):
            for cell in row["cells"]:
                cell["colspan"] = max(1, cell["colspan"])
                cell["rowspan"] = max(1, min(cell["rowspan"], n - r))
        width = _grid_width(b["rows"])
        ws = _percent(b.get("col_widths") or [], width)
        if ws != b.get("col_widths"):
            notes.append(f"표 열 비율 {b.get('col_widths')} → {ws}")
            b["col_widths"] = ws

    return {**page, "blocks": fixed}, notes


def problems(page: dict, pdf_text: str) -> list[str]:
    """코드로 고칠 수 없는 문제(다시 요청할 이유)."""
    out = []
    f = extract.fidelity(pdf_text, extract.output_text(page))
    if f is not None:
        if f["recall"] < MIN_TEXT_MATCH:
            out.append(f"원문 글자의 {100 * (1 - f['recall']):.1f}% 를 옮기지 않았습니다. 빠진 문단·표 칸·캡션이 없는지 쪽 전체를 다시 확인하세요")
        if f["precision"] < MIN_TEXT_MATCH:
            out.append(f"옮긴 글자의 {100 * (1 - f['precision']):.1f}% 가 원문에 없습니다. 잘못 읽었거나 지어낸 글자를 원문 그대로 고치세요")
    return out
