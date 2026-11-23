/**
 * 표 서식 도구: 열 너비(%·균등·끌기 보정), 여러 행 높이, 머리행 서식, 서식 복사·붙여넣기.
 * 열 너비는 칸의 colwidth(px) 로 저장된다. 표는 늘 용지 너비(100%)로 그려지므로 실제로는 '비율'로 작동한다.
 */
import { Extension } from "@tiptap/core";
import type { Editor } from "@tiptap/core";
import { multiCellPlugin } from "./multiCell";
import type { Node as PMNode } from "@tiptap/pm/model";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import type { EditorState, Transaction } from "@tiptap/pm/state";
import type { EditorView } from "@tiptap/pm/view";
import { CellSelection, TableMap, columnResizingPluginKey } from "@tiptap/pm/tables";

export const HEAD_BG = "#dadbdc";

interface TableCtx { node: PMNode; pos: number; start: number; map: TableMap }

function tableAt(state: EditorState): TableCtx | null {
  const $f = state.selection.$from;
  for (let d = $f.depth; d > 0; d--) {
    const node = $f.node(d);
    if (node.type.name === "table") {
      const pos = $f.before(d);
      return { node, pos, start: pos + 1, map: TableMap.get(node) };
    }
  }
  return null;
}

function tableAtPos(doc: PMNode, pos: number): TableCtx | null {
  const node = doc.nodeAt(pos);
  return node?.type.name === "table" ? { node, pos, start: pos + 1, map: TableMap.get(node) } : null;
}

/** 선택이 걸친 열·행 번호(칸 선택이면 그 범위, 아니면 커서 칸). */
function selectedRect(state: EditorState, t: TableCtx) {
  const sel = state.selection;
  if (sel instanceof CellSelection) {
    const a = t.map.findCell(sel.$anchorCell.pos - t.start);
    const b = t.map.findCell(sel.$headCell.pos - t.start);
    return { left: Math.min(a.left, b.left), right: Math.max(a.right, b.right), top: Math.min(a.top, b.top), bottom: Math.max(a.bottom, b.bottom) };
  }
  const $f = sel.$from;
  for (let d = $f.depth; d > 0; d--) {
    const name = $f.node(d).type.name;
    if (name === "tableCell" || name === "tableHeader") return t.map.findCell($f.before(d) - t.start);
  }
  return { left: 0, right: 1, top: 0, bottom: 1 };
}

/* ---------- 열 너비 ---------- */

/** 칸 속성에 적힌 열 너비(없으면 null). */
function attrWidths(t: TableCtx): (number | null)[] {
  const out: (number | null)[] = Array(t.map.width).fill(null);
  for (let row = 0; row < t.map.height; row++) {
    for (let col = 0; col < t.map.width; col++) {
      const cellPos = t.map.map[row * t.map.width + col];
      const cell = t.node.nodeAt(cellPos)!;
      const rect = t.map.findCell(cellPos);
      const cw = cell.attrs.colwidth as number[] | null;
      if (cw && rect.left === col && cw[0] && out[col] == null) cw.forEach((w, i) => { if (w && out[col + i] == null) out[col + i] = w; });
    }
  }
  return out;
}

/** 화면에 실제로 그려진 열 너비(CSS px). 배율(zoom)을 걷어낸 값. */
function measuredWidths(view: EditorView, t: TableCtx): number[] {
  const dom = view.nodeDOM(t.pos) as HTMLElement | null;
  const table = dom?.querySelector("table") ?? (dom?.tagName === "TABLE" ? dom : null);
  const cols = table ? [...table.querySelectorAll(":scope > colgroup > col")] as HTMLElement[] : [];
  if (!table || cols.length !== t.map.width) {
    const w = attrWidths(t);
    const known = w.filter((x): x is number => x != null);
    const avg = known.length ? known.reduce((a, b) => a + b, 0) / known.length : 100;
    return w.map((x) => x ?? avg);
  }
  const scale = table.getBoundingClientRect().width / (table.offsetWidth || 1) || 1;
  return cols.map((c) => Math.max(8, c.getBoundingClientRect().width / scale));
}

