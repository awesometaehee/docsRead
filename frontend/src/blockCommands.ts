/**
 * 문서 구조 다루기: 박스 나누기·합치기·풀기·넣고 빼기, 표·박스 앞뒤에 줄 넣기, 쪽 나눔.
 * 모두 ProseMirror 트랜잭션 한 번으로 처리하므로 되돌리기(Ctrl+Z) 한 번에 원상복구된다.
 */
import { Extension, Node } from "@tiptap/core";
import type { Editor } from "@tiptap/core";
import type { Node as PMNode, ResolvedPos } from "@tiptap/pm/model";
import { NodeSelection, Plugin, PluginKey, TextSelection } from "@tiptap/pm/state";
import { Decoration, DecorationSet } from "@tiptap/pm/view";
import type { EditorState, Transaction } from "@tiptap/pm/state";
import { canSplit, liftTarget } from "@tiptap/pm/transform";

/** 안에 줄을 이어 쓸 수 없는 '닫힌' 블록: 그 바로 앞뒤에 커서를 둘 문단이 필요하다. */
const CLOSED = new Set(["table", "box", "columns", "figure", "gallery", "region", "moduleBlock", "pageBreak"]);

function ancestorDepth($pos: ResolvedPos, name: string): number {
  for (let d = $pos.depth; d > 0; d--) if ($pos.node(d).type.name === name) return d;
  return -1;
}

/* ---------- 1. 표·박스 앞뒤에 줄 넣기 ---------- */

/** pos 자리에 빈 문단을 넣고 그 안에 커서를 둔다. */
function insertParagraphAt(tr: Transaction, pos: number) {
  tr.insert(pos, tr.doc.type.schema.nodes.paragraph.create());
  tr.setSelection(TextSelection.create(tr.doc, pos + 1));
  return tr.scrollIntoView();
}

/** 커서를 감싼 name 블록(가장 안쪽)의 바로 앞/뒤에 빈 문단을 넣는다. */
export function addLineNear(editor: Editor, name: string, after: boolean): boolean {
  const { state, view } = editor;
  const d = ancestorDepth(state.selection.$from, name);
  if (d < 0) return false;
  const $p = state.selection.$from;
  view.dispatch(insertParagraphAt(state.tr, after ? $p.after(d) : $p.before(d)));
  view.focus();
  return true;
}

/**
 * ↓(↑)로 더 갈 곳이 없을 때: 커서가 마지막(첫) 줄에 있고, 감싼 닫힌 블록이 부모의 마지막(첫) 자식이면
 * 그 블록 바로 뒤(앞)에 빈 문단을 만든다. 가장 안쪽 블록 기준이므로 박스 끝의 표 뒤에 쓰면 박스 안에 줄이 생긴다.
 */
function escapeClosedBlock(editor: Editor, down: boolean): boolean {
  const { state, view } = editor;
  const sel = state.selection;
  if (!sel.empty || !view.endOfTextblock(down ? "down" : "up")) return false;
  const $h = sel.$head;
  const paraType = state.schema.nodes.paragraph;
  for (let d = $h.depth; d >= 1; d--) {
    const parent = $h.node(d - 1);
    const idx = $h.index(d - 1);
    // 표의 칸은 가로 위치와 상관없이 위아래로 움직이므로 '행'만 끝인지 본다
    const edge = parent.type.name === "tableRow" || (down ? idx === parent.childCount - 1 : idx === 0);
    if (!edge) return false;   // 그 방향에 형제가 있으면 기본 이동에 맡긴다
    const node = $h.node(d);
    // ↑로는 박스 앞에 줄을 만들지 않는다(문서 맨 위 제목 박스에서 빈 줄이 자꾸 생기지 않게)
    if (!CLOSED.has(node.type.name) || (!down && node.type.name === "box")) continue;
    const at = down ? idx + 1 : idx;
    if (!parent.canReplaceWith(at, at, paraType)) continue;
    view.dispatch(insertParagraphAt(state.tr, down ? $h.after(d) : $h.before(d)));
    return true;
  }
  return false;
}

/* ---------- 2. 박스 나누기·합치기, 빈 줄 Enter ---------- */

/** 커서가 있는 줄부터 뒤를 새 박스로 나눈다(박스 속성은 그대로 복사). */
export function splitBox(editor: Editor): boolean {
  const { state, view } = editor;
  const $f = state.selection.$from;
  const d = ancestorDepth($f, "box");
  if (d < 0) return false;
  const idx = $f.index(d);
  if (idx === 0) return false;   // 맨 앞 줄에서는 나눌 것이 없다
  const pos = $f.posAtIndex(idx, d);   // 박스 안 idx 번째 자식의 시작
  if (!canSplit(state.doc, pos, 1)) return false;
  const tr = state.tr.split(pos, 1);
  tr.setSelection(TextSelection.near(tr.doc.resolve(pos + 2)));
  view.dispatch(tr.scrollIntoView());
  view.focus();
  return true;
}

