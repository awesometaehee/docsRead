import { Extension, mergeAttributes, Node } from "@tiptap/core";
import Paragraph from "@tiptap/extension-paragraph";
import Color from "@tiptap/extension-color";
import Image from "@tiptap/extension-image";
import Heading from "@tiptap/extension-heading";
import Table, { TableView } from "@tiptap/extension-table";
import TextAlign from "@tiptap/extension-text-align";
import type { Node as PMNode } from "@tiptap/pm/model";
import TableCell from "@tiptap/extension-table-cell";
import TableHeader from "@tiptap/extension-table-header";
import TableRow from "@tiptap/extension-table-row";
import TextStyle from "@tiptap/extension-text-style";
import StarterKit from "@tiptap/starter-kit";
import type { JSONContent } from "@tiptap/core";
import type { Part, Slot } from "./types";

/** 변수: 문장 안의 바꿔 끼우는 자리. 같은 이름의 변수는 값을 함께 바꾼다. */
export const Variable = Node.create({
  name: "variable",
  group: "inline",
  inline: true,
  atom: true,
  selectable: true,
  marks: "_",
  addAttributes() {
    return {
      name: { default: "변수" },
      value: { default: "" },
      options: { default: [], rendered: false },
      vtype: { default: "문구", rendered: false },
    };
  },
  parseHTML() {
    return [{ tag: "span[data-variable]", getAttrs: (el) => ({ name: (el as HTMLElement).dataset.variable, value: (el as HTMLElement).textContent ?? "" }) }];
  },
  renderHTML({ node, HTMLAttributes }) {
    return ["span", mergeAttributes(HTMLAttributes, { "data-variable": node.attrs.name, class: "var", title: node.attrs.name }), node.attrs.value];
  },
  renderText({ node }) {
    return node.attrs.value;
  },
});

/** 모듈 블록: 공통 모듈에서 온 조·항목 묶음. 안의 글은 자유롭게 고칠 수 있다. */
export const ModuleBlock = Node.create({
  name: "moduleBlock",
  group: "block",
  content: "block+",
  defining: true,
  addAttributes() {
    return {
      moduleId: { default: null }, variantId: { default: null }, title: { default: "" }, kind: { default: "article" },
      nDocs: { default: 0 }, nVariants: { default: 1 }, ordinal: { default: 0 }, edited: { default: false },
    };
  },
  parseHTML() {
    return [{ tag: "div[data-module]" }];
  },
  renderHTML({ node }) {
    const a = node.attrs;
    const label = `모듈 · ${a.nDocs}개 문서 공통${a.nVariants > 1 ? ` · 문안 ${a.ordinal + 1}/${a.nVariants}` : ""}`;
    return ["div", { "data-module": a.moduleId, "data-label": label, class: "modblock" }, 0];
  },
});

/** 글자 크기(pt): 원본에서 본문과 다른 크기로 찍힌 글자를 그대로 옮긴다. */
const FontSize = Extension.create({
  name: "fontSize",
  addGlobalAttributes() {
    return [{
      types: ["textStyle"],
      attributes: {
        fontSize: {
          default: null,
          parseHTML: (el: HTMLElement) => parseFloat(el.style.fontSize) || null,
          renderHTML: (a: Record<string, unknown>) => (a.fontSize ? { style: `font-size:${a.fontSize}pt` } : {}),
        },
      },
    }];
  },
});

/** 문단 역할(hang = 글머리 기호 뒤 내어쓰기). */
const Para = Paragraph.extend({
  addAttributes() {
    return { role: { default: null, renderHTML: (a: Record<string, unknown>) => (a.role ? { class: String(a.role) } : {}) } };
  },
});

/** 여러 단 배치(유의사항의 2단). */
const Columns = Node.create({
  name: "columns",
  group: "block",
  content: "block+",
  addAttributes() { return { count: { default: 2 } }; },
  parseHTML() { return [{ tag: "div[data-columns]" }]; },
  renderHTML({ node }) { return ["div", { "data-columns": node.attrs.count, class: "cols", style: `column-count:${node.attrs.count}` }, 0]; },
});