/** 모든 칸의 colwidth 를 widths 로 맞춘다(병합 칸은 걸친 열들의 값). */
function writeWidths(tr: Transaction, t: TableCtx, widths: number[]) {
  const seen = new Set<number>();
  for (const cellPos of t.map.map) {
    if (seen.has(cellPos)) continue;
    seen.add(cellPos);
    const cell = t.node.nodeAt(cellPos)!;
    const rect = t.map.findCell(cellPos);
    const cw = widths.slice(rect.left, rect.right).map((w) => Math.round(w));
    tr.setNodeMarkup(tr.mapping.map(t.start + cellPos), undefined, { ...cell.attrs, colwidth: cw });
  }
  return tr;
}

export function columnPct(editor: Editor): number | null {
  const t = tableAt(editor.state);
  if (!t) return null;
  const w = measuredWidths(editor.view, t);
  const sum = w.reduce((a, b) => a + b, 0);
  const r = selectedRect(editor.state, t);
  const own = w.slice(r.left, r.right).reduce((a, b) => a + b, 0);
  return Math.round((own / sum) * 1000) / 10;
}

/** 선택한 열(여러 열이면 합계)을 표 너비의 pct% 로. 나머지 열은 원래 비율대로 남은 너비를 나눠 갖는다. */
export function setColumnPct(editor: Editor, pct: number): boolean {
  const t = tableAt(editor.state);
  if (!t || !(pct > 0 && pct < 100)) return false;
  const w = measuredWidths(editor.view, t);
  const sum = w.reduce((a, b) => a + b, 0);
  const r = selectedRect(editor.state, t);
  const sel = w.slice(r.left, r.right).reduce((a, b) => a + b, 0);
  const rest = sum - sel;
  if (rest <= 0) return false;
  const newSel = (sum * pct) / 100;
  const k = (sum - newSel) / rest;
  const out = w.map((x, i) => (i >= r.left && i < r.right ? (x / sel) * newSel : x * k));
  editor.view.dispatch(writeWidths(editor.state.tr, t, out));
  return true;
}

/** 열마다 표 너비에서 차지하는 비율(%), 소수 첫째 자리. */
export function columnRatios(editor: Editor): number[] | null {
  const t = tableAt(editor.state);
  if (!t) return null;
  const w = measuredWidths(editor.view, t);
  const sum = w.reduce((a, b) => a + b, 0);
  return w.map((x) => Math.round((x / sum) * 1000) / 10);
}

/** 모든 열의 비율을 한 번에 정한다. 숫자 개수가 열 수와 같아야 하며, 합은 100이 아니어도 비례로 맞춘다. */
export function setColumnRatios(editor: Editor, nums: number[]): boolean {
  const t = tableAt(editor.state);
  if (!t || nums.length !== t.map.width || nums.some((n) => !(n > 0))) return false;
  const total = measuredWidths(editor.view, t).reduce((a, b) => a + b, 0);
  const sum = nums.reduce((a, b) => a + b, 0);
  editor.view.dispatch(writeWidths(editor.state.tr, t, nums.map((n) => (n / sum) * total)));
  return true;
}

export function equalizeColumns(editor: Editor): boolean {
  const t = tableAt(editor.state);
  if (!t) return false;
  const w = measuredWidths(editor.view, t);
  const avg = w.reduce((a, b) => a + b, 0) / w.length;
  editor.view.dispatch(writeWidths(editor.state.tr, t, w.map(() => avg)));
  return true;
}

/**
 * 끌어서 열 너비 바꾸기 보정: 끌기 전에 모든 열 너비를 실제 값으로 고정해 두고,
 * 끌기가 끝나면 바뀐 만큼을 옆 열에서 빼서 표 전체 너비(=다른 열들)가 그대로 있게 한다.
 */