/** 쪽 배치가 요청한 자리(박스 안의 요소)에서 박스를 둘로 자른다. 뒤 박스는 이어지는 틀이라 둥근 머리를 두지 않는다. */
export function splitBoxAtDom(editor: Editor, el: HTMLElement): boolean {
  const { state, view } = editor;
  let inside: number;
  try { inside = view.posAtDOM(el, 0); } catch { return false; }
  const $p = state.doc.resolve(Math.min(inside, state.doc.content.size));
  let d = $p.depth;
  while (d > 0 && $p.node(d).type.name !== "box") d--;
  if (d <= 0) return false;
  const idx = $p.index(d);
  if (idx === 0) return false;
  const pos = $p.posAtIndex(idx, d);
  if (!canSplit(state.doc, pos, 1)) return false;
  const box = $p.node(d);
  const tr = state.tr.split(pos, 1, [{ type: box.type, attrs: { ...box.attrs, round: false, pin: null, cont: true } }]);
  tr.setMeta("addToHistory", false);
  view.dispatch(tr);
  return true;
}

/** 쪽 배치가 자른 박스(cont)를 바로 앞 박스와 다시 합친다(더 넘길 필요가 없어졌을 때). */
export function joinBoxAtDom(editor: Editor, el: HTMLElement): boolean {
  const { state, view } = editor;
  let inside: number;
  try { inside = view.posAtDOM(el, 0); } catch { return false; }
  const $p = state.doc.resolve(Math.min(inside, state.doc.content.size));
  let d = $p.depth;
  while (d > 0 && !($p.node(d).type.name === "box" && $p.node(d).attrs.cont)) d--;
  if (d <= 0) return false;
  const at = $p.before(d);
  if (state.doc.resolve(at).nodeBefore?.type.name !== "box") return false;
  const tr = state.tr.join(at);
  tr.setMeta("addToHistory", false);
  view.dispatch(tr);
  return true;
}

/** 박스 바로 앞(사이에 쪽 나눔만 있어도 됨)의 박스와 하나로 합친다. */
export function joinPrevBox(editor: Editor): boolean {
  const { state, view } = editor;
  const $f = state.selection.$from;
  const d = ancestorDepth($f, "box");
  if (d < 0) return false;
  const tr = state.tr;
  let pos = $f.before(d);
  let before = tr.doc.resolve(pos).nodeBefore;
  if (before?.type.name === "pageBreak") {   // 쪽 나눔을 사이에 둔 두 박스: 쪽 나눔을 지우고 합친다
    tr.delete(pos - before.nodeSize, pos);
    pos -= before.nodeSize;
    before = tr.doc.resolve(pos).nodeBefore;
  }
  if (before?.type.name !== "box") return false;
  tr.join(pos);
  view.dispatch(tr.scrollIntoView());
  view.focus();
  return true;
}

export function canJoinPrevBox(state: EditorState): boolean {
  const $f = state.selection.$from;
  const d = ancestorDepth($f, "box");
  if (d < 0) return false;
  const $p = state.doc.resolve($f.before(d));
  let before = $p.nodeBefore;
  if (before?.type.name === "pageBreak") before = state.doc.resolve($p.pos - before.nodeSize).nodeBefore;
  return before?.type.name === "box";
}

/** 박스·단 안의 빈 줄에서 Enter: 박스를 쪼개지 않고 빈 줄을 하나 더 만든다. */
function enterInContainer(editor: Editor): boolean {
  const { state, view } = editor;
  const { $from, empty } = state.selection;
  if (!empty || !$from.parent.isTextblock || $from.parent.content.size > 0 || $from.depth < 2) return false;
  if (!["box", "columns", "moduleBlock"].includes($from.node($from.depth - 1).type.name)) return false;
  view.dispatch(insertParagraphAt(state.tr, $from.after()));
  return true;
}

/* ---------- 4. 박스 풀기·빼기·넣기 ---------- */

/** 가장 안쪽 박스를 통째로 없앤다(내용은 그 자리에 남는다). */
export function unwrapBox(editor: Editor): boolean {
  const { state, view } = editor;
  const $f = state.selection.$from;
  const d = ancestorDepth($f, "box");
  if (d < 0) return false;
  const box = $f.node(d);
  const start = $f.before(d);
  const tr = state.tr.replaceWith(start, start + box.nodeSize, box.content);
  tr.setSelection(TextSelection.near(tr.doc.resolve(Math.min(tr.mapping.map(state.selection.from), tr.doc.content.size))));
  view.dispatch(tr.scrollIntoView());
  view.focus();
  return true;
}