/** 박스(틀·패널): 테두리, 둥근 위 모서리, 배경색, 용지 끝까지 채우기. 안에 무엇이든 넣을 수 있다. */
export const Box = Node.create({
  name: "box",
  group: "block",
  content: "block+",
  defining: true,
  addAttributes() {
    const hidden = { rendered: false };
    return {
      border: { default: true, ...hidden }, borderColor: { default: "#939598", ...hidden }, borderW: { default: 0.5, ...hidden },
      round: { default: false, ...hidden }, bg: { default: null, ...hidden }, pad: { default: 7, ...hidden }, bleed: { default: false, ...hidden },
    };
  },
  parseHTML() { return [{ tag: "div[data-box]" }]; },
  renderHTML({ node }) {
    const a = node.attrs;
    const style = [
      a.border ? `border:${a.borderW}pt solid ${a.borderColor}` : "", a.bg ? `background:${a.bg}` : "",
      a.round ? "border-radius:13pt 13pt 0 0" : "", `--bp:${a.pad}pt`,
    ].filter(Boolean).join(";");
    return ["div", { "data-box": "", class: `box${a.bleed ? " bleed" : ""}${a.round ? " round" : ""}`, style }, 0];
  },
});

/** 캡션 그림: 사진(여러 장을 겹쳐 놓을 수 있다)과 그 아래 캡션 글. */
export const Figure = Node.create({
  name: "figure",
  group: "block",
  content: "inline*",
  draggable: true,
  addAttributes() {
    const hidden = { rendered: false };
    return { w: { default: 80, ...hidden }, h: { default: 50, ...hidden }, images: { default: [], ...hidden },
      float: { default: null, ...hidden }, plain: { default: false, ...hidden } };   // plain = 캡션 자리 없이 그림만
  },
  parseHTML() { return [{ tag: "figure[data-figure]", contentElement: "figcaption" }]; },
  renderHTML({ node }) {
    const a = node.attrs as { w: number; h: number; float: string | null; plain: boolean; images: { src: string; x: number; y: number; w: number; h: number }[] };
    const imgs = a.images.map((im) => ["img", { src: im.src, draggable: "false", style: `left:${(im.x / a.w) * 100}%;top:${(im.y / a.h) * 100}%;width:${(im.w / a.w) * 100}%;height:${(im.h / a.h) * 100}%` }]);
    return ["figure", { "data-figure": "", class: `fig${a.plain ? " plain" : ""}`, style: `width:${a.w}pt${a.float ? `;float:${a.float}` : ""}` },
      ["div", { class: "figbox", contenteditable: "false", style: `aspect-ratio:${a.w} / ${a.h}` }, ...imgs],
      ["figcaption", 0]] as never;
  },
});

/** 사진 묶음: 캡션 그림을 가로로 늘어놓는 상자. 옆에 글이 오도록 띄울 수 있다. */
export const Gallery = Node.create({
  name: "gallery",
  group: "block",
  content: "figure+",
  addAttributes() {
    const hidden = { rendered: false };
    return { float: { default: null, ...hidden }, widthPct: { default: null, ...hidden }, bg: { default: null, ...hidden } };
  },
  parseHTML() { return [{ tag: "div[data-gallery]" }]; },
  renderHTML({ node }) {
    const a = node.attrs;
    const style = [a.float ? `float:${a.float}` : "", a.widthPct ? `width:${a.widthPct}%` : "", a.bg ? `background:${a.bg}` : ""].filter(Boolean).join(";");
    return ["div", { "data-gallery": "", class: `gallery${a.float ? " floated" : ""}`, style }, 0];
  },
});

/** 제목 띠의 배경색을 바꿀 수 있게 한다. */
const Head = Heading.extend({
  addAttributes() {
    return { ...this.parent?.(), bg: { default: null, renderHTML: (a: Record<string, unknown>) => (a.bg ? { style: `background:${a.bg}` } : {}) } };
  },
});

const SVG = "http://www.w3.org/2000/svg ";
type RItem = { k: string; b: number[]; d?: string; fill?: string; fo?: number; eo?: number; stroke?: string; sw?: number; so?: number;
  asset?: string; m?: number[]; spans?: { t: string; font: string; size: number; w: number; color: string; o: number[]; x1: number }[] };
const fam = (f: string) => (/nanum/i.test(f) ? "'Nanum Gothic', sans-serif" : "'Noto Sans KR', sans-serif");

