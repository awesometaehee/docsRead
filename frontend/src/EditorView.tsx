import { EditorContent, useEditor } from "@tiptap/react";
import type { Editor, JSONContent } from "@tiptap/core";
import type React from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { get } from "./api";
import { extensions, insertColumnBreak, partsToInline, setColumnCount } from "./editorExtensions";
import { addLineNear, autoHangAll, clearTextStyleAttr, fitImage, setImageAttrs, canJoinPrevBox, canMoveIntoPrevBox, canPin, hangOf, insertPageBreak, isPinned, joinPrevBox,
  layoutPages, liftOutOfBox, moveIntoPrevBox, setHang, splitBox, togglePin, toggleHang, unwrapBox } from "./blockCommands";
import type { PageInfo } from "./blockCommands";
import { applyHeaderStyle, columnPct, columnRatios, copyTableFormat, setColumnRatios, equalizeColumns, insertTable, pasteTableFormat, rowHeightOf, setColumnPct,
  setRowHeight } from "./tableCommands";
import type { TableFormat } from "./tableCommands";
import { tip } from "./tips";
import { installPrintLines } from "./printLines";
import { addSplitCol, findSplit, insertSplit, removeSplitCol, setSplitColShare, setSplitGap, setSplitRatios, splitColShare, splitRatios, unwrapSplit } from "./splitLayout";
import type { DocRow, ModuleDetail, ModuleRow, Variant } from "./types";

interface DraftRow { id: number; title: string; doc_type: string; base_document_id: number | null; updated_at: string }
interface PaperStyle { body_pt?: number; line_height?: number; page_w?: number; page_h?: number; pad?: number }
interface Draft extends DraftRow { content: JSONContent; style: PaperStyle }
interface VarInfo { name: string; value: string; count: number; mixed: boolean; options: string[] }