/** 선택한 줄들만 가장 안쪽 박스 밖으로 뺀다. 가운데 줄이면 박스가 앞뒤로 나뉜다. */
export function liftOutOfBox(editor: Editor): boolean {
  const { state, view } = editor;
  const { $from, $to } = state.selection;
  const d = ancestorDepth($from, "box");
  if (d < 0) return false;
  const box = $from.node(d);
  // 선택 끝이 같은 박스를 벗어나면 박스의 마지막 줄까지로 자른다
  const end = $to.depth > d && $to.node(d) === box ? $to.pos : $from.end(d) - 1;
  const range = $from.blockRange(state.doc.resolve(Math.max(end, $from.pos)), (n) => n === box);
  if (!range) return false;
  const target = liftTarget(range);
  if (target == null) return false;
  view.dispatch(state.tr.lift(range, target).scrollIntoView());
  view.focus();
  return true;
}

/** 선택한 줄들(같은 부모 안)의 바로 앞 형제가 박스이면 그 박스의 끝으로 옮긴다. */
function intoPrevBoxRange(state: EditorState) {
  const { $from, $to } = state.selection;
  const range = $from.blockRange($to);
  if (!range) return null;
  const before = state.doc.resolve(range.start).nodeBefore;
  return before?.type.name === "box" ? range : null;
}

export function canMoveIntoPrevBox(state: EditorState): boolean {
  return intoPrevBoxRange(state) !== null;
}

export function moveIntoPrevBox(editor: Editor): boolean {
  const { state, view } = editor;
  const range = intoPrevBoxRange(state);
  if (!range) return false;
  const slice = state.doc.slice(range.start, range.end);
  const tr = state.tr.delete(range.start, range.end);
  const at = range.start - 1;   // 앞 박스의 닫는 자리 바로 앞
  tr.insert(at, slice.content);
  tr.setSelection(TextSelection.near(tr.doc.resolve(at + 1)));
  view.dispatch(tr.scrollIntoView());
  view.focus();
  return true;
}

/* ---------- 5. 쪽 나눔 ---------- */

/** 쪽 나눔: 인쇄 시 다음 쪽으로 넘기고, 편집 화면에서는 다음 쪽 경계까지 빈 공간을 둔다(layoutPages 가 높이를 정함). */
export const PageBreak = Node.create({
  name: "pageBreak",
  group: "block",
  atom: true,
  selectable: true,
  // soft: 원본 쪽 경계(AI 변환이 넣음). 앞 쪽 내용이 이미 다음 쪽으로 넘쳐 흘렀으면 새 쪽을 또 만들지 않는다
  addAttributes() {
    return { soft: { default: false, parseHTML: (el: HTMLElement) => el.hasAttribute("data-soft"), renderHTML: () => ({}) } };
  },
  parseHTML() { return [{ tag: "div[data-pagebreak]" }]; },
  renderHTML({ node }) {
    return ["div", { "data-pagebreak": "", class: `pagebreak${node.attrs.soft ? " soft" : ""}`, ...(node.attrs.soft ? { "data-soft": "" } : {}) }];
  },
});

/**
 * 커서 자리에서 쪽을 나눈다. 박스 안이면 박스를 둘로 나누고 그 사이에 쪽 나눔을 둔다
 * (원본처럼 쪽마다 테두리가 따로 그려진다). 표 안이면 표 앞에서 나눈다.
 */
export function insertPageBreak(editor: Editor): boolean {
  const { state, view } = editor;
  const $f = state.selection.$from;
  // 나눌 기준 블록: 표 안이면 표, 아니면 커서가 있는 줄
  let d = ancestorDepth($f, "table");
  if (d < 0) d = $f.depth;
  // 부모의 첫 자식이면 한 단계씩 올라간다(빈 박스가 생기지 않게)
  while (d > 1 && $f.index(d - 1) === 0) d--;
  const tr = state.tr;
  const br = state.schema.nodes.pageBreak.create();
  let at: number;
  if (d <= 1) {
    at = $f.before(1);
    tr.insert(at, br);
  } else {
    const pos = $f.before(d);
    if (!canSplit(state.doc, pos, d - 1)) return false;
    tr.split(pos, d - 1);
    at = pos + (d - 1);   // 나뉜 두 최상위 블록 사이
    tr.insert(at, br);
  }
  tr.setSelection(TextSelection.near(tr.doc.resolve(at + br.nodeSize)));
  view.dispatch(tr.scrollIntoView());
  view.focus();
  return true;
}

