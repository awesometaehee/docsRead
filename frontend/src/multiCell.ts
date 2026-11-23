/**
 * 표 칸 여러 개 따로 고르기(Ctrl+클릭, Mac 은 ⌘+클릭).
 *
 * prosemirror-tables 의 CellSelection 은 직사각형만 고른다. 떨어진 칸을 고르기 위해 CellSelection 을 이어받아
 * 칸 목록(cells)과 칸마다의 선택 범위(ranges)를 따로 둔다. 표 명령(배경·괘선·정렬 = setCellAttribute,
 * 칸 선택 표시)은 forEachCell 로 칸을 돌고, 글자 서식(굵게·색·크기)은 ranges 를 돌므로 그대로 동작한다.
 * 칸 합치기는 직사각형에서만 의미가 있어, anchor = head(마지막 칸)로 두어 mergeCells 가 동작하지 않게 한다.
 */
import { Plugin, PluginKey, Selection, SelectionRange, TextSelection } from "@tiptap/pm/state";
import type { Mappable } from "@tiptap/pm/transform";
import type { Node as PMNode } from "@tiptap/pm/model";
import { CellSelection, cellAround } from "@tiptap/pm/tables";

const isCell = (doc: PMNode, pos: number) => {
  if (pos < 0 || pos >= doc.content.size) return false;
  const role = doc.nodeAt(pos)?.type.spec.tableRole;
  return role === "cell" || role === "header_cell";
};

export class MultiCellSelection extends CellSelection {
  readonly cells: number[];

  /** cells: 칸 노드 바로 앞 위치들(같은 표 안). 마지막 칸이 기준(anchor·head)이다. */
  constructor(doc: PMNode, cells: number[]) {
    const $last = doc.resolve(cells[cells.length - 1]);
    super($last, $last);
    this.cells = cells;
    const ranges = cells.map((p) => {
      const node = doc.nodeAt(p)!;
      return new SelectionRange(doc.resolve(p + 1), doc.resolve(p + 1 + node.content.size));
    });
    // Selection.ranges 는 생성자에서 정해지는 값이라 직접 바꿔 넣는다
    (this as unknown as { ranges: SelectionRange[] }).ranges = ranges;
  }

  forEachCell(f: (node: PMNode, pos: number) => void) {
    const doc = this.$anchorCell.node(0);
    for (const p of this.cells) f(doc.nodeAt(p)!, p);
  }

  map(doc: PMNode, mapping: Mappable): Selection {
    const out: number[] = [];
    for (const p of this.cells) {
      const r = mapping.mapResult(p, -1);
      if (!r.deleted && isCell(doc, r.pos) && !out.includes(r.pos)) out.push(r.pos);
    }
    if (out.length > 1) return new MultiCellSelection(doc, out);
    if (out.length === 1) return new CellSelection(doc.resolve(out[0]));
    return TextSelection.near(doc.resolve(Math.min(mapping.map(this.cells[0]), doc.content.size)));
  }

  eq(other: Selection): boolean {
    return other instanceof MultiCellSelection && other.cells.length === this.cells.length && other.cells.every((p, i) => p === this.cells[i]);
  }

  toJSON() { return { type: "multicell", anchor: this.$anchorCell.pos, head: this.$headCell.pos, cells: this.cells }; }

  static fromJSON(doc: PMNode, json: { anchor: number; head: number }): CellSelection {
    const cells = (json as { cells?: number[] }).cells;
    return cells && cells.length > 1 ? new MultiCellSelection(doc, cells) : new CellSelection(doc.resolve(json.anchor), doc.resolve(json.head));
  }

  getBookmark() { return new MultiCellBookmark(this.cells) as unknown as ReturnType<CellSelection["getBookmark"]>; }
}
Selection.jsonID("multicell", MultiCellSelection as unknown as typeof Selection);

class MultiCellBookmark {
  constructor(readonly cells: number[]) {}
  map(mapping: Mappable) { return new MultiCellBookmark(this.cells.map((p) => mapping.map(p, -1))); }
  resolve(doc: PMNode): Selection {
    const ok = this.cells.filter((p) => isCell(doc, p));
    if (ok.length > 1) return new MultiCellSelection(doc, ok);
    if (ok.length === 1) return new CellSelection(doc.resolve(ok[0]));
    return Selection.near(doc.resolve(Math.min(this.cells[0] ?? 0, doc.content.size)));
  }
}

/** 지금 고른 칸들(칸 선택이면 그 칸들, 커서만 있으면 커서가 있는 칸). */
function selectedCells(sel: Selection): number[] {
  if (sel instanceof MultiCellSelection) return [...sel.cells];
  if (sel instanceof CellSelection) { const out: number[] = []; sel.forEachCell((_n, p) => out.push(p)); return out; }
  const $cell = cellAround(sel.$head);
  return $cell ? [$cell.pos] : [];
}

/** Ctrl(⌘)+클릭: 누른 칸을 선택에 더하거나 뺀다. 다른 표의 칸이면 그 칸부터 새로 고른다. */
export const multiCellPlugin = () => new Plugin({
  key: new PluginKey("multiCell"),
  props: {
    handleDOMEvents: {
      mousedown(view, e) {
        if (!(e.ctrlKey || e.metaKey) || e.button !== 0 || e.shiftKey) return false;
        const hit = view.posAtCoords({ left: e.clientX, top: e.clientY });
        if (!hit) return false;
        const $cell = cellAround(view.state.doc.resolve(hit.pos));
        if (!$cell) return false;
        e.preventDefault();
        const { doc } = view.state;
        const table = $cell.node(-1);
        let cells = selectedCells(view.state.selection).filter((p) => isCell(doc, p) && doc.resolve(p).node(-1) === table);
        cells = cells.includes($cell.pos) ? cells.filter((p) => p !== $cell.pos) : [...cells, $cell.pos];
        let sel: Selection;
        if (cells.length > 1) sel = new MultiCellSelection(doc, cells);
        else if (cells.length === 1) sel = new CellSelection(doc.resolve(cells[0]));
        else sel = TextSelection.near(doc.resolve($cell.pos + 1));
        view.dispatch(view.state.tr.setSelection(sel));
        view.focus();
        return true;
      },
    },
  },
});
