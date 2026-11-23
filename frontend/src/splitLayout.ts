/**
 * 나란히 놓기(split): 내용 덩어리를 좌우로 나란히 놓는 틀. 단(splitCol)마다 너비 비율(w)을 따로 가진다.
 *
 * 2단(columns)은 글이 단을 따라 흘러 넘어가는 신문식 단이고, 나란히 놓기는 단마다 내용이 따로인 배치다.
 * 예) 상품설명서 왼쪽 본표 | 오른쪽 '비아젬 상품 정보' 패널, 표 | 설명 글, 카드 여러 장.
 * 너비는 w 끼리의 비율로 나눈다(flex-grow). 단 사이 간격은 split 의 gap(pt).
 */
import { Node } from "@tiptap/core";
import type { Editor } from "@tiptap/core";
import type { Node as PMNode } from "@tiptap/pm/model";
import { Fragment } from "@tiptap/pm/model";
import type { EditorState } from "@tiptap/pm/state";
import { TextSelection } from "@tiptap/pm/state";

export const Split = Node.create({
  name: "split",
  group: "block",
  content: "splitCol{2,}",
  defining: true,
  addAttributes() {
    return { gap: { default: 6, rendered: false } };
  },
  parseHTML() { return [{ tag: "div[data-split]" }]; },
  renderHTML({ node }) {
    return ["div", { "data-split": "", class: "split", style: `gap:${node.attrs.gap ?? 6}pt` }, 0];
  },
});

export const SplitCol = Node.create({
  name: "splitCol",
  content: "block+",
  isolating: true,
  defining: true,
  addAttributes() {
    return { w: { default: 50, rendered: false } };
  },
  parseHTML() { return [{ tag: "div[data-splitcol]" }]; },
  renderHTML({ node }) {
    return ["div", { "data-splitcol": "", class: "splitcol", style: `flex:${Math.max(1, Number(node.attrs.w) || 50)} 1 0` }, 0];
  },
});

/** 커서를 감싼 단과 나란히 틀. */
export function findSplit(state: EditorState) {
  const $f = state.selection.$from;
  for (let d = $f.depth; d > 1; d--) {
    if ($f.node(d).type.name === "splitCol" && $f.node(d - 1).type.name === "split") {
      return { split: $f.node(d - 1), splitPos: $f.before(d - 1), col: $f.node(d), colPos: $f.before(d), index: $f.index(d - 1) };
    }
  }
  return null;
}

/** 지금 단이 전체 너비에서 차지하는 비율(%). */
export function splitColShare(state: EditorState): number | null {
  const f = findSplit(state);
  if (!f) return null;
  let sum = 0;
  f.split.forEach((c) => { sum += Number(c.attrs.w) || 0; });
  return sum ? Math.round(((Number(f.col.attrs.w) || 0) / sum) * 1000) / 10 : null;
}

/** 단 너비 비율을 정한다. 나머지 단들은 원래 비율대로 남은 너비를 나눈다. */
export function setSplitColShare(editor: Editor, pct: number): boolean {
  const f = findSplit(editor.state);
  if (!f || !(pct > 2 && pct < 98)) return false;
  let others = 0;
  f.split.forEach((c, _o, i) => { if (i !== f.index) others += Number(c.attrs.w) || 0; });
  if (!others) return false;
  const w = Math.round(((pct * others) / (100 - pct)) * 100) / 100;
  editor.view.dispatch(editor.state.tr.setNodeMarkup(f.colPos, undefined, { ...f.col.attrs, w }));
  return true;
}

/** 모든 단 비율을 한꺼번에 정한다(예: 70, 30). */
export function setSplitRatios(editor: Editor, nums: number[]): boolean {
  const f = findSplit(editor.state);
  if (!f || nums.length !== f.split.childCount || nums.some((n) => !(n > 0))) return false;
  const tr = editor.state.tr;
  let pos = f.splitPos + 1;
  f.split.forEach((c, _o, i) => {
    tr.setNodeMarkup(pos, undefined, { ...c.attrs, w: nums[i] });
    pos += c.nodeSize;
  });
  editor.view.dispatch(tr);
  return true;
}