/* ---------- 6. 내어쓰기 ---------- */

/** 문단 앞 번호·기호: ① 1. 1) (1) 가. ※ ▶ - • 등과 뒤따르는 공백. */
const MARKER = /^\s*(?:[①-⑳㉑-㉟]|\(\d{1,2}\)|\d{1,2}[.)]|[가나다라마바사아자차카타파하][.)]|[•※▶▷◆◇■□○●◎\-·*])\s*/;

export function markerOf(text: string): string | null {
  const m = MARKER.exec(text);
  return m && m[0].trim() ? m[0] : null;
}

let canvas: HTMLCanvasElement | null = null;
/** 문단에 실제로 적용된 글꼴로 글자 폭을 재서 em 으로 돌려준다(자간 포함). */
function measureEm(text: string, el: HTMLElement): number {
  const cs = getComputedStyle(el);
  const size = parseFloat(cs.fontSize) || 12;
  canvas ??= document.createElement("canvas");
  const ctx = canvas.getContext("2d")!;
  ctx.font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
  const ls = parseFloat(cs.letterSpacing) || 0;
  const w = ctx.measureText(text).width + ls * text.length;
  return Math.round((w / size) * 100) / 100;
}

/** pos 의 문단에 맞는 내어쓰기 폭(em). 앞 기호가 없으면 null. */
function autoHang(editor: Editor, node: PMNode, pos: number): number | null {
  const marker = markerOf(node.textContent);
  if (!marker) return null;
  const dom = editor.view.nodeDOM(pos);
  return dom instanceof HTMLElement ? measureEm(marker, dom) : null;
}

function selectedParagraphs(state: EditorState): { node: PMNode; pos: number }[] {
  const out: { node: PMNode; pos: number }[] = [];
  const { from, to } = state.selection;
  state.doc.nodesBetween(from, to, (node, pos) => {
    if (node.type.name === "paragraph") out.push({ node, pos });
    return !node.isTextblock;
  });
  return out;
}

export function hangOf(state: EditorState): number | null {
  const p = selectedParagraphs(state)[0];
  if (!p) return null;
  return (p.node.attrs.hang as number | null) ?? (p.node.attrs.role === "hang" ? 1.05 : null);
}

/** 선택한 문단의 내어쓰기를 켜고 끈다. 켤 때 폭은 앞 기호의 실제 폭(기호가 없으면 1em). */
export function toggleHang(editor: Editor): boolean {
  const { state, view } = editor;
  const paras = selectedParagraphs(state);
  if (!paras.length) return false;
  const on = hangOf(state) === null;
  const tr = state.tr;
  for (const { node, pos } of paras) {
    const attrs = { ...node.attrs, hang: on ? (autoHang(editor, node, pos) ?? 1) : null,
      role: !on && node.attrs.role === "hang" ? null : node.attrs.role };
    tr.setNodeMarkup(pos, undefined, attrs);
  }
  view.dispatch(tr);
  return true;
}

export function setHang(editor: Editor, em: number | null): boolean {
  const { state, view } = editor;
  const paras = selectedParagraphs(state);
  if (!paras.length) return false;
  const tr = state.tr;
  for (const { node, pos } of paras) {
    tr.setNodeMarkup(pos, undefined, { ...node.attrs, hang: em && em > 0 ? em : null, role: node.attrs.role === "hang" ? null : node.attrs.role });
  }
  view.dispatch(tr);
  return true;
}

/** 문서 전체에서 번호·기호로 시작하는 문단에 그 기호 폭만큼 내어쓰기를 맞춘다. 맞춘 문단 수를 돌려준다. */
export function autoHangAll(editor: Editor): number {
  const { state, view } = editor;
  const tr = state.tr;
  let n = 0;
  state.doc.descendants((node, pos) => {
    if (node.type.name === "table") return false;   // 표 칸 글은 칸 정렬을 따르므로 내어쓰기하지 않는다
    if (node.type.name !== "paragraph") return true;
    if (node.attrs.textAlign === "center" || node.attrs.textAlign === "right") return false;   // 가운데·오른쪽 줄은 제외
    const em = autoHang(editor, node, pos);
    if (em !== null && em !== node.attrs.hang) {
      tr.setNodeMarkup(pos, undefined, { ...node.attrs, hang: em, role: node.attrs.role === "hang" ? null : node.attrs.role });
      n++;
    }
    return false;
  });
  if (n) view.dispatch(tr);
  return n;
}

/* ---------- 7. 쪽 아래 고정(꼬리말) ---------- */