const keeperKey = new PluginKey("tableWidthKeeper");
function keeperPlugin() {
  return new Plugin({
    key: keeperKey,
    props: {
      handleDOMEvents: {
        mousedown(view, event) {
          const rs = columnResizingPluginKey.getState(view.state) as { activeHandle: number } | undefined;
          if (!rs || rs.activeHandle < 0 || event.button !== 0) return false;
          const $cell = view.state.doc.resolve(rs.activeHandle);
          let tablePos = -1;
          for (let d = $cell.depth; d >= 0; d--) if ($cell.node(d).type.name === "table") { tablePos = d === 0 ? 0 : $cell.before(d); break; }
          if (tablePos < 0) return false;
          const t = tableAtPos(view.state.doc, tablePos);
          if (!t) return false;
          const before = measuredWidths(view, t);
          view.dispatch(writeWidths(view.state.tr, t, before).setMeta("addToHistory", false));
          const up = () => {
            window.removeEventListener("mouseup", up);
            setTimeout(() => {   // 열 끌기 플러그인이 새 너비를 적용한 뒤에
              const t2 = tableAtPos(view.state.doc, tablePos);
              if (!t2 || t2.map.width !== before.length) return;
              const after = attrWidths(t2).map((x, i) => x ?? before[i]);
              const changed = after.map((x, i) => Math.abs(x - before[i]) > 0.5 ? i : -1).filter((i) => i >= 0);
              if (changed.length !== 1) return;
              const i = changed[0];
              const n = i + 1 < after.length ? i + 1 : i - 1;
              if (n < 0) return;
              // 끌기 플러그인은 화면 px 이동량을 그대로 너비에 더한다. 확대(zoom) 중이면 그만큼 나눠 마우스 위치에 맞춘다
              const dom = view.nodeDOM(tablePos) as HTMLElement | null;
              const tbl = dom?.querySelector("table");
              const scale = tbl ? tbl.getBoundingClientRect().width / (tbl.offsetWidth || 1) || 1 : 1;
              const delta = (after[i] - before[i]) / scale;
              const fixed = [...before];
              const room = before[n] - 12;              // 옆 열은 최소 12px 은 남긴다
              const d = Math.max(-before[i] + 12, Math.min(delta, room));
              fixed[i] = before[i] + d;
              fixed[n] = before[n] - d;
              view.dispatch(writeWidths(view.state.tr, t2, fixed));
            }, 0);
          };
          window.addEventListener("mouseup", up);
          return false;
        },
      },
    },
  });
}

/* ---------- 행 높이 ---------- */

/** 행 높이(pt). all 이면 표 전체, 아니면 선택한 행들(칸 선택) 또는 커서 행. null 은 자동. */
export function setRowHeight(editor: Editor, h: number | null, all = false): boolean {
  const { state, view } = editor;
  const t = tableAt(state);
  if (!t) return false;
  const r = all ? { top: 0, bottom: t.map.height } : selectedRect(state, t);
  const tr = state.tr;
  let pos = t.start;
  t.node.forEach((row, offset, i) => {
    pos = t.start + offset;
    if (i >= r.top && i < r.bottom) tr.setNodeMarkup(pos, undefined, { ...row.attrs, h: h && h > 0 ? h : null });
  });
  view.dispatch(tr);
  return true;
}

export function rowHeightOf(state: EditorState): number | null {
  const t = tableAt(state);
  if (!t) return null;
  const r = selectedRect(state, t);
  return (t.node.child(r.top).attrs.h as number | null) ?? null;
}

/* ---------- 머리행 서식·서식 복사 ---------- */

export interface TableFormat {
  lineColor: string; lineW: number; widthPct: number | null;
  headBg: string | null; headAlign: string[]; bodyBg: string | null; bodyAlign: string[];
  headH: number | null; bodyH: number | null; colPct: number[] | null;
}

/** 표 칸마다 (행, 열, 칸 노드, 위치)를 돌며 새 속성을 정한다. */
function eachCell(t: TableCtx, fn: (row: number, col: number, cell: PMNode) => Record<string, unknown> | null, tr: Transaction) {
  const seen = new Set<number>();
  t.map.map.forEach((cellPos) => {
    if (seen.has(cellPos)) return;
    seen.add(cellPos);
    const cell = t.node.nodeAt(cellPos)!;
    const rect = t.map.findCell(cellPos);
    const attrs = fn(rect.top, rect.left, cell);
    if (attrs) tr.setNodeMarkup(tr.mapping.map(t.start + cellPos), undefined, { ...cell.attrs, ...attrs });
  });
}

