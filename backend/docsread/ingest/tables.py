"""벡터 선분(괘선)과 채움 사각형으로부터 표의 격자·병합 셀을 복원한다.

InDesign 에서 내보낸 PDF 의 표는 '표 객체'가 아니라 선과 사각형의 모음이다.
PyMuPDF 의 find_tables 는 세로선이 없는 표(상품설명서 본표)를 찾지 못하므로,
선분 → 연결 요소(표 영역) → 격자 → 경계선이 없는 이웃 칸 병합 순으로 직접 복원한다.
좌표 단위는 PDF pt, 원점은 페이지 좌상단.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

TOUCH = 1.2      # 선분이 서로 닿았다고 보는 허용 오차
SNAP = 3.2       # 격자 좌표를 하나로 묶는 허용 오차(3pt 흰 여백 열을 경계로 흡수)
STACK_GAP = 70   # 세로선 없이 쌓인 가로선들을 한 표로 묶는 최대 간격
EDGE_COVER = 0.6 # 칸 변의 이 비율 이상을 선이 덮으면 경계선으로 인정


@dataclass
class Cell:
    r: int
    c: int
    rs: int
    cs: int
    bbox: tuple[float, float, float, float]
    borders: tuple[bool, bool, bool, bool]  # top, right, bottom, left
    fill: str | None = None
    lines: list = field(default_factory=list)  # 추출 단계에서 채운다
    ruled: tuple[bool, bool, bool, bool] = (False, False, False, False)  # 실제로 그어진(보이는) 괘선


@dataclass
class Table:
    bbox: tuple[float, float, float, float]
    xs: list[float]
    ys: list[float]
    cells: list[Cell]
    line_color: str = "#231f20"
    line_width: float = 0.5


class _UF:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, a: int) -> int:
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


def _hex(rgb) -> str | None:
    if rgb is None:
        return None
    return "#" + "".join(f"{max(0, min(255, round(v * 255))):02x}" for v in rgb[:3])


def collect_segments(drawings, page_w: float, page_h: float):
    """(H, V, fills, visible). H=(y,x0,x1), V=(x,y0,y1), fills=(x0,y0,x1,y1,hex,seqno).

    visible=(VH, VV, 색·굵기 빈도): 격자 경계 중 실제로 어두운 선으로 그어진 것만 따로 모은다.
    채움 사각형의 가장자리나 흰 선(칸 사이 여백)은 경계이긴 하지만 눈에 보이는 괘선은 아니다.
    """
    H, V, fills = [], [], []
    VH, VV, pens = [], [], {}
    page_area = page_w * page_h

    def add_rect(x0, y0, x1, y1):
        x0, x1 = sorted((x0, x1))
        y0, y1 = sorted((y0, y1))
        w, h = x1 - x0, y1 - y0
        if w < 2 and h < 2:
            return
        if h < 2:
            H.append(((y0 + y1) / 2, x0, x1))
        elif w < 2:
            V.append(((x0 + x1) / 2, y0, y1))
        elif w * h < page_area * 0.35:  # 페이지 테두리·큰 패널은 표가 아니다
            H.extend([(y0, x0, x1), (y1, x0, x1)])
            V.extend([(x0, y0, y1), (x1, y0, y1)])

    for g in drawings:
        items = g["items"]
        kind = g.get("type") or ""
        stroked, filled = "s" in kind, "f" in kind
        only_lines = all(it[0] == "l" for it in items)
        pen = _hex(g.get("color")) if stroked else None
        dark = pen is not None and pen != "#ffffff"
        if dark and only_lines:
            key = (pen, round(g.get("width") or 0.5, 2))
            pens[key] = pens.get(key, 0) + len(items)
        nH, nV = len(H), len(V)
        for it in items:
            if it[0] == "l" and stroked and only_lines:
                p, q = it[1], it[2]
                if abs(p.y - q.y) <= 0.6 and abs(p.x - q.x) > 2:
                    H.append(((p.y + q.y) / 2, min(p.x, q.x), max(p.x, q.x)))
                elif abs(p.x - q.x) <= 0.6 and abs(p.y - q.y) > 2:
                    V.append(((p.x + q.x) / 2, min(p.y, q.y), max(p.y, q.y)))
            elif it[0] == "re":
                r = it[1]
                if filled and not stroked and _hex(g.get("fill")) == "#ffffff":
                    continue  # 흰색 채움은 이미지 배경·가림 패치이므로 표 경계가 아니다
                add_rect(r.x0, r.y0, r.x1, r.y1)
                if filled and g.get("fill") is not None:
                    fills.append((min(r.x0, r.x1), min(r.y0, r.y1), max(r.x0, r.x1),
                                  max(r.y0, r.y1), _hex(g["fill"]), g.get("seqno", 0)))
            elif it[0] == "qu":
                r = it[1].rect
                if it[1].is_rectangular:
                    add_rect(r.x0, r.y0, r.x1, r.y1)
        if dark:
            VH.extend(H[nH:])
            VV.extend(V[nV:])
    return H, V, fills, (VH, VV, pens)


def _cluster(values: list[float], tol: float) -> list[float]:
    out: list[list[float]] = []
    for v in sorted(values):
        if out and v - out[-1][0] <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return [sum(g) / len(g) for g in out]


def _components(H, V):
    nH, nV = len(H), len(V)
    uf = _UF(nH + nV)
    if nH and nV:
        h = np.array(H)
        v = np.array(V)
        touch = ((v[None, :, 0] >= h[:, None, 1] - TOUCH) & (v[None, :, 0] <= h[:, None, 2] + TOUCH)
                 & (h[:, None, 0] >= v[None, :, 1] - TOUCH) & (h[:, None, 0] <= v[None, :, 2] + TOUCH))
        for i, j in zip(*np.nonzero(touch)):
            uf.union(int(i), nH + int(j))
    # 같은 직선 위에서 이어지는 선분
    for segs, off in ((H, 0), (V, nH)):
        order = sorted(range(len(segs)), key=lambda k: (round(segs[k][0] / TOUCH), segs[k][1]))
        for a, b in zip(order, order[1:]):
            if (abs(segs[a][0] - segs[b][0]) <= TOUCH and segs[b][1] <= segs[a][2] + TOUCH
                    and segs[a][1] <= segs[b][2] + TOUCH):
                uf.union(off + a, off + b)
    # 세로선 없이 같은 폭으로 쌓인 가로선(행 구분선만 있는 표)
    by_extent: dict[tuple[int, int], list[int]] = {}
    for k, (y, x0, x1) in enumerate(H):
        by_extent.setdefault((round(x0 / 2), round(x1 / 2)), []).append(k)
    for ks in by_extent.values():
        ks.sort(key=lambda k: H[k][0])
        for a, b in zip(ks, ks[1:]):
            if H[b][0] - H[a][0] <= STACK_GAP:
                uf.union(a, b)
    groups: dict[int, tuple[list, list]] = {}
    for k in range(nH):
        groups.setdefault(uf.find(k), ([], []))[0].append(H[k])
    for k in range(nV):
        groups.setdefault(uf.find(nH + k), ([], []))[1].append(V[k])
    return list(groups.values())


def _bbox(hs, vs):
    xs = [x for _, a, b in hs for x in (a, b)] + [x for x, _, _ in vs]
    ys = [y for y, _, _ in hs] + [y for _, a, b in vs for y in (a, b)]
    return (min(xs), min(ys), max(xs), max(ys))


def _merge_close(comps, gap: float = 4.0):
    """흰 여백(3pt)으로 떨어진 좌우/상하 조각을 하나의 표로 합친다."""
    comps = [(hs, vs, _bbox(hs, vs)) for hs, vs in comps if hs or vs]
    changed = True
    while changed:
        changed = False
        for i in range(len(comps)):
            for j in range(i + 1, len(comps)):
                a, b = comps[i][2], comps[j][2]
                if a[0] - gap <= b[2] and b[0] - gap <= a[2] and a[1] - gap <= b[3] and b[1] - gap <= a[3]:
                    hs, vs = comps[i][0] + comps[j][0], comps[i][1] + comps[j][1]
                    comps[i] = (hs, vs, _bbox(hs, vs))
                    del comps[j]
                    changed = True
                    break
            if changed:
                break
    return comps


def _covered(segs, pos: float, lo: float, hi: float) -> bool:
    """pos 위치의 선들이 구간 [lo,hi] 를 EDGE_COVER 이상 덮는가."""
    span = hi - lo
    if span <= 0:
        return False
    ivs = sorted((max(a, lo), min(b, hi)) for p, a, b in segs if abs(p - pos) <= SNAP and min(b, hi) > max(a, lo))
    total, cur = 0.0, lo
    for a, b in ivs:
        a = max(a, cur)
        if b > a:
            total += b - a
            cur = b
    return total >= span * EDGE_COVER


def _build(hs, vs, bbox, fills, visible) -> Table | None:
    xs = _cluster([x for x, _, _ in vs] + [x for _, a, b in hs for x in (a, b)], SNAP)
    ys = _cluster([y for y, _, _ in hs] + [y for _, a, b in vs for y in (a, b)], SNAP)
    nr, nc = len(ys) - 1, len(xs) - 1
    if nr < 2 or nc < 2 or nr * nc > 20000:
        return None  # 한 행·한 열짜리 상자는 표가 아니라 글상자(단 나눔·패널)로 본다
    # vedge[i][j]: 행 i 에서 x=xs[j] 경계선 존재, hedge[i][j]: 열 j 에서 y=ys[i] 경계선 존재
    vedge = [[_covered(vs, xs[j], ys[i], ys[i + 1]) for j in range(nc + 1)] for i in range(nr)]
    hedge = [[_covered(hs, ys[i], xs[j], xs[j + 1]) for j in range(nc)] for i in range(nr + 1)]
    uf = _UF(nr * nc)
    for i in range(nr):
        for j in range(nc):
            if j + 1 < nc and not vedge[i][j + 1]:
                uf.union(i * nc + j, i * nc + j + 1)
            if i + 1 < nr and not hedge[i + 1][j]:
                uf.union(i * nc + j, (i + 1) * nc + j)
    groups: dict[int, list[tuple[int, int]]] = {}
    for i in range(nr):
        for j in range(nc):
            groups.setdefault(uf.find(i * nc + j), []).append((i, j))
    rects: list[tuple[int, int, int, int]] = []
    for members in groups.values():
        r0, r1 = min(m[0] for m in members), max(m[0] for m in members)
        c0, c1 = min(m[1] for m in members), max(m[1] for m in members)
        if len(members) == (r1 - r0 + 1) * (c1 - c0 + 1):
            rects.append((r0, c0, r1, c1))
        else:  # 직사각형이 아닌 병합은 행 단위 조각으로 나눈다
            members.sort()
            run = [members[0]]
            for m in members[1:]:
                if m[0] == run[-1][0] and m[1] == run[-1][1] + 1:
                    run.append(m)
                else:
                    rects.append((run[0][0], run[0][1], run[0][0], run[-1][1]))
                    run = [m]
            rects.append((run[0][0], run[0][1], run[0][0], run[-1][1]))
    cells = []
    for r0, c0, r1, c1 in sorted(rects):
        cb = (xs[c0], ys[r0], xs[c1 + 1], ys[r1 + 1])
        cx, cy = (cb[0] + cb[2]) / 2, (cb[1] + cb[3]) / 2
        fill, best = None, -1
        for fx0, fy0, fx1, fy1, color, seq in fills:
            if fx0 <= cx <= fx1 and fy0 <= cy <= fy1 and seq >= best:
                fill, best = color, seq
        borders = (
            all(hedge[r0][j] for j in range(c0, c1 + 1)),
            all(vedge[i][c1 + 1] for i in range(r0, r1 + 1)),
            all(hedge[r1 + 1][j] for j in range(c0, c1 + 1)),
            all(vedge[i][c0] for i in range(r0, r1 + 1)),
        )
        vh, vv, _ = visible
        ruled = (
            all(_covered(vh, ys[r0], xs[j], xs[j + 1]) for j in range(c0, c1 + 1)),
            all(_covered(vv, xs[c1 + 1], ys[i], ys[i + 1]) for i in range(r0, r1 + 1)),
            all(_covered(vh, ys[r1 + 1], xs[j], xs[j + 1]) for j in range(c0, c1 + 1)),
            all(_covered(vv, xs[c0], ys[i], ys[i + 1]) for i in range(r0, r1 + 1)),
        )
        cells.append(Cell(r0, c0, r1 - r0 + 1, c1 - c0 + 1, cb, borders,
                          None if fill in (None, "#ffffff") else fill, ruled=ruled))
    table = Table((xs[0], ys[0], xs[-1], ys[-1]), xs, ys, cells)
    if visible[2]:
        (table.line_color, table.line_width), _ = max(visible[2].items(), key=lambda kv: kv[1])
    return table


def find_tables(drawings, page_w: float, page_h: float) -> list[Table]:
    H, V, fills, visible = collect_segments(drawings, page_w, page_h)
    if not H and not V:
        return []
    tables = []
    for hs, vs, bbox in _merge_close(_components(H, V)):
        t = _build(hs, vs, bbox, fills, visible)
        if t is not None:
            tables.append(t)
    tables.sort(key=lambda t: (t.bbox[1], t.bbox[0]))
    return tables