const PINNABLE = ["paragraph", "heading", "box"];

/** 커서·선택이 걸친 최상위 블록들. */
function topBlocks(state: EditorState): { node: PMNode; pos: number }[] {
  const { $from, $to } = state.selection;
  const out: { node: PMNode; pos: number }[] = [];
  const last = Math.max($from.index(0), $to.index(0) - ($to.depth === 0 && $to.pos > $from.pos ? 1 : 0));
  for (let i = $from.index(0); i <= last && i < state.doc.childCount; i++) {
    out.push({ node: state.doc.child(i), pos: $from.posAtIndex(i, 0) });
  }
  return out;
}

export function isPinned(state: EditorState): boolean {
  const b = topBlocks(state)[0];
  return !!b?.node.attrs.pin;
}

export function canPin(state: EditorState): boolean {
  return topBlocks(state).some((b) => PINNABLE.includes(b.node.type.name));
}

/** 선택한 최상위 블록을 쪽 아래에 붙이거나 뗀다. 이어진 고정 블록들은 한 덩어리로 쪽 맨 아래에 놓인다. */
export function togglePin(editor: Editor): boolean {
  const { state, view } = editor;
  const blocks = topBlocks(state).filter((b) => PINNABLE.includes(b.node.type.name));
  if (!blocks.length) return false;
  const on = !blocks[0].node.attrs.pin;
  const tr = state.tr;
  for (const { node, pos } of blocks) tr.setNodeMarkup(pos, undefined, { ...node.attrs, pin: on ? "bottom" : null });
  view.dispatch(tr);
  view.focus();
  return true;
}

/** 고정 블록 덩어리 바로 앞에 빈 칸(위젯)을 두고, layoutPages 가 그 높이로 덩어리를 쪽 아래까지 민다. */
const pinKey = new PluginKey("pinSpacer");
const pinSpacerPlugin = new Plugin({
  key: pinKey,
  props: {
    decorations(state) {
      const decos: Decoration[] = [];
      let prevPinned = false;
      state.doc.forEach((node, offset, i) => {
        const pinned = !!node.attrs.pin;
        if (pinned && !prevPinned) {
          decos.push(Decoration.widget(offset, () => {
            const el = document.createElement("div");
            el.className = "pin-spacer";
            el.contentEditable = "false";
            return el;
          }, { side: -1, key: `pin-${i}`, ignoreSelection: true }));
        }
        prevPinned = pinned;
      });
      return DecorationSet.create(state.doc, decos);
    },
  },
});

/**
 * 통째로 넘기기: 조(제목 + 그 아래 내용)·표·서문(문서 제목 + 첫 조 앞 글)·모듈 블록이 쪽 경계에 걸리면
 * 덩어리 앞의 빈 칸(위젯)을 키워 덩어리 전체를 다음 쪽 맨 위로 민다. 높이는 layoutPages 가 정한다.
 * 표·단 나눔·나란히·그림 안쪽은 들여다보지 않는다(그 안에서는 밀지 않음).
 */
const KEEP_INSIDE_SKIP = new Set(["table", "columns", "split", "figure", "gallery", "region"]);
const keepKey = new PluginKey("keepSpacer");
const keepSpacerPlugin = new Plugin({
  key: keepKey,
  props: {
    decorations(state) {
      const decos: Decoration[] = [];
      state.doc.descendants((node, pos) => {
        const t = node.type.name;
        // 쪽 아래 고정 박스(꼬리말)에는 두지 않는다: 고정 덩어리 배치(pin-spacer)가 바로 뒤 형제들을 잰다
        if ((t === "heading" && node.attrs.level <= 3) || t === "table" || t === "moduleBlock" || (t === "box" && !node.attrs.pin)) {
          // 박스 앞 빈 칸은 박스 첫머리의 덩어리를 넘길 때 박스째 넘기는 데 쓴다
          decos.push(Decoration.widget(pos, () => {
            const el = document.createElement("div");
            el.className = t === "box" ? "keep-spacer box-spacer" : "keep-spacer";
            el.contentEditable = "false";
            return el;
          }, { side: -1, key: `keep-${pos}`, ignoreSelection: true }));
        }
        return !KEEP_INSIDE_SKIP.has(t) && t !== "heading" && t !== "paragraph";
      });
      return DecorationSet.create(state.doc, decos);
    },
  },
});

/** 덩어리를 시작하는 요소(조 제목·장 제목·문서 제목·모듈 블록). 표는 덩어리 안에 포함되고, 혼자서도 덩어리가 된다. */
const isGroupStart = (el: Element | null) => !!el && (/^H[1-3]$/.test(el.tagName) || el.classList.contains("modblock"));