/** 원본 영역(사진 묶음·꼬리말): 원본의 그리기 요소를 좌표째 담아 똑같이 그린다. 통째로 옮기거나 지울 수 있다. */
const Region = Node.create({
  name: "region",
  group: "block",
  atom: true,
  selectable: true,
  draggable: true,
  addAttributes() {
    const hidden = { rendered: false };
    return { x: { default: 0, ...hidden }, y: { default: 0, ...hidden }, w: { default: 100, ...hidden }, h: { default: 10, ...hidden },
      kind: { default: "gallery", ...hidden }, items: { default: [], ...hidden }, float: { default: null, ...hidden }, widthPct: { default: null, ...hidden } };
  },
  parseHTML() { return [{ tag: "div[data-region]" }]; },
  renderHTML({ node }) {
    const a = node.attrs as { x: number; y: number; w: number; h: number; kind: string; items: RItem[]; float: string | null; widthPct: number | null };
    const kids: unknown[] = [];
    a.items.forEach((it) => {
      if (it.k === "path") {
        const at: Record<string, string | number> = { d: it.d ?? "", fill: it.fill ?? "none", stroke: it.stroke ?? "none" };
        if (it.fo != null) at["fill-opacity"] = it.fo;
        if (it.eo) at["fill-rule"] = "evenodd";
        if (it.sw != null) at["stroke-width"] = it.sw;
        kids.push([`${SVG}path`, at]);
      } else if (it.k === "image" && it.m) {
        kids.push([`${SVG}image`, { href: `/api/assets/${it.asset}.png`, width: 1, height: 1, preserveAspectRatio: "none", transform: `matrix(${it.m.join(" ")})` }]);
      } else if (it.k === "text" && it.spans) {
        it.spans.forEach((sp) => {
          const len = sp.x1 - sp.o[0];
          const at: Record<string, string | number> = { x: sp.o[0], y: sp.o[1], "font-size": sp.size, "font-weight": sp.w, fill: sp.color, "font-family": fam(sp.font), style: "white-space:pre" };
          if (len > 0.5 && sp.t.trim().length > 1) { at.textLength = len; at.lengthAdjust = "spacing"; }
          kids.push([`${SVG}text`, at, sp.t]);
        });
      }
    });
    const style = [a.float ? `float:${a.float}` : "", a.widthPct ? `width:${a.widthPct}%` : ""].filter(Boolean).join(";");
    return ["div", { "data-region": a.kind, class: `region ${a.kind}${a.float ? " floated" : ""}`, style, contenteditable: "false" },
      [`${SVG}svg`, { viewBox: `${a.x} ${a.y} ${a.w} ${a.h}` }, ...kids]] as never;
  },
});

/** 이미지: 글줄 안에 놓이며 너비(pt)와 띄움(왼쪽·오른쪽)을 가진다. */
const Img = Image.extend({
  addAttributes() {
    return {
      ...this.parent?.(),
      widthPt: { default: null, renderHTML: () => ({}) },
      float: { default: null, renderHTML: () => ({}) },
    };
  },
  renderHTML({ node, HTMLAttributes }) {
    const a = node.attrs;
    const style = [a.widthPt ? `width:${a.widthPt}pt` : "", a.float ? `float:${a.float};margin:2pt ${a.float === "left" ? "6pt 2pt 0" : "0 2pt 6pt"}` : ""].filter(Boolean).join(";");
    return ["img", mergeAttributes(HTMLAttributes, style ? { style } : {})];
  },
}).configure({ inline: true });

const cellAttrs = {
  background: {
    default: null,
    parseHTML: (el: HTMLElement) => el.style.backgroundColor || null,
    renderHTML: () => ({}) as Record<string, string>,
  },
  align: { default: "left", renderHTML: () => ({}) },
  ln: { default: [1, 1, 1, 1], renderHTML: () => ({}) },   // 실제로 그어진 괘선: 위·오른쪽·아래·왼쪽
};
const SIDES = ["top", "right", "bottom", "left"];
const styled = (a: Record<string, unknown>) => {
  const ln = (a.ln as number[] | null) ?? [1, 1, 1, 1];
  const borders = SIDES.map((side, i) => `border-${side}:${ln[i] ? "var(--lw) solid var(--lc)" : "0"}`);
  const style = [a.background ? `background:${a.background}` : "", `text-align:${a.align ?? "left"}`, ...borders].filter(Boolean).join(";");
  return { style };
};
const Cell = TableCell.extend({
  addAttributes() { return { ...this.parent?.(), ...cellAttrs }; },
  renderHTML({ node, HTMLAttributes }) { return ["td", mergeAttributes(HTMLAttributes, styled(node.attrs)), 0]; },
});
const Header = TableHeader.extend({
  addAttributes() { return { ...this.parent?.(), ...cellAttrs }; },
  renderHTML({ node, HTMLAttributes }) { return ["th", mergeAttributes(HTMLAttributes, styled(node.attrs)), 0]; },
});
const Row = TableRow.extend({
  addAttributes() {
    return { ...this.parent?.(), h: { default: null, renderHTML: (a: Record<string, unknown>) => (a.h ? { style: `height:${a.h}pt` } : {}) } };
  },
});
function applyTableLook(dom: HTMLElement, node: PMNode) {
  const a = node.attrs;
  dom.style.setProperty("--lc", a.lineColor ?? "#231f20");
  dom.style.setProperty("--lw", `${Math.max(0.3, a.lineW ?? 0.5)}pt`);
  if (a.widthPct) dom.style.setProperty("--tw", `${a.widthPct}%`); else dom.style.removeProperty("--tw");
  dom.style.float = a.float ?? "";
  dom.classList.toggle("floated", !!a.float);
}
class LookTableView extends TableView {
  constructor(node: PMNode, cellMinWidth: number) {
    super(node, cellMinWidth);
    applyTableLook(this.dom as HTMLElement, node);
  }
  update(node: PMNode) {
    const ok = super.update(node);
    if (ok) applyTableLook(this.dom as HTMLElement, node);
    return ok;
  }
}