/** 약관 표 기본 서식: 머리행 회색, 모든 칸 가운데 정렬. */
export function applyHeaderStyle(editor: Editor, tablePos?: number): boolean {
  const { state, view } = editor;
  const t = tablePos != null ? tableAtPos(state.doc, tablePos) : tableAt(state);
  if (!t) return false;
  const tr = state.tr;
  eachCell(t, (row) => ({ align: "center", background: row === 0 ? HEAD_BG : null }), tr);
  view.dispatch(tr);
  return true;
}

export function copyTableFormat(editor: Editor): TableFormat | null {
  const t = tableAt(editor.state);
  if (!t) return null;
  const cellAt = (row: number, col: number) => t.node.nodeAt(t.map.map[row * t.map.width + col])!;
  const headAlign = Array.from({ length: t.map.width }, (_, c) => cellAt(0, c).attrs.align as string);
  const bodyRow = t.map.height > 1 ? 1 : 0;
  const bodyAlign = Array.from({ length: t.map.width }, (_, c) => cellAt(bodyRow, c).attrs.align as string);
  const w = measuredWidths(editor.view, t);
  const sum = w.reduce((a, b) => a + b, 0);
  const a = t.node.attrs;
  return {
    lineColor: a.lineColor, lineW: a.lineW, widthPct: a.widthPct ?? null,
    headBg: (cellAt(0, 0).attrs.background as string | null) ?? null, headAlign,
    bodyBg: t.map.height > 1 ? ((cellAt(1, 0).attrs.background as string | null) ?? null) : null, bodyAlign,
    headH: (t.node.child(0).attrs.h as number | null) ?? null,
    bodyH: t.map.height > 1 ? ((t.node.child(1).attrs.h as number | null) ?? null) : null,
    colPct: w.map((x) => x / sum),
  };
}

/** 복사한 서식을 지금 표에 입힌다. 열 수가 같으면 열 비율도 맞춘다. */
export function pasteTableFormat(editor: Editor, f: TableFormat): boolean {
  const { state, view } = editor;
  const t = tableAt(state);
  if (!t) return false;
  const tr = state.tr;
  tr.setNodeMarkup(t.pos, undefined, { ...t.node.attrs, lineColor: f.lineColor, lineW: f.lineW, widthPct: f.widthPct });
  const pick = (arr: string[], col: number) => arr[Math.min(col, arr.length - 1)] ?? "left";
  eachCell(t, (row, col) => (row === 0
    ? { align: pick(f.headAlign, col), background: f.headBg }
    : { align: pick(f.bodyAlign, col), background: f.bodyBg }), tr);
  t.node.forEach((row, offset, i) => {
    tr.setNodeMarkup(tr.mapping.map(t.start + offset), undefined, { ...row.attrs, h: i === 0 ? f.headH : f.bodyH });
  });
  if (f.colPct && f.colPct.length === t.map.width) {
    const sum = measuredWidths(view, t).reduce((a, b) => a + b, 0);
    const t2 = tableAtPos(tr.doc, t.pos)!;
    writeWidths(tr, t2, f.colPct.map((p) => p * sum));
  }
  view.dispatch(tr);
  return true;
}

/** 표를 넣고(선택) 머리행 서식까지 입힌다. */
export function insertTable(editor: Editor, rows: number, cols: number, headerStyle: boolean): boolean {
  if (!editor.chain().focus().insertTable({ rows, cols, withHeaderRow: false }).run()) return false;
  if (headerStyle) {
    const t = tableAt(editor.state);
    if (t) applyHeaderStyle(editor, t.pos);
  }
  return true;
}

export const TableTools = Extension.create({
  name: "tableTools",
  priority: 1000,   // 열 끌기 플러그인보다 먼저 mousedown 을 받아야 끌기 전 너비를 고정할 수 있다
  addProseMirrorPlugins() { return [keeperPlugin(), multiCellPlugin()]; },
});