/** 빈 칸 뒤의 덩어리(같은 부모 안의 형제 요소들). */
function keepUnit(spacer: HTMLElement): HTMLElement[] {
  const first = spacer.nextElementSibling as HTMLElement | null;
  if (!first) return [];
  const unit = [first];
  if (!isGroupStart(first)) return unit;           // 표 하나
  if (first.classList.contains("modblock")) return unit;
  for (let el = first.nextElementSibling as HTMLElement | null; el; el = el.nextElementSibling as HTMLElement | null) {
    if (el.classList.contains("pagebreak") || el.classList.contains("pin-spacer") || el.classList.contains("pin-bottom")) break;
    if (el.classList.contains("keep-spacer")) {
      if (isGroupStart(el.nextElementSibling)) break;   // 다음 조
      continue;                                          // 조 안의 표: 함께 넘긴다
    }
    unit.push(el);
  }
  return unit;
}

/** 문단 내어쓰기(hang, em)와 쪽 아래 고정(pin) 속성. */
export const LayoutAttrs = Extension.create({
  name: "layoutAttrs",
  addGlobalAttributes() {
    return [
      { types: ["paragraph"], attributes: { hang: {
        default: null,
        parseHTML: (el: HTMLElement) => parseFloat(el.style.getPropertyValue("--hang")) || null,
        renderHTML: (a: Record<string, unknown>) => (a.hang ? { class: "hang", style: `--hang:${a.hang}em` } : {}),
      } } },
      { types: PINNABLE, attributes: { pin: {
        default: null,
        parseHTML: (el: HTMLElement) => el.getAttribute("data-pin"),
        renderHTML: (a: Record<string, unknown>) => (a.pin ? { "data-pin": String(a.pin), class: "pin-bottom" } : {}),
      } } },
    ];
  },
  addProseMirrorPlugins() { return [pinSpacerPlugin, keepSpacerPlugin]; },
});

export interface PageInfo { pages: number; overflow: number[]; split?: HTMLElement; join?: HTMLElement }

/** 빈 칸 앞에 (다른 빈 칸 말고) 형제 요소가 없는가 = 부모의 첫 내용인가. */
function firstInParent(el: HTMLElement): boolean {
  for (let p = el.previousElementSibling; p; p = p.previousElementSibling) {
    if (!p.classList.contains("keep-spacer") && !p.classList.contains("pin-spacer")) return false;
  }
  return true;
}

/**
 * 편집 화면의 쪽 배치: 각 쪽 나눔의 높이를 '다음 쪽 경계 + 위 여백'까지로 맞추고,
 * 한 쪽 분량을 넘친 쪽 번호를 돌려준다. 용지 너비(--pw)와 높이(--ph)의 비율로 화면 배율과 무관하게 계산한다.
 */