/** 표: 선 색·굵기와 배치(왼쪽 띄움·너비)를 원본에서 가져온다. */
const Tbl = Table.extend({
  addAttributes() {
    const hidden = { rendered: false };
    return { ...this.parent?.(), lineColor: { default: "#231f20", ...hidden }, lineW: { default: 0.5, ...hidden },
      float: { default: null, ...hidden }, widthPct: { default: null, ...hidden } };
  },
  renderHTML(props) {
    const spec = this.parent?.(props) as unknown as [string, Record<string, unknown>, ...unknown[]];
    const a = props.node.attrs;
    const extra = [`--lc:${a.lineColor}`, `--lw:${Math.max(0.3, a.lineW)}pt`, a.widthPct ? `--tw:${a.widthPct}%` : "", a.float ? `float:${a.float}` : ""].filter(Boolean).join(";");
    spec[1] = { ...spec[1], style: `${(spec[1]?.style as string | undefined) ?? ""};${extra}`, class: a.float ? "floated" : undefined };
    return spec as never;
  },
});

export const extensions = [
  StarterKit.configure({ heading: false, paragraph: false }),
  Head.configure({ levels: [1, 2, 3] }), Para, TextStyle, Color, FontSize, Img,
  // 기본 정렬은 용지 스타일(본문 양쪽, 제목 가운데)에 맡기고, 직접 고른 정렬만 문단에 기록한다
  TextAlign.configure({ types: ["paragraph", "heading"], defaultAlignment: null as never }),
  Tbl.configure({ resizable: true, View: LookTableView as never, cellMinWidth: 8, lastColumnResizable: true }), Row, Cell, Header,
  Variable, ModuleBlock, Columns, Region, Box, Figure, Gallery,
];

/** 모듈 문안(템플릿)을 편집기 인라인 노드로 만든다. 변수는 기본값으로 채운다. */
export function partsToInline(parts: Part[], slots: Slot[], moduleTitle: string): JSONContent[] {
  const short = (moduleTitle.split(" › ").pop() ?? "").slice(0, 10).trim();
  const out: JSONContent[] = [];
  parts.forEach((p) => {
    if ("t" in p) {
      if (p.t) out.push({ type: "text", text: p.t });
    } else {
      const s = slots[p.v];
      // 서버 변환과 같은 기준: 유형이 분명하거나 값이 여러 가지인 짧은 자리만 변수 칩으로 둔다
      if (!/[0-9A-Za-z가-힣]/.test(s.default) || s.default.length > 40 || (s.type === "문구" && s.n_values < 4)) {
        if (s.default) out.push({ type: "text", text: s.default });
        return;
      }
      out.push({ type: "variable", attrs: { name: `${short}·${s.name}`, value: s.default, options: s.top.map((t) => t[0]).slice(0, 8), vtype: s.type } });
    }
  });
  // 이웃한 글자 조각을 합친다(편집기는 같은 서식의 글자 노드가 나란히 있는 것을 허용하지 않는다)
  return out.reduce<JSONContent[]>((acc, n) => {
    const last = acc[acc.length - 1];
    if (n.type === "text" && last?.type === "text") last.text = (last.text ?? "") + (n.text ?? "");
    else acc.push(n);
    return acc;
  }, []);
}