export function splitRatios(state: EditorState): number[] | null {
  const f = findSplit(state);
  if (!f) return null;
  let sum = 0;
  f.split.forEach((c) => { sum += Number(c.attrs.w) || 0; });
  const out: number[] = [];
  f.split.forEach((c) => out.push(Math.round(((Number(c.attrs.w) || 0) / (sum || 1)) * 1000) / 10));
  return out;
}

/** 선택한 블록들을 왼쪽 단에 두고 오른쪽에 빈 단을 하나 만든다. 이미 나란히 안이면 단을 하나 더한다. */
export function insertSplit(editor: Editor): boolean {
  const { state, view } = editor;
  const { schema } = state;
  const f = findSplit(state);
  if (f) return addSplitCol(editor);
  const range = state.selection.$from.blockRange(state.selection.$to);
  if (!range) return false;
  const blocks: PMNode[] = [];
  for (let i = range.startIndex; i < range.endIndex; i++) blocks.push(range.parent.child(i));
  const left = schema.nodes.splitCol.create({ w: 50 }, Fragment.from(blocks));
  const right = schema.nodes.splitCol.create({ w: 50 }, schema.nodes.paragraph.create());
  const split = schema.nodes.split.create({ gap: 6 }, [left, right]);
  try { split.check(); } catch { return false; }   // 단에 넣을 수 없는 블록이면 그만둔다
  const tr = state.tr.replaceWith(range.start, range.end, split);
  // 커서는 오른쪽 빈 단으로
  const rightStart = range.start + 1 + left.nodeSize + 1;
  tr.setSelection(TextSelection.near(tr.doc.resolve(rightStart + 1)));
  view.dispatch(tr.scrollIntoView());
  view.focus();
  return true;
}

/** 지금 단 오른쪽에 빈 단을 더한다(너비는 지금 단과 같게). */
export function addSplitCol(editor: Editor): boolean {
  const f = findSplit(editor.state);
  if (!f) return false;
  const { schema } = editor.state;
  const col = schema.nodes.splitCol.create({ w: f.col.attrs.w }, schema.nodes.paragraph.create());
  const at = f.colPos + f.col.nodeSize;
  const tr = editor.state.tr.insert(at, col);
  tr.setSelection(TextSelection.near(tr.doc.resolve(at + 2)));
  editor.view.dispatch(tr);
  editor.view.focus();
  return true;
}

/** 지금 단을 지운다. 단이 하나만 남으면 나란히 틀을 풀어 그 내용만 남긴다. */
export function removeSplitCol(editor: Editor): boolean {
  const f = findSplit(editor.state);
  if (!f) return false;
  if (f.split.childCount <= 2) {
    // 남는 단 하나의 내용만 남긴다
    const keep = f.split.child(f.index === 0 ? 1 : 0);
    const tr = editor.state.tr.replaceWith(f.splitPos, f.splitPos + f.split.nodeSize, keep.content);
    tr.setSelection(TextSelection.near(tr.doc.resolve(Math.min(f.splitPos + 1, tr.doc.content.size))));
    editor.view.dispatch(tr);
  } else {
    const tr = editor.state.tr.delete(f.colPos, f.colPos + f.col.nodeSize);
    tr.setSelection(TextSelection.near(tr.doc.resolve(Math.min(f.colPos + 1, tr.doc.content.size))));
    editor.view.dispatch(tr);
  }
  editor.view.focus();
  return true;
}

/** 나란히 틀을 풀어 단들의 내용을 위에서 아래로 차례로 놓는다(왼쪽 단 → 오른쪽 단). */
export function unwrapSplit(editor: Editor): boolean {
  const f = findSplit(editor.state);
  if (!f) return false;
  const parts: PMNode[] = [];
  f.split.forEach((c) => c.forEach((b) => parts.push(b)));
  const tr = editor.state.tr.replaceWith(f.splitPos, f.splitPos + f.split.nodeSize, parts);
  tr.setSelection(TextSelection.near(tr.doc.resolve(Math.min(f.splitPos + 1, tr.doc.content.size))));
  editor.view.dispatch(tr);
  editor.view.focus();
  return true;
}

export function setSplitGap(editor: Editor, gap: number | null): boolean {
  const f = findSplit(editor.state);
  if (!f) return false;
  editor.view.dispatch(editor.state.tr.setNodeMarkup(f.splitPos, undefined, { ...f.split.attrs, gap: gap ?? 6 }));
  return true;
}