export function layoutPages(paper: HTMLElement | null): PageInfo {
  if (!paper) return { pages: 1, overflow: [] };
  const cs = getComputedStyle(paper);
  const pw = parseFloat(cs.getPropertyValue("--pw")) || 595.3;
  const ph = parseFloat(cs.getPropertyValue("--ph")) || 841.9;
  const pad = parseFloat(cs.getPropertyValue("--pad")) || 14.2;
  const rect = () => paper.getBoundingClientRect();
  const scale = rect().width / (pw * 4 / 3);           // 화면 px / CSS px
  const pagePx = rect().width * (ph / pw);             // 화면 px 기준 한 쪽 높이
  const padPx = rect().width * (pad / pw);
  const overflow: number[] = [];
  const TOL = 4;   // 화면 px 반올림 오차 허용
  let page = 0;
  let split: HTMLElement | undefined;
  // 쪽 나눔과 고정 블록 앞 빈 칸을 문서 순서대로 맞춘다(앞의 높이가 뒤의 위치를 바꾸므로)
  paper.querySelectorAll<HTMLElement>(".pagebreak, .pin-spacer, .keep-spacer").forEach((el) => {
    el.style.height = "0px";
    if (el.classList.contains("keep-spacer")) {
      if (el.classList.contains("box-spacer")) return;   // 박스 첫머리 덩어리가 대신 정한다
      const unit = keepUnit(el);
      if (!unit.length) return;
      // 박스 첫머리의 덩어리는 박스째 넘긴다(바깥 박스의 첫머리면 바깥 박스째)
      let target = el;
      let box: HTMLElement | null = null;
      for (let cur: HTMLElement = el; firstInParent(cur) && cur.parentElement?.classList.contains("box"); ) {
        box = cur.parentElement;
        const before = box.previousElementSibling as HTMLElement | null;
        if (!before?.classList.contains("box-spacer")) break;
        target = before;
        cur = before;
      }
      const lead = target === el ? unit[0] : (target.nextElementSibling as HTMLElement);
      const top = lead.getBoundingClientRect().top - rect().top;
      const bottom = Math.max(...unit.map((u) => u.getBoundingClientRect().bottom)) - rect().top;
      const k = Math.floor(top / pagePx);
      const limit = (k + 1) * pagePx - padPx;           // 이 쪽 글 영역의 아래 끝
      const atTop = top <= k * pagePx + padPx + TOL;      // 이미 쪽 맨 위면 밀어도 소용없다
      // 한 쪽에 다 들어가는 덩어리만 민다(더 크면 어차피 나뉘므로 그대로 둔다)
      if (bottom > limit + TOL && !atTop && bottom - top <= pagePx - 2 * padPx) {
        if (target === el && el.parentElement?.classList.contains("box")) {
          // 박스 중간의 덩어리: 박스 테두리가 쪽을 건너가지 않도록 그 자리에서 박스를 자르게 한다(다음 배치에서 박스째 넘김)
          split = split ?? unit[0];
          return;
        }
        target.style.height = `${((k + 1) * pagePx + padPx - top) / scale}px`;
      }
      return;
    }
    if (el.classList.contains("pin-spacer")) {
      let last = el.nextElementSibling as HTMLElement | null;
      if (!last) return;
      while (last.nextElementSibling?.classList.contains("pin-bottom")) last = last.nextElementSibling as HTMLElement;
      const top = el.getBoundingClientRect().top - rect().top;
      const bottom = last.getBoundingClientRect().bottom - rect().top;
      const pageIdx = Math.max(page, Math.floor(top / pagePx));
      if (pageIdx > page) overflow.push(page + 1);
      // 용지 끝까지 띠(bleed)는 용지 맨 아래에, 나머지는 아래 여백 위에 붙인다
      const target = (pageIdx + 1) * pagePx - (last.classList.contains("bleed") ? 0 : padPx);
      const gap = target - bottom;
      if (gap < -TOL) overflow.push(pageIdx + 1);
      el.style.height = `${Math.max(0, gap) / scale}px`;
      page = pageIdx;
      return;
    }
    const y = el.getBoundingClientRect().top - rect().top;
    if (el.classList.contains("soft")) {
      // 원본 쪽 경계: 앞 내용이 이미 다음 쪽으로 흘러 넘쳤으면 쪽을 또 넘기지 않고 이어 흐른다
      const spilled = y > (page + 1) * pagePx + TOL;
      el.classList.toggle("soft-off", spilled);
      if (spilled) { overflow.push(page + 1); page = Math.floor(y / pagePx); return; }   // 원본 쪽이 한 쪽에 안 들어감
    }
    if (y > (page + 1) * pagePx + TOL) overflow.push(page + 1);   // 이 쪽 내용이 한 쪽을 넘침
    const pagesUsed = Math.max(page + 1, Math.ceil((y - TOL) / pagePx));
    const next = pagesUsed * pagePx + padPx;
    el.style.height = `${Math.max(0, next - y) / scale}px`;
    page = pagesUsed;
  });
  // 마지막 쪽 나눔 뒤의 내용은 자연스럽게 다음 쪽으로 흐르므로 '넘침'이 아니라 쪽 수로만 센다
  // 용지 높이(아래 여백 포함)로 센다. 용지 끝까지 띠는 아래 여백을 덮으므로 내용 끝 + 여백으로 세면 한 쪽이 더 나온다
  const end = rect().height;
  const pages = Math.max(page + 1, Math.ceil((end - TOL) / pagePx));
  // 원본 쪽 경계가 있는 문서(AI 변환)는 마지막 원본 쪽도 한 쪽에 들어가야 한다
  if (paper.querySelector(".pagebreak.soft") && pages > page + 1) overflow.push(page + 1);
  // 쪽 배치가 잘랐던 박스 중 이제 넘기지 않는 것(앞 빈 칸 0, 바로 앞이 박스)은 앞 박스와 다시 합친다
  const join = split ? undefined : [...paper.querySelectorAll<HTMLElement>(".box.cont")].find((b) => {
    const sp = b.previousElementSibling as HTMLElement | null;
    if (!sp?.classList.contains("box-spacer") || parseFloat(sp.style.height) > 0) return false;
    return !!sp.previousElementSibling?.classList.contains("box");
  });
  return { pages, overflow: [...new Set(overflow)].sort((a, b) => a - b), split, join };
}

/* ---------- 키 동작 ---------- */