async function send<T>(method: string, url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${url}`);
  return res.json() as Promise<T>;
}

/* ---------- 편집 동작 ---------- */

function listVariables(editor: Editor): VarInfo[] {
  const map = new Map<string, VarInfo>();
  editor.state.doc.descendants((node) => {
    if (node.type.name !== "variable") return;
    const { name, value, options } = node.attrs as { name: string; value: string; options: string[] };
    const cur = map.get(name);
    if (cur) {
      cur.count += 1;
      if (cur.value !== value) cur.mixed = true;
    } else map.set(name, { name, value, count: 1, mixed: false, options: options ?? [] });
  });
  return [...map.values()];
}

/** 같은 이름의 변수 전부의 값을 바꾼다(되돌리기 한 번으로 복구된다). */
function setVariable(editor: Editor, name: string, patch: { value?: string; name?: string }) {
  editor.commands.command(({ tr, state }) => {
    state.doc.descendants((node, pos) => {
      if (node.type.name === "variable" && node.attrs.name === name) tr.setNodeMarkup(pos, undefined, { ...node.attrs, ...patch });
    });
    return true;
  });
}

function currentModule(editor: Editor) {
  const { $from } = editor.state.selection;
  for (let d = $from.depth; d > 0; d--) {
    const node = $from.node(d);
    if (node.type.name === "moduleBlock") return { node, pos: $from.before(d) };
  }
  return null;
}

function moduleJSON(mod: ModuleDetail, v: Variant, keep: JSONContent[] = []): JSONContent {
  const heads = keep.filter((n) => n.type === "heading");
  const tables = keep.filter((n) => n.type === "table");
  const head: JSONContent[] = heads.length || mod.kind !== "article" ? heads
    : [{ type: "heading", attrs: { level: 3 }, content: [{ type: "text", text: `제0조 (${mod.title})` }] }];
  return {
    type: "moduleBlock",
    attrs: { moduleId: mod.id, variantId: v.id, title: mod.title, kind: mod.kind, nDocs: mod.n_docs, nVariants: mod.variants.length, ordinal: v.ordinal },
    content: [...head, { type: "paragraph", content: partsToInline(v.parts, v.slots, mod.title) }, ...tables],
  };
}

/** 약관의 '제N조' 제목을 문서 순서대로 다시 매긴다. 본문 속 '제N조 참조' 문구는 바꾸지 않는다. */
function renumberArticles(editor: Editor): number {
  const hits: { from: number; to: number }[] = [];
  editor.state.doc.descendants((node, pos) => {
    if (node.type.name !== "heading" || node.attrs.level !== 3) return;
    const m = /^제\s*\d+\s*조/.exec(node.textContent);
    if (m) hits.push({ from: pos + 1, to: pos + 1 + m[0].length });
  });
  editor.commands.command(({ tr }) => {
    for (let i = hits.length - 1; i >= 0; i--) tr.insertText(`제${i + 1}조`, hits[i].from, hits[i].to);
    return true;
  });
  return hits.length;
}

/* ---------- 오른쪽 패널 ---------- */

function VariablePanel({ editor, selected }: { editor: Editor; selected: string | null }) {
  const vars = listVariables(editor);
  const [filter, setFilter] = useState("");
  const shown = vars.filter((v) => !filter || v.name.includes(filter) || v.value.includes(filter));
  const active = vars.find((v) => v.name === selected);
  return (
    <div className="panel">
      {active && (
        <div className="varbox">
          <label>변수 이름
            <input defaultValue={active.name} key={active.name}
              onBlur={(e) => { const n = e.target.value.trim(); if (n && n !== active.name) setVariable(editor, active.name, { name: n }); }} />
          </label>
          <small className="mute">이름을 같게 하면 여러 곳의 값을 한 번에 바꿀 수 있습니다. 현재 {active.count}곳.</small>
          <label>값
            <textarea rows={2} value={active.value} onChange={(e) => setVariable(editor, active.name, { value: e.target.value })} />
          </label>
          {active.options.length > 1 && (
            <div className="opts">
              <small className="mute">다른 문서에서 쓰인 값</small>
              {active.options.map((o, i) => (
                <button key={i} className={o === active.value ? "on" : ""} onClick={() => setVariable(editor, active.name, { value: o })}>{o || "(없음)"}</button>
              ))}
            </div>
          )}
        </div>
      )}
      <h5>문서의 변수 {vars.length}개</h5>
      <input className="filter" placeholder="변수 찾기" value={filter} onChange={(e) => setFilter(e.target.value)} />
      <div className="varlist">
        {shown.slice(0, 200).map((v) => (
          <label key={v.name} className={v.name === selected ? "on" : ""}>
            <span>{v.name}{v.count > 1 && <em> ×{v.count}</em>}</span>
            <input value={v.mixed ? "" : v.value} placeholder={v.mixed ? "(값이 서로 다름)" : "(없음)"} onChange={(e) => setVariable(editor, v.name, { value: e.target.value })} />
          </label>
        ))}
        {shown.length > 200 && <small className="mute">… 외 {shown.length - 200}개 (찾기로 좁히세요)</small>}
      </div>
    </div>
  );
}

function ModulePanel({ editor }: { editor: Editor }) {
  const cur = currentModule(editor);
  const moduleId: number | null = cur?.node.attrs.moduleId ?? null;
  const [detail, setDetail] = useState<ModuleDetail | null>(null);
  const [q, setQ] = useState("");
  const [found, setFound] = useState<ModuleRow[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setDetail(null);
    if (moduleId !== null) get<ModuleDetail>(`/api/modules/${moduleId}`).then(setDetail).catch((e) => setError(String(e)));
  }, [moduleId]);
  useEffect(() => {
    if (!q.trim()) { setFound([]); return; }
    const t = setTimeout(() => get<ModuleRow[]>(`/api/modules?min_docs=2&q=${encodeURIComponent(q.trim())}`).then((r) => setFound(r.slice(0, 12))).catch((e) => setError(String(e))), 250);
    return () => clearTimeout(t);
  }, [q]);

  const replaceWith = (v: Variant) => {
    if (!cur || !detail) return;
    const json = moduleJSON(detail, v, (cur.node.toJSON() as JSONContent).content ?? []);
    editor.chain().focus().insertContentAt({ from: cur.pos, to: cur.pos + cur.node.nodeSize }, json).run();
  };
  const insert = async (id: number) => {
    try {
      const mod = await get<ModuleDetail>(`/api/modules/${id}`);
      const { $from } = editor.state.selection;
      const at = $from.depth > 0 ? $from.after(1) : editor.state.doc.content.size;
      editor.chain().focus().insertContentAt(at, moduleJSON(mod, mod.variants[0])).run();
      setQ("");
    } catch (e) { setError(String(e)); }
  };

  return (
    <div className="panel">
      {error && <div className="error" onClick={() => setError(null)}>{error}</div>}
      <h5>모듈 넣기</h5>
      <input className="filter" placeholder="모듈 제목·문안 검색 (예: 청약철회)" value={q} onChange={(e) => setQ(e.target.value)} />
      {found.map((m) => (
        <button key={m.id} className="pick" onClick={() => insert(m.id)}>
          <b>{m.title}</b><small>문서 {m.n_docs} · 문안 {m.n_variants}종 — 커서가 있는 블록 뒤에 넣기</small>
        </button>
      ))}
      <h5>현재 모듈</h5>
      {!cur && <p className="mute">모듈 블록(파란 선) 안에 커서를 두면 문안을 바꿀 수 있습니다.</p>}
      {cur && (
        <>
          <p><b>{cur.node.attrs.title}</b><br /><span className="mute">{cur.node.attrs.nDocs}개 문서 공통</span></p>
          <div className="row">
            <button onClick={() => editor.chain().focus().lift("moduleBlock").run()} {...tip("모듈 풀기")}>모듈 풀기</button>
            <button onClick={() => editor.chain().focus().deleteRange({ from: cur.pos, to: cur.pos + cur.node.nodeSize }).run()}>블록 삭제</button>
          </div>
          {detail && detail.variants.map((v) => (
            <div key={v.id} className={`vcard ${v.id === cur.node.attrs.variantId ? "on" : ""}`}>
              <header>
                <b>문안 {v.ordinal + 1}</b><span className="mute">{v.n_docs}개 문서 · 변수 {v.slots.length}</span>
                <button onClick={() => replaceWith(v)}>{v.id === cur.node.attrs.variantId ? "원래 문안으로" : "이 문안으로"}</button>
              </header>
              <p>{v.text.slice(0, 220)}{v.text.length > 220 && "…"}</p>
            </div>
          ))}
        </>
      )}
    </div>
  );
}

/* ---------- 편집 화면 ---------- */

const COLORS = ["#231f20", "#ed1c24", "#2e3192", "#58595b", "#939598", "#ffffff"];
const FILLS = ["#dadbdc", "#d1d3d4", "#8d9d9f", "#fffef3", "#fff6dd", "#eef3ff", "#ffffff"];
/** 누르면 커서가 다른 곳으로 가는 버튼: 화면도 따라가야 하므로 위치를 고정하지 않는다. */
const MOVES_CURSOR = new Set(["쪽 나눔", "여기서 나누기", "앞 박스와 합치기", "위에 줄", "아래에 줄", "표 넣기", "단 넘기기", "1단", "2단", "3단",
  "위 박스에 넣기", "선택 줄 박스 밖으로", "박스 풀기", "이미지", "캡션 그림", "↶", "↷"]);
const SIZES = [4.8, 5.5, 6, 6.5, 7, 7.6, 8, 8.4, 9, 10, 11, 12, 14, 16, 18, 22];

/** 색 고르기: 자주 쓰는 색 + 직접 고르기 + 지우기. */
/** 도구 묶음: 작은 이름표 + 도구들. 묶음 사이는 세로 구분선으로 나눈다. */
function G({ cap, children }: { cap: string; children: React.ReactNode }) {
  return <div className="tg" role="group" aria-label={cap}><span className="tg-cap">{cap}</span>{children}</div>;
}
const DANGER = new Set(["표 삭제", "박스 풀기", "그림 삭제", "단 삭제"]);
const PRIMARY = new Set(["표 넣기", "인쇄 · PDF"]);

function Swatches({ label, colors, onPick }: { label: string; colors: string[]; onPick: (c: string | null) => void }) {
  return (
    <span className="sw">
      <em {...tip(label.split(":")[0].trim(), undefined, label)}>{label}</em>
      {colors.map((c) => <button key={c} style={{ background: c }} aria-label={`${label} ${c}`} {...tip(label.split(":")[0].trim(), `${c} 색을 적용합니다.`, label)}
        onMouseDown={(e) => { e.preventDefault(); onPick(c); }} />)}
      <input type="color" aria-label={`${label} 직접 고르기`} {...tip("직접", "목록에 없는 색을 직접 고릅니다.", `${label} 직접 고르기`)} onChange={(e) => onPick(e.target.value)} />
      <button className="none" aria-label="지우기" {...tip("지우기")} onMouseDown={(e) => { e.preventDefault(); onPick(null); }}>×</button>
    </span>
  );
}

/** 열 비율 한꺼번에 입력: "5, 5.6, 6.5, …" 처럼 열마다 숫자를 넣고 Enter(또는 칸을 벗어나면) 적용. 합은 100으로 맞춘다. */
function RatioInput({ value, onApply, label = "열 비율" }: { value: number[] | null; onApply: (nums: number[]) => void; label?: string }) {
  const shown = value ? value.join(", ") : "";
  const [text, setText] = useState(shown);
  const [editing, setEditing] = useState(false);
  useEffect(() => { if (!editing) setText(shown); }, [shown, editing]);
  const apply = () => {
    setEditing(false);
    const nums = text.split(/[\s,;/]+/).filter(Boolean).map(Number);
    if (nums.length && nums.every((n) => Number.isFinite(n) && n > 0) && text !== shown) onApply(nums);
    else setText(shown);
  };
  return (
    <label className="num ratio" {...tip(label)}>{label}
      <input value={text} style={{ width: 170 }} onFocus={() => setEditing(true)} onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); (e.target as HTMLInputElement).blur(); } if (e.key === "Escape") { setText(shown); setEditing(false); (e.target as HTMLInputElement).blur(); } }}
        onBlur={apply} />
    </label>
  );
}

/**
 * 숫자 입력: 입력하거나 화살표로 바꾸는 즉시 적용한다.
 * 적용 명령이 편집기로 포커스를 가져가므로(다음 프레임) 그 뒤에 입력 칸으로 포커스를 돌려 이어서 칠 수 있게 한다.
 * 빈 칸은 입력 도중일 수 있어 포커스가 벗어날 때만 '기본값(null)'으로 적용한다.
 */
function Num({ label, value, onSet, step = 1, width = 46 }: { label: string; value: number | null | undefined; onSet: (v: number | null) => void; step?: number; width?: number }) {
  const ref = useRef<HTMLInputElement>(null);
  const [text, setText] = useState(value == null ? "" : String(value));
  useEffect(() => {   // 커서 이동 등으로 대상이 바뀌면 표시값을 맞춘다(입력 중인 값은 건드리지 않음)
    const v = value == null ? "" : String(value);
    if (document.activeElement !== ref.current || (text !== "" && Number(text) !== value)) setText(v);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);
  const apply = (v: number | null) => {
    if (v === (value ?? null)) return;
    onSet(v);
    requestAnimationFrame(() => ref.current?.focus());
  };
  return (
    <label className="num" {...tip(label)}>{label}
      <input ref={ref} type="number" step={step} style={{ width }} value={text}
        onChange={(e) => { setText(e.target.value); if (e.target.value !== "" && Number.isFinite(Number(e.target.value))) apply(Number(e.target.value)); }}
        onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
        onBlur={() => { if (text === "") { if (value != null) onSet(null); } else if (Number(text) !== value) onSet(Number(text)); }} />
    </label>
  );
}

function DraftEditor({ draft, onSaved }: { draft: Draft; onSaved: (row: DraftRow) => void }) {
  const [tick, setTick] = useState(0);
  const [pageInfo, setPageInfo] = useState<PageInfo>({ pages: 1, overflow: [] });
  const scrollRef = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState<"saved" | "dirty" | "saving" | "error">("saved");
  const [title, setTitle] = useState(draft.title);
  const [tab, setTab] = useState<"vars" | "module">("vars");
  const [note, setNote] = useState("");
  const [zoom, setZoom] = useState(draft.doc_type === "brochure" ? 1.5 : 1.25);
  const [marks, setMarks] = useState(true);
  const [paper, setPaper] = useState<PaperStyle>(draft.style ?? {});
  const [grid, setGrid] = useState({ rows: 3, cols: 3 });
  // 새 표를 머리행 서식(회색·가운데)으로 넣을지: 사용자마다 기억(저장소를 못 쓰면 켜진 채로)
  const [headStyle, setHeadStyle] = useState(() => { try { return localStorage.getItem("docsread.headStyle") !== "0"; } catch { return true; } });
  const [tableFmt, setTableFmt] = useState<TableFormat | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  const fileRef = useRef<HTMLInputElement>(null);
  const asFigure = useRef(false);
  const paperRef = useRef(paper);
  paperRef.current = paper;
  const paperVars = {
    "--body": `${paper.body_pt ?? 8.4}pt`, "--lh": String(paper.line_height ?? 1.2), "--pw": `${paper.page_w ?? 595.3}pt`,
    "--ph": `${paper.page_h ?? 841.9}pt`, "--pad": `${paper.pad ?? 14.2}pt`, "--pages": String(Math.max(1, pageInfo.pages)), zoom,
  } as React.CSSProperties;

  const save = useCallback(async (ed: Editor, t: string) => {
    setStatus("saving");
    try {
      onSaved(await send<DraftRow>("PUT", `/api/drafts/${draft.id}`, { title: t, content: ed.getJSON(), style: paperRef.current }));
      setStatus("saved");
    } catch { setStatus("error"); }
  }, [draft.id, onSaved]);

  const titleRef = useRef(title);
  titleRef.current = title;
  const editor = useEditor({
    extensions,
    content: draft.content,
    onUpdate: ({ editor: ed }) => {
      setStatus("dirty");
      clearTimeout(timer.current);
      timer.current = setTimeout(() => save(ed as Editor, titleRef.current), 1200);
      setTick((n) => n + 1);
    },
    onSelectionUpdate: () => setTick((n) => n + 1),
  });
  useEffect(() => () => clearTimeout(timer.current), []);
  // 인쇄할 때 표 괘선을 지정 굵기 그대로 다시 긋는다(크롬은 얇은 CSS 테두리를 0.75pt 로 올려 찍음)
  useEffect(() => installPrintLines(() => scrollRef.current?.querySelector<HTMLElement>(".paper") ?? null), []);
  useEffect(() => {   // 알림은 잠시 보여 주고 저절로 닫는다
    if (!note) return;
    const t = setTimeout(() => setNote(""), 6000);
    return () => clearTimeout(t);
  }, [note]);
  // 쪽 배치: 내용·배율·용지가 바뀔 때마다 쪽 나눔 높이를 다시 맞추고 넘친 쪽을 찾는다(글꼴 로딩 뒤에도 한 번 더)
  useEffect(() => {
    const run = () => {
      const info = layoutPages(scrollRef.current?.querySelector<HTMLElement>(".paper") ?? null);
      setPageInfo((cur) => (cur.pages === info.pages && cur.overflow.join() === info.overflow.join() ? cur : info));
    };
    const raf = requestAnimationFrame(run);
    const late = setTimeout(run, 600);
    return () => { cancelAnimationFrame(raf); clearTimeout(late); };
  }, [tick, zoom, paper, editor]);

  if (!editor) return null;
  const later = (t = title) => { setStatus("dirty"); clearTimeout(timer.current); timer.current = setTimeout(() => save(editor, t), 1200); };
  const sel = editor.state.selection as unknown as { node?: { type: { name: string }; attrs: Record<string, never> } };
  const picked = sel.node?.type.name ?? null;
  const selectedVar = picked === "variable" ? (sel.node!.attrs.name as string) : null;
  const hasText = !editor.state.selection.empty && !picked;
  const inTable = editor.isActive("table");
  const inBox = editor.isActive("box");
  const inCols = editor.isActive("columns");
  const inGallery = editor.isActive("gallery");
  const inFigure = editor.isActive("figure") || picked === "figure";
  const isImage = picked === "image";
  const split = findSplit(editor.state);
  // 도구 버튼은 편집기로 포커스만 돌려준다(커서 위치로 화면을 굴리지 않음)
  const c = () => editor.chain().focus(null, { scrollIntoView: false });
  const A = (name: string) => editor.getAttributes(name);
  const fontSize = (A("textStyle").fontSize as number | null) ?? null;
  const hang = hangOf(editor.state);
  const colPct = inTable ? columnPct(editor) : null;
  const colRatios = inTable ? columnRatios(editor) : null;
  const blockType = editor.isActive("heading", { level: 1 }) ? "h1" : editor.isActive("heading", { level: 2 }) ? "h2" : editor.isActive("heading", { level: 3 }) ? "h3" : "p";

  const setBlock = (v: string) => (v === "p" ? c().setParagraph().run() : c().setHeading({ level: Number(v[1]) as 1 | 2 | 3 }).run());
  // 지우기는 직접 구현한다: TipTap 의 removeEmptyTextStyle 은 칸 선택(여러 행)일 때 범위 안 모든 칸의 글자 크기·색까지 지운다
  const setSize = (v: number | null) => (v ? c().setMark("textStyle", { fontSize: v }).run() : clearTextStyleAttr(editor, "fontSize"));
  const setColor = (v: string | null) => (v ? c().setColor(v).run() : clearTextStyleAttr(editor, "color"));
  const align = (v: "left" | "center" | "right" | "justify") => (inTable ? c().setCellAttribute("align", v === "justify" ? "left" : v).run() : c().setTextAlign(v).run());
  // 배경색은 가장 안쪽 대상에 적용: 표 칸 > 제목 띠 > 사진 묶음 > 박스
  const fillTarget = inTable ? "표 칸" : editor.isActive("heading") ? "제목 띠" : inGallery ? "사진 묶음" : inBox ? "박스" : null;
  const setFill = (v: string | null) => {
    if (inTable) c().setCellAttribute("background", v).run();
    else if (editor.isActive("heading")) c().updateAttributes("heading", { bg: v }).run();
    else if (inGallery) c().updateAttributes("gallery", { bg: v }).run();
    else if (inBox) c().updateAttributes("box", { bg: v }).run();
  };
  const makeVariable = () => {
    const { from, to } = editor.state.selection;
    const value = editor.state.doc.textBetween(from, to, " ");
    if (!value.trim()) return;
    const name = `변수${listVariables(editor).length + 1}`;
    c().insertContentAt({ from, to }, { type: "variable", attrs: { name, value, options: [value], vtype: "문구" } }).run();
    setTab("vars");
  };
  const setColumns = (n: number) => setColumnCount(editor, n);
  const upload = async (file: File) => {
    try {
      const res = await fetch("/api/assets", { method: "POST", headers: { "Content-Type": file.type || "application/octet-stream" }, body: file });
      if (!res.ok) throw new Error((await res.json().catch(() => ({ detail: res.statusText }))).detail);
      const img = (await res.json()) as { src: string; width: number; height: number };
      const w = Math.min(160, Math.round(img.width * 0.75));
      if (asFigure.current) {
        const h = Math.round((w * img.height) / img.width);
        const node = { type: "figure", attrs: { w, h, images: [{ src: img.src, x: 0, y: 0, w, h }] }, content: [{ type: "text", text: "캡션" }] };
        const { $from } = editor.state.selection;
        let at = $from.depth > 0 ? $from.after(1) : editor.state.doc.content.size;
        for (let d = $from.depth; d > 0; d--) if ($from.node(d).type.name === "figure") at = $from.after(d);
        c().insertContentAt(at, node).run();
      } else c().insertContent({ type: "image", attrs: { src: img.src, widthPt: w } }).run();
    } catch (e) { setNote(`이미지를 넣지 못했습니다: ${e instanceof Error ? e.message : e}`); }
  };
  // 서식 버튼을 누른 뒤에는 화면 위치를 그대로 둔다. 칸 선택 상태에서 포커스가 다음 프레임에 돌아오며
  // 브라우저가 선택 위치(예: 여러 행을 합친 칸의 끝)로 화면을 굴리기 때문. 커서를 옮기는 게 목적인 버튼은 제외.
  const keepScroll = (run: () => void) => {
    const sc = scrollRef.current;
    const top = sc?.scrollTop ?? 0;
    run();
    if (!sc) return;
    requestAnimationFrame(() => requestAnimationFrame(() => { sc.scrollTop = top; }));
  };
  const btn = (label: string, on: boolean, run: () => void, hint?: string) => (
    <button className={[on ? "on" : "", DANGER.has(label) ? "danger" : "", PRIMARY.has(label) ? "primary" : ""].filter(Boolean).join(" ") || undefined} {...tip(label, hint)}
      onMouseDown={(e) => { e.preventDefault(); if (MOVES_CURSOR.has(label)) run(); else keepScroll(run); }}>{label}</button>
  );
  const fig = A("figure") as { w?: number; h?: number; images?: { src: string; x: number; y: number; w: number; h: number }[] };

  return (
    <div className="edwrap">
      <div className="edmain">
        <div className="edbar">
          <input className="edtitle" value={title} aria-label="문서 제목" onChange={(e) => { setTitle(e.target.value); later(e.target.value); }} />
          <span className={`save ${status}`}>{{ saved: "저장됨", dirty: "수정됨", saving: "저장 중…", error: "저장 실패 — 다시 시도" }[status]}</span>
          {status === "error" && <button onClick={() => save(editor, title)}>다시 저장</button>}
          {/* 문서 전체 설정과 보기 도구: 선택에 따라 바뀌지 않으므로 제목 줄에 둔다 */}
          <span className="edview tools">
            <G cap="문서">
              <Num label="본문 pt" value={paper.body_pt ?? 8.4} step={0.1} onSet={(v) => { if (v && v >= 3 && v <= 30) { setPaper({ ...paper, body_pt: v }); later(); } }} />
              <Num label="줄 간격" value={paper.line_height ?? 1.2} step={0.05} onSet={(v) => { if (v && v >= 0.9 && v <= 3) { setPaper({ ...paper, line_height: v }); later(); } }} />
              <span className={`pages${pageInfo.overflow.length ? " over" : ""}`}
                {...tip("쪽 수", pageInfo.overflow.length ? `${pageInfo.overflow.join(", ")}쪽 내용이 한 쪽 높이를 넘습니다. 글을 줄이거나 쪽 나눔 위치를 옮기세요.` : "인쇄될 쪽 수입니다. 편집 화면의 빨간 선이 쪽 경계입니다. 쪽 나눔 앞 내용이 한 쪽을 넘으면 빨간색으로 알려 줍니다.", pageInfo.overflow.length ? "쪽 넘침" : "쪽 수")}>
                {pageInfo.pages}쪽{pageInfo.overflow.length ? ` · ${pageInfo.overflow.join(",")}쪽 넘침` : ""}
              </span>
            </G>
            <G cap="보기">
              {btn("모듈·변수 표시", marks, () => setMarks(!marks))}
              <select className="zoom" value={zoom} onChange={(e) => setZoom(Number(e.target.value))} aria-label="확대" {...tip("확대")}>
                {[1, 1.25, 1.5, 2, 2.5].map((z) => <option key={z} value={z}>{z * 100}%</option>)}
              </select>
            </G>
            {btn("인쇄 · PDF", false, () => window.print())}
          </span>
        </div>
        {/* 1줄: 글자·문단 */}
        <div className="edbar tools">
          <G cap="편집">
            {btn("↶", false, () => c().undo().run())}
            {btn("↷", false, () => c().redo().run())}
          </G>
          <G cap="글자">
            <select value={blockType} onChange={(e) => setBlock(e.target.value)} aria-label="문단 종류" {...tip("문단 종류")}>
              <option value="p">본문</option><option value="h1">제목 띠</option><option value="h2">섹션 띠</option><option value="h3">조 제목</option>
            </select>
            <select value={fontSize ?? ""} onChange={(e) => setSize(e.target.value ? Number(e.target.value) : null)} aria-label="글자 크기" {...tip("글자 크기")}>
              <option value="">크기: 본문</option>
              {[...new Set([...SIZES, ...(fontSize ? [fontSize] : [])])].sort((a, b) => a - b).map((n) => <option key={n} value={n}>{n}pt</option>)}
            </select>
            {btn("굵게", editor.isActive("bold"), () => c().toggleBold().run())}
            <Swatches label="글자색" colors={COLORS} onPick={setColor} />
          </G>
          <G cap="문단">
            <div className="seg">
              {btn("왼쪽", false, () => align("left"))}{btn("가운데", false, () => align("center"))}
              {btn("오른쪽", false, () => align("right"))}{btn("양쪽", false, () => align("justify"))}
            </div>
            <div className="seg">
              {btn("내어쓰기", hang !== null, () => toggleHang(editor), "둘째 줄부터 앞 번호·기호(①, 1., ※ 등) 폭만큼 들여 씁니다")}
              {btn("전체 자동", false, () => setNote(`번호·기호로 시작하는 문단 ${autoHangAll(editor)}개에 내어쓰기를 맞췄습니다.`), "문서 전체에서 ①·1.·※ 등으로 시작하는 문단에 기호 폭만큼 내어쓰기를 맞춥니다")}
            </div>
            {hang !== null && <Num label="폭 em" value={hang} step={0.05} onSet={(v) => setHang(editor, v)} width={50} />}
          </G>
          <G cap="채우기">
            <Swatches label={fillTarget ? `배경: ${fillTarget}` : "배경"} colors={FILLS} onPick={setFill} />
          </G>
        </div>
        {/* 2줄: 넣기·배치 */}
        <div className="edbar tools">
          <G cap="새 표">
            <Num label="표" value={grid.rows} onSet={(v) => setGrid({ ...grid, rows: Math.max(1, v ?? 1) })} width={36} />
            <Num label="×" value={grid.cols} onSet={(v) => setGrid({ ...grid, cols: Math.max(1, v ?? 1) })} width={36} />
            {btn("머리행 서식", headStyle, () => { const v = !headStyle; setHeadStyle(v); try { localStorage.setItem("docsread.headStyle", v ? "1" : "0"); } catch { /* 저장 못 해도 이번 화면에서는 적용 */ } },
              "켜면 새 표의 첫 행을 회색으로, 모든 칸을 가운데 정렬로 넣습니다")}
            {btn("표 넣기", false, () => insertTable(editor, grid.rows, grid.cols, headStyle))}
          </G>
          <G cap="넣기">
            {btn("박스로 감싸기", false, () => c().wrapIn("box", { border: true, round: false, pad: 7 }).run(), "선택한 문단들을 테두리 상자로 묶습니다")}
            {canMoveIntoPrevBox(editor.state) && btn("위 박스에 넣기", false, () => moveIntoPrevBox(editor), "선택한 줄을 바로 위 박스의 끝으로 옮깁니다")}
            {btn("이미지", false, () => { asFigure.current = false; fileRef.current?.click(); }, "글줄 안에 이미지를 넣습니다")}
            {btn("캡션 그림", false, () => { asFigure.current = true; fileRef.current?.click(); }, "아래에 설명 글이 붙는 그림을 넣습니다")}
            <input ref={fileRef} type="file" accept="image/*" hidden onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) upload(f); }} />
          </G>
          <G cap="쪽·단">
            {btn("쪽 나눔", false, () => insertPageBreak(editor), "여기서 다음 쪽으로 넘깁니다. 박스 안이면 박스도 쪽마다 나뉩니다")}
            {canPin(editor.state) && btn("쪽 아래 고정", isPinned(editor.state), () => togglePin(editor), "이 블록(이어진 고정 블록들과 함께)을 쪽 맨 아래에 붙입니다. 꼬리말·연락처 띠에 씁니다")}
            <div className="seg">
              {btn("1단", !inCols, () => setColumns(1))}
              {btn("2단", inCols && A("columns").count === 2, () => setColumns(2))}
              {btn("3단", inCols && A("columns").count === 3, () => setColumns(3))}
            </div>
            {inCols && btn("단 넘기기", false, () => insertColumnBreak(editor), "커서 뒤의 내용을 다음 단 맨 위로 보냅니다")}
          </G>
          <G cap="나란히">
            {btn("나란히 놓기", !!split, () => insertSplit(editor), split ? "지금 단 오른쪽에 빈 단을 하나 더합니다" : "선택한 블록을 왼쪽 단에 두고 오른쪽에 빈 단을 만들어 좌우로 나란히 놓습니다")}
            {split && (
              <>
                <Num label="단 너비 %" value={splitColShare(editor.state)} step={0.5} width={50} onSet={(v) => { if (v) setSplitColShare(editor, v); }} />
                <RatioInput label="단 비율" value={splitRatios(editor.state)} onApply={(nums) => {
                  if (!setSplitRatios(editor, nums)) setNote(`단 비율은 숫자 ${split.split.childCount}개를 쉼표로 구분해 넣으세요.`);
                }} />
                <Num label="단 간격" value={split.split.attrs.gap} step={0.5} width={42} onSet={(v) => setSplitGap(editor, v)} />
                {btn("단 삭제", false, () => removeSplitCol(editor))}
                {btn("나란히 풀기", false, () => unwrapSplit(editor))}
              </>
            )}
          </G>
          <G cap="문서">
            {btn("변수로 만들기", false, makeVariable, hasText ? "선택한 글자를 변수로 바꿉니다" : "먼저 글자를 선택하세요")}
            {btn("조 번호 다시 매기기", false, () => setNote(`조 제목 ${renumberArticles(editor)}개의 번호를 다시 매겼습니다. 본문 속 '제N조' 참조는 직접 확인하세요.`))}
          </G>
        </div>
        {/* 3줄: 지금 선택한 대상의 디자인. 늘 같은 높이로 둔다(선택에 따라 줄이 생기고 없어지면 편집 화면이 위아래로 밀린다) */}
        <div className={`edbar tools ctx${inTable || inBox || inGallery || inFigure || isImage ? " active" : ""}`}>
          {!(inTable || inBox || inGallery || inFigure || isImage) && <span className="ctxhint">표 · 박스 · 그림 · 이미지 안에 커서를 두면 여기에 그 대상의 서식 도구가 나옵니다.</span>}
          {inTable && !isImage && (
            <>
              <span className="ctx-tag" {...tip("표", "커서가 있는 표의 도구입니다. 칸을 끌면 직사각형으로, Ctrl+클릭하면 떨어진 칸을 하나씩 더하거나 빼며 고를 수 있습니다. 고른 칸 모두에 배경·괘선·정렬·글자 서식·행 높이가 적용됩니다.", "표")}>표</span>
              <G cap="구조">
                <div className="seg">
                  {btn("행 추가", false, () => c().addRowAfter().run())}{btn("행 삭제", false, () => c().deleteRow().run())}
                </div>
                <div className="seg">
                  {btn("열 추가", false, () => c().addColumnAfter().run())}{btn("열 삭제", false, () => c().deleteColumn().run())}
                </div>
                <div className="seg">
                  {btn("칸 합치기", false, () => c().mergeCells().run(), "여러 칸을 끌어서(직사각형으로) 고른 뒤 누르세요. Ctrl+클릭으로 따로 고른 칸은 합칠 수 없습니다.")}
                  {btn("칸 나누기", false, () => c().splitCell().run())}
                </div>
              </G>
              <G cap="괘선">
                <div className="seg">
                  {btn("전체", false, () => c().setCellAttribute("ln", [1, 1, 1, 1]).run())}
                  {btn("가로만", false, () => c().setCellAttribute("ln", [1, 0, 1, 0]).run())}
                  {btn("세로만", false, () => c().setCellAttribute("ln", [0, 1, 0, 1]).run())}
                  {btn("없음", false, () => c().setCellAttribute("ln", [0, 0, 0, 0]).run())}
                </div>
                {btn("바깥 테두리", !!A("table").frame, () => c().updateAttributes("table", { frame: !A("table").frame }).run())}
                <Swatches label="선 색" colors={["#231f20", "#58595b", "#939598", "#ffffff"]} onPick={(v) => c().updateAttributes("table", { lineColor: v ?? "#231f20" }).run()} />
                <Num label="선 굵기" value={A("table").lineW} step={0.1} onSet={(v) => c().updateAttributes("table", { lineW: v ?? 0.5 }).run()} />
              </G>
              <G cap="크기">
                <Num label="행 높이" value={rowHeightOf(editor.state)} onSet={(v) => setRowHeight(editor, v)} />
                {btn("표 전체", false, () => setRowHeight(editor, rowHeightOf(editor.state), true), "이 행 높이를 표의 모든 행에 적용합니다(여러 행은 칸을 끌어 선택한 뒤 높이를 넣어도 됩니다)")}
                <Num label="열 너비 %" value={colPct} step={0.5} onSet={(v) => { if (v) setColumnPct(editor, v); }} width={50} />
                {btn("열 균등", false, () => equalizeColumns(editor), "모든 열을 같은 너비로")}
                <RatioInput value={colRatios} onApply={(nums) => {
                  if (!setColumnRatios(editor, nums)) setNote(`열 비율은 숫자 ${colRatios?.length ?? 0}개를 쉼표로 구분해 넣으세요.`);
                }} />
                <Num label="표 너비 %" value={A("table").widthPct} onSet={(v) => c().updateAttributes("table", { widthPct: v && v < 100 ? v : null }).run()} />
              </G>
              <G cap="배치">
                <select value={A("table").float ?? ""} onChange={(e) => c().updateAttributes("table", { float: e.target.value || null }).run()} aria-label="표 배치" {...tip("표 배치")}>
                  <option value="">글과 따로</option><option value="left">왼쪽에 두고 글이 옆으로</option><option value="right">오른쪽에 두고 글이 옆으로</option>
                </select>
                <div className="seg">
                  {btn("위에 줄", false, () => addLineNear(editor, "table", false), "표 바로 위에 빈 줄을 넣습니다")}
                  {btn("아래에 줄", false, () => addLineNear(editor, "table", true), "표 바로 아래에 빈 줄을 넣습니다")}
                </div>
              </G>
              <G cap="서식">
                {btn("머리행 서식", false, () => applyHeaderStyle(editor), "첫 행을 회색으로, 모든 칸을 가운데 정렬로")}
                {btn("서식 복사", false, () => { const f = copyTableFormat(editor); setTableFmt(f); if (f) setNote("표 서식을 복사했습니다. 다른 표에 커서를 두고 '서식 붙여넣기'를 누르세요."); }, "선 색·굵기, 머리행·본문 배경과 정렬, 행 높이, 열 비율을 복사합니다")}
                {tableFmt && btn("서식 붙여넣기", false, () => pasteTableFormat(editor, tableFmt), "복사한 서식을 이 표에 입힙니다(열 수가 같으면 열 비율까지)")}
              </G>
              <span className="ctx-end">{btn("표 삭제", false, () => c().deleteTable().run())}</span>
            </>
          )}
          {inBox && !inTable && (
            <>
              <span className="ctx-tag">박스</span>
              <G cap="모양">
                {btn("테두리", !!A("box").border, () => c().updateAttributes("box", { border: !A("box").border }).run())}
                {btn("둥근 위 모서리", !!A("box").round, () => c().updateAttributes("box", { round: !A("box").round }).run())}
                {btn("용지 끝까지", !!A("box").bleed, () => c().updateAttributes("box", { bleed: !A("box").bleed }).run())}
                <Swatches label="테두리 색" colors={["#939598", "#231f20", "#58595b"]} onPick={(v) => c().updateAttributes("box", { borderColor: v ?? "#939598" }).run()} />
                <Num label="테두리 굵기" value={A("box").borderW} step={0.1} onSet={(v) => c().updateAttributes("box", { borderW: v ?? 0.5 }).run()} />
              </G>
              <G cap="여백">
                <Num label="안쪽 여백" value={A("box").pad} onSet={(v) => c().updateAttributes("box", { pad: v ?? 0 }).run()} />
                <Num label="바깥 여백" value={A("box").margin} onSet={(v) => c().updateAttributes("box", { margin: v ?? null }).run()} />
              </G>
              <G cap="나누기·합치기">
                {btn("여기서 나누기", false, () => splitBox(editor), "커서가 있는 줄부터 새 박스로 나눕니다")}
                {canJoinPrevBox(editor.state) && btn("앞 박스와 합치기", false, () => joinPrevBox(editor), "바로 위 박스와 하나로 합칩니다")}
                {btn("선택 줄 박스 밖으로", false, () => liftOutOfBox(editor), "선택한 줄만 박스 밖으로 뺍니다")}
              </G>
              <G cap="줄 넣기">
                <div className="seg">
                  {btn("위에 줄", false, () => addLineNear(editor, "box", false), "박스 바로 위(밖)에 빈 줄을 넣습니다")}
                  {btn("아래에 줄", false, () => addLineNear(editor, "box", true), "박스 바로 아래(밖)에 빈 줄을 넣습니다")}
                </div>
              </G>
              <span className="ctx-end">{btn("박스 풀기", false, () => unwrapBox(editor), "박스를 없애고 내용만 남깁니다")}</span>
            </>
          )}
          {inGallery && (
            <>
              <span className="ctx-tag">사진 묶음</span>
              <G cap="크기·배치">
                <Num label="너비 %" value={A("gallery").widthPct} onSet={(v) => c().updateAttributes("gallery", { widthPct: v && v < 100 ? v : null }).run()} />
                <select value={A("gallery").float ?? ""} onChange={(e) => c().updateAttributes("gallery", { float: e.target.value || null }).run()} aria-label="사진 묶음 배치" {...tip("사진 묶음 배치")}>
                  <option value="">글과 따로</option><option value="left">왼쪽에 두고 글이 옆으로</option><option value="right">오른쪽에 두고 글이 옆으로</option>
                </select>
              </G>
            </>
          )}
          {inFigure && fig.w && (
            <>
              <span className="ctx-tag">캡션 그림</span>
              <G cap="크기">
                <Num label="너비 pt" value={fig.w} onSet={(v) => { if (v && v > 4) c().updateAttributes("figure", { w: v, h: Math.round(((fig.h ?? v) * v) / (fig.w ?? v) * 10) / 10,
                  images: (fig.images ?? []).map((im) => ({ ...im, x: (im.x * v) / fig.w!, y: (im.y * v) / fig.w!, w: (im.w * v) / fig.w!, h: (im.h * v) / fig.w! })) }).run(); }} />
              </G>
              <span className="ctx-end">{btn("그림 삭제", false, () => c().deleteNode("figure").run())}</span>
            </>
          )}
          {isImage && (
            <>
              <span className="ctx-tag">이미지</span>
              <G cap="크기">
                <Num label="너비 pt" value={A("image").widthPt} onSet={(v) => setImageAttrs(editor, { widthPt: v })} />
                {btn("칸에 맞추기", false, () => fitImage(editor))}
              </G>
              <G cap="배치">
                <select value={A("image").float ?? ""} onChange={(e) => setImageAttrs(editor, { float: e.target.value || null })} aria-label="이미지 배치" {...tip("이미지 배치")}>
                  <option value="">글줄 안</option><option value="left">왼쪽에 두고 글이 옆으로</option><option value="right">오른쪽에 두고 글이 옆으로</option>
                </select>
                {A("image").float && (
                  <select value={A("image").valign ?? ""} onChange={(e) => setImageAttrs(editor, { valign: e.target.value || null })} aria-label="옆 글 세로 위치" {...tip("옆 글 세로 위치")}>
                    <option value="">옆 글: 위에 맞춤</option><option value="middle">옆 글: 세로 가운데</option>
                  </select>
                )}
              </G>
            </>
          )}
        </div>
        {note && <div className="note" onClick={() => setNote("")}>{note}</div>}
        <div className="edscroll" ref={scrollRef}>
          <EditorContent editor={editor} className={`paper ${draft.doc_type} ${marks ? "marks" : ""}`} style={paperVars} />
        </div>
      </div>
      <aside className="edside">
        <div className="seg full">
          <button className={tab === "vars" ? "on" : ""} onClick={() => setTab("vars")}>변수</button>
          <button className={tab === "module" ? "on" : ""} onClick={() => setTab("module")}>모듈</button>
        </div>
        {tab === "vars" ? <VariablePanel editor={editor} selected={selectedVar} /> : <ModulePanel editor={editor} />}
      </aside>
    </div>
  );
}

export default function EditorView({ startFrom, onStarted }: { startFrom: number | null; onStarted: () => void }) {
  const [drafts, setDrafts] = useState<DraftRow[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [q, setQ] = useState("");
  const [docs, setDocs] = useState<DocRow[]>([]);
  const [kind, setKind] = useState<"" | "terms" | "brochure">("");
  const [more, setMore] = useState(0);      // 목록에 다 못 보여 준 검색 결과 수
  const [busy, setBusy] = useState(false);
  const [confirmDel, setConfirmDel] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => get<DraftRow[]>("/api/drafts").then(setDrafts).catch((e) => setError(String(e))), []);
  useEffect(() => { reload(); }, [reload]);
  useEffect(() => {
    if (!q.trim()) { setDocs([]); setMore(0); return; }
    const word = q.trim();
    const t = setTimeout(() => get<DocRow[]>(`/api/documents?q=${encodeURIComponent(word)}${kind ? `&doc_type=${kind}` : ""}`).then((r) => {
      // 서버는 종류순(상품설명서 → 약관)으로 주므로 앞에서 자르면 약관이 밀려난다.
      // 이름이 맞는 문서를 본문만 맞는 문서보다 앞에 두고, 종류를 가리지 않았으면 약관·상품설명서를 반씩 보여 준다.
      const named = (d: DocRow) => ((d.product ?? "") + d.filename).includes(word) ? 0 : 1;
      const ranked = [...r].sort((a, b) => named(a) - named(b));
      const pick = kind ? ranked.slice(0, 16)
        : [...ranked.filter((d) => d.doc_type === "terms").slice(0, 8), ...ranked.filter((d) => d.doc_type === "brochure").slice(0, 8)];
      setDocs(pick);
      setMore(r.length - pick.length);
    }).catch((e) => setError(String(e))), 250);
    return () => clearTimeout(t);
  }, [q, kind]);

  const create = useCallback(async (documentId: number | null, docType: "terms" | "brochure" = "terms") => {
    setBusy(true);
    try {
      const d = await send<Draft>("POST", "/api/drafts", documentId === null
        ? { doc_type: docType, title: docType === "brochure" ? "새 상품설명서" : "새 약관" } : { document_id: documentId });
      setDraft(d);
      setQ("");
      await reload();
    } catch (e) { setError(String(e)); }
    setBusy(false);
  }, [reload]);
  useEffect(() => {
    if (startFrom !== null) { onStarted(); create(startFrom); }
  }, [startFrom, onStarted, create]);

  const open = (id: number) => get<Draft>(`/api/drafts/${id}`).then(setDraft).catch((e) => setError(String(e)));
  const remove = async (id: number) => {
    try {
      await send("DELETE", `/api/drafts/${id}`);
      if (draft?.id === id) setDraft(null);
      setConfirmDel(null);
      await reload();
    } catch (e) { setError(String(e)); }
  };
  const onSaved = useMemo(() => (row: DraftRow) => setDrafts((cur) => [row, ...cur.filter((d) => d.id !== row.id)]), []);

  return (
    <>
      <aside className="noprint">
        <div className="filters">
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="기준 문서 검색 → 복제해서 시작" aria-label="기준 문서 검색" />
          <select className="wide" value={kind} onChange={(e) => setKind(e.target.value as "" | "terms" | "brochure")} aria-label="검색할 문서 종류">
            <option value="">약관 + 상품설명서</option>
            <option value="terms">약관만</option>
            <option value="brochure">상품설명서만</option>
          </select>
          <button className="half" disabled={busy} onClick={() => create(null, "terms")} {...tip("빈 약관", "약관 용지(본문 8.4pt)로 빈 문서를 만듭니다.", "빈 약관으로 시작")}>빈 약관</button>
          <button className="half" disabled={busy} onClick={() => create(null, "brochure")} {...tip("빈 상품설명서", "상품설명서 용지(본문 4.8pt, 작은 제목 띠, 좁은 표 칸)로 빈 문서를 만듭니다.", "빈 상품설명서로 시작")}>빈 상품설명서</button>
        </div>
        <ul>
          {docs.map((d) => (
            <li key={`doc${d.id}`} onClick={() => !busy && create(d.id)}>
              <div className="line1"><span className={`badge ${d.doc_type}`}>{d.doc_type === "terms" ? "약관" : "상품설명서"}</span><b>{d.product}</b></div>
              <div className="line2">{d.company} · {d.form_no} · 복제해서 편집 시작</div>
            </li>
          ))}
          {more > 0 && <li className="sep">검색 결과 {more}건이 더 있습니다. 검색어를 더 자세히 쓰거나 종류를 고르세요.</li>}
          {docs.length > 0 && <li className="sep">편집 문서</li>}
          {drafts.map((d) => (
            <li key={d.id} className={d.id === draft?.id ? "on" : ""} onClick={() => open(d.id)}>
              <div className="line1"><b>{d.title}</b></div>
              <div className="line2">
                {d.updated_at.replace("T", " ")}
                {confirmDel === d.id
                  ? <> · <button className="link danger" onClick={(e) => { e.stopPropagation(); remove(d.id); }}>정말 삭제</button> · <button className="link" onClick={(e) => { e.stopPropagation(); setConfirmDel(null); }}>취소</button></>
                  : <> · <button className="link" onClick={(e) => { e.stopPropagation(); setConfirmDel(d.id); }}>삭제</button></>}
              </div>
            </li>
          ))}
          {!drafts.length && !docs.length && <li className="sep">아직 편집 문서가 없습니다</li>}
        </ul>
      </aside>
      <main>
        {error && <div className="error" onClick={() => setError(null)}>{error}</div>}
        {busy && <div className="loading">문서를 편집용으로 변환하는 중…</div>}
        {!draft && !busy && (
          <div className="empty">
            <h2>편집기</h2>
            <p>왼쪽에서 기준이 될 약관·상품설명서를 검색해 복제하거나, 빈 문서로 시작하세요.</p>
            <p>복제한 문서에서는 조·항목이 모듈 블록으로, 문서마다 달라지는 부분이 노란 변수로 들어옵니다. 원본 데이터는 바뀌지 않습니다.</p>
          </div>
        )}
        {draft && !busy && <DraftEditor key={draft.id} draft={draft} onSaved={onSaved} />}
      </main>
    </>
  );
}
