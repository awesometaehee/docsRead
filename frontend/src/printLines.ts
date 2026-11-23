/**
 * 인쇄할 때 표 괘선을 SVG 선으로 다시 긋는다.
 *
 * 크롬은 인쇄(PDF)할 때 1px 보다 얇은 CSS 테두리를 1px(0.75pt)로 올려 그린다. 그래서 0.3pt 괘선이 2.5배 굵어지고,
 * PDF 뷰어에서 배율에 따라 선마다 1~2화소로 달리 보인다. SVG 선(stroke)은 지정한 굵기 그대로 PDF 에 들어가므로,
 * 인쇄 직전에 칸마다 그어진 변을 모아 같은 자리에 SVG 선으로 긋고, CSS 테두리는 색만 투명하게 한다(자리·배치는 그대로).
 * 인쇄가 끝나면 원래대로 되돌린다.
 */

const PT = 96 / 72;

function toPx(v: string, fallbackPt: number): number {
  const s = v.trim();
  const n = parseFloat(s);
  if (!Number.isFinite(n)) return fallbackPt * PT;
  if (s.endsWith("pt")) return n * PT;
  if (s.endsWith("mm")) return (n * 96) / 25.4;
  return n;   // px
}

type Seg = { x1: number; y1: number; x2: number; y2: number; w: number; c: string };

function buildOverlay(paper: HTMLElement): SVGSVGElement | null {
  const prevZoom = paper.style.zoom;
  paper.style.zoom = "1";   // 인쇄 배율(1)에서 자리를 잰다
  try {
    const base = paper.getBoundingClientRect();
    const segs = new Map<string, Seg>();
    const add = (x1: number, y1: number, x2: number, y2: number, w: number, c: string) => {
      const r = (n: number) => Math.round(n * 20) / 20;
      const s = { x1: r(x1 - base.left), y1: r(y1 - base.top), x2: r(x2 - base.left), y2: r(y2 - base.top), w, c };
      segs.set(`${s.x1},${s.y1},${s.x2},${s.y2}`, s);
    };
    const visible = (style: string, width: string) => style !== "none" && style !== "hidden" && parseFloat(width) > 0;

    for (const table of paper.querySelectorAll<HTMLTableElement>("table")) {
      const ts = getComputedStyle(table);
      const w = toPx(ts.getPropertyValue("--lw"), 0.5);
      const cells = [...table.querySelectorAll<HTMLElement>("td, th")].filter((c) => c.closest("table") === table);
      if (!cells.length) continue;
      let L = Infinity, T = Infinity, R = -Infinity, B = -Infinity;
      for (const cell of cells) {
        const r = cell.getBoundingClientRect();
        if (!r.width || !r.height) continue;
        L = Math.min(L, r.left); T = Math.min(T, r.top); R = Math.max(R, r.right); B = Math.max(B, r.bottom);
        const cs = getComputedStyle(cell);
        if (visible(cs.borderTopStyle, cs.borderTopWidth)) add(r.left, r.top, r.right, r.top, w, cs.borderTopColor);
        if (visible(cs.borderBottomStyle, cs.borderBottomWidth)) add(r.left, r.bottom, r.right, r.bottom, w, cs.borderBottomColor);
        if (visible(cs.borderLeftStyle, cs.borderLeftWidth)) add(r.left, r.top, r.left, r.bottom, w, cs.borderLeftColor);
        if (visible(cs.borderRightStyle, cs.borderRightWidth)) add(r.right, r.top, r.right, r.bottom, w, cs.borderRightColor);
      }
      // 바깥 테두리(표 자체 테두리)
      if (L < R && T < B) {
        if (visible(ts.borderTopStyle, ts.borderTopWidth)) add(L, T, R, T, w, ts.borderTopColor);
        if (visible(ts.borderBottomStyle, ts.borderBottomWidth)) add(L, B, R, B, w, ts.borderBottomColor);
        if (visible(ts.borderLeftStyle, ts.borderLeftWidth)) add(L, T, L, B, w, ts.borderLeftColor);
        if (visible(ts.borderRightStyle, ts.borderRightWidth)) add(R, T, R, B, w, ts.borderRightColor);
      }
    }
    if (!segs.size) return null;

    const NS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("class", "print-lines");
    svg.setAttribute("width", String(paper.offsetWidth));
    svg.setAttribute("height", String(paper.offsetHeight));
    for (const s of segs.values()) {
      const line = document.createElementNS(NS, "line");
      line.setAttribute("x1", String(s.x1)); line.setAttribute("y1", String(s.y1));
      line.setAttribute("x2", String(s.x2)); line.setAttribute("y2", String(s.y2));
      line.setAttribute("stroke", s.c); line.setAttribute("stroke-width", String(s.w));
      line.setAttribute("stroke-linecap", "square");
      svg.append(line);
    }
    return svg;
  } finally {
    paper.style.zoom = prevZoom;
  }
}

/** beforeprint / afterprint 에 맞춰 괘선 덧그림을 붙였다 뗀다. 정리 함수를 돌려준다. */
export function installPrintLines(getPaper: () => HTMLElement | null): () => void {
  let svg: SVGSVGElement | null = null;
  let paper: HTMLElement | null = null;
  const after = () => {
    svg?.remove(); svg = null;
    paper?.classList.remove("print-lines-on"); paper = null;
  };
  const before = () => {
    after();
    paper = getPaper();
    if (!paper) return;
    svg = buildOverlay(paper);
    if (!svg) { paper = null; return; }
    paper.append(svg);
    paper.classList.add("print-lines-on");
  };
  window.addEventListener("beforeprint", before);
  window.addEventListener("afterprint", after);
  return () => { window.removeEventListener("beforeprint", before); window.removeEventListener("afterprint", after); after(); };
}