export const BlockKeys = Extension.create({
  name: "blockKeys",
  priority: 1000,   // 기본 키맵(박스 쪼개기 등)보다 먼저
  addKeyboardShortcuts() {
    return {
      Enter: () => enterInContainer(this.editor),
      ArrowDown: () => escapeClosedBlock(this.editor, true),
      ArrowUp: () => escapeClosedBlock(this.editor, false),
    };
  },
});

/** 쪽 나눔이 선택된 상태인지(툴바 표시용). */
export function pageBreakSelected(state: EditorState): boolean {
  const s = state.selection;
  return s instanceof NodeSelection && (s.node as PMNode).type.name === "pageBreak";
}

/* ---------- 글자 서식 지우기 ---------- */

/**
 * 선택한 글자(칸 선택이면 고른 칸들의 글자)에서 textStyle 의 한 속성(color·fontSize)만 지운다.
 * 다른 속성이 남아 있으면 그 속성은 유지하고, 모두 비면 표식 자체를 없앤다. 글자 노드만 건드린다.
 */
export function clearTextStyleAttr(editor: Editor, attr: "color" | "fontSize"): boolean {
  const { state, view } = editor;
  const type = state.schema.marks.textStyle;
  if (!type) return false;
  const tr = state.tr;
  for (const range of state.selection.ranges) {
    const from = range.$from.pos, to = range.$to.pos;
    state.doc.nodesBetween(from, to, (node, pos) => {
      if (!node.isText) return true;
      const mark = node.marks.find((m) => m.type === type);
      if (!mark || mark.attrs[attr] == null) return false;
      const start = Math.max(pos, from), end = Math.min(pos + node.nodeSize, to);
      const attrs = { ...mark.attrs, [attr]: null };
      tr.removeMark(start, end, type);
      if (Object.values(attrs).some((v) => v != null)) tr.addMark(start, end, type.create(attrs));
      return false;
    });
  }
  if (tr.docChanged) view.dispatch(tr);
  view.focus();
  return true;
}

/* ---------- 이미지 칸에 맞추기 ---------- */

/**
 * 선택한 이미지를 칸(표 안) 또는 글 폭(표 밖)에 맞춘다. 비율은 유지한다.
 * 표 안이고 그 행에 높이가 정해져 있으면 높이도 넘지 않게 맞춘다.
 */
/** 선택한 이미지의 속성을 바꾸고 선택을 유지한다(도구 줄의 칸·목록을 쓴 뒤에도 이미지 도구가 남도록). */
export function setImageAttrs(editor: Editor, attrs: Record<string, unknown>): boolean {
  const { state, view } = editor;
  const sel = state.selection;
  if (!(sel instanceof NodeSelection) || sel.node.type.name !== "image") return false;
  const tr = state.tr.setNodeMarkup(sel.from, undefined, { ...sel.node.attrs, ...attrs });
  tr.setSelection(NodeSelection.create(tr.doc, sel.from));
  view.dispatch(tr);
  view.focus();
  return true;
}

export function fitImage(editor: Editor): boolean {
  const { state, view } = editor;
  const sel = state.selection;
  if (!(sel instanceof NodeSelection) || sel.node.type.name !== "image") return false;
  const wrap = view.nodeDOM(sel.from) as HTMLElement | null;
  const img = wrap?.querySelector("img") ?? (wrap instanceof HTMLImageElement ? wrap : null);
  if (!wrap || !img) return false;
  const host = (wrap.closest("td,th") ?? wrap.parentElement) as HTMLElement | null;
  if (!host) return false;
  const cs = getComputedStyle(host);
  const pxToPt = 0.75;
  // 칸 안쪽 너비(CSS px)
  const innerW = host.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
  let maxWpt = innerW * pxToPt;
  const ratio = img.naturalWidth && img.naturalHeight ? img.naturalWidth / img.naturalHeight
    : (img.getBoundingClientRect().width / (img.getBoundingClientRect().height || 1));
  const row = host.closest("tr") as HTMLElement | null;
  const rowH = row?.style.height.endsWith("pt") ? parseFloat(row.style.height) : NaN;
  if (host.matches("td,th") && rowH > 0) {
    const padV = (parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom)) * pxToPt;
    maxWpt = Math.min(maxWpt, (rowH - padV - 1) * ratio);   // 행 높이 안에 들도록
  }
  const widthPt = Math.max(4, Math.floor(maxWpt * 10) / 10);
  const tr = state.tr.setNodeMarkup(sel.from, undefined, { ...sel.node.attrs, widthPt });
  tr.setSelection(NodeSelection.create(tr.doc, sel.from));
  view.dispatch(tr);
  view.focus();
  return true;
}
