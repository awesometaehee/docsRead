import { EditorContent, useEditor } from "@tiptap/react";
import type { Editor, JSONContent } from "@tiptap/core";
import type React from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { get } from "./api";
import { extensions, partsToInline } from "./editorExtensions";
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
            <button onClick={() => editor.chain().focus().lift("moduleBlock").run()} title="모듈 표시를 떼고 일반 문단으로 둡니다">모듈 풀기</button>
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
const SIZES = [4.8, 5.5, 6, 6.5, 7, 7.6, 8, 8.4, 9, 10, 11, 12, 14, 16, 18, 22];

/** 색 고르기: 자주 쓰는 색 + 직접 고르기 + 지우기. */
function Swatches({ label, colors, onPick }: { label: string; colors: string[]; onPick: (c: string | null) => void }) {
  return (
    <span className="sw" title={label}>
      <em>{label}</em>
      {colors.map((c) => <button key={c} style={{ background: c }} aria-label={`${label} ${c}`} onMouseDown={(e) => { e.preventDefault(); onPick(c); }} />)}
      <input type="color" aria-label={`${label} 직접 고르기`} onChange={(e) => onPick(e.target.value)} />
      <button className="none" title="지우기" onMouseDown={(e) => { e.preventDefault(); onPick(null); }}>×</button>
    </span>
  );
}

/** 숫자 입력: 값이 확정될 때(포커스 이동·Enter) 한 번만 적용한다. */
function Num({ label, value, onSet, step = 1, width = 46 }: { label: string; value: number | null | undefined; onSet: (v: number | null) => void; step?: number; width?: number }) {
  return (
    <label className="num">{label}
      <input type="number" step={step} style={{ width }} defaultValue={value ?? ""} key={String(value)}
        onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
        onBlur={(e) => { const v = e.target.value === "" ? null : Number(e.target.value); if (v !== (value ?? null)) onSet(v); }} />
    </label>
  );
}

function DraftEditor({ draft, onSaved }: { draft: Draft; onSaved: (row: DraftRow) => void }) {
  const [, setTick] = useState(0);
  const [status, setStatus] = useState<"saved" | "dirty" | "saving" | "error">("saved");
  const [title, setTitle] = useState(draft.title);
  const [tab, setTab] = useState<"vars" | "module">("vars");
  const [note, setNote] = useState("");
  const [zoom, setZoom] = useState(draft.doc_type === "brochure" ? 1.5 : 1.25);
  const [marks, setMarks] = useState(true);
  const [paper, setPaper] = useState<PaperStyle>(draft.style ?? {});
  const [grid, setGrid] = useState({ rows: 3, cols: 3 });
  const timer = useRef<ReturnType<typeof setTimeout>>();
  const fileRef = useRef<HTMLInputElement>(null);
  const asFigure = useRef(false);
  const paperRef = useRef(paper);
  paperRef.current = paper;
  const paperVars = {
    "--body": `${paper.body_pt ?? 8.4}pt`, "--lh": String(paper.line_height ?? 1.2), "--pw": `${paper.page_w ?? 595.3}pt`,
    "--ph": `${paper.page_h ?? 841.9}pt`, "--pad": `${paper.pad ?? 14.2}pt`, zoom,
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
  const c = () => editor.chain().focus();
  const A = (name: string) => editor.getAttributes(name);
  const fontSize = (A("textStyle").fontSize as number | null) ?? null;
  const blockType = editor.isActive("heading", { level: 1 }) ? "h1" : editor.isActive("heading", { level: 2 }) ? "h2" : editor.isActive("heading", { level: 3 }) ? "h3" : "p";

  const setBlock = (v: string) => (v === "p" ? c().setParagraph().run() : c().setHeading({ level: Number(v[1]) as 1 | 2 | 3 }).run());
  const setSize = (v: number | null) => (v ? c().setMark("textStyle", { fontSize: v }).run() : c().setMark("textStyle", { fontSize: null }).removeEmptyTextStyle().run());
  const setColor = (v: string | null) => (v ? c().setColor(v).run() : c().unsetColor().run());
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
  const setColumns = (n: number) => {
    if (inCols) return n === 1 ? c().lift("columns").run() : c().updateAttributes("columns", { count: n }).run();
    if (n > 1) c().wrapIn("columns", { count: n }).run();
  };
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
  const btn = (label: string, on: boolean, run: () => void, hint?: string) => (
    <button className={on ? "on" : ""} title={hint} onMouseDown={(e) => { e.preventDefault(); run(); }}>{label}</button>
  );
  const fig = A("figure") as { w?: number; h?: number; images?: { src: string; x: number; y: number; w: number; h: number }[] };

  return (
    <div className="edwrap">
      <div className="edmain">
        <div className="edbar">
          <input className="edtitle" value={title} aria-label="문서 제목" onChange={(e) => { setTitle(e.target.value); later(e.target.value); }} />
          <span className={`save ${status}`}>{{ saved: "저장됨", dirty: "수정됨", saving: "저장 중…", error: "저장 실패 — 다시 시도" }[status]}</span>
          {status === "error" && <button onClick={() => save(editor, title)}>다시 저장</button>}
        </div>
        {/* 1줄: 글자 */}
        <div className="edbar tools">
          <div className="seg">
            {btn("↶", false, () => c().undo().run(), "되돌리기")}
            {btn("↷", false, () => c().redo().run(), "다시 실행")}
          </div>
          <select value={blockType} onChange={(e) => setBlock(e.target.value)} aria-label="문단 종류">
            <option value="p">본문</option><option value="h1">제목 띠</option><option value="h2">섹션 띠</option><option value="h3">조 제목</option>
          </select>
          <select value={fontSize ?? ""} onChange={(e) => setSize(e.target.value ? Number(e.target.value) : null)} aria-label="글자 크기">
            <option value="">크기: 본문</option>
            {[...new Set([...SIZES, ...(fontSize ? [fontSize] : [])])].sort((a, b) => a - b).map((n) => <option key={n} value={n}>{n}pt</option>)}
          </select>
          <div className="seg">{btn("굵게", editor.isActive("bold"), () => c().toggleBold().run())}</div>
          <Swatches label="글자색" colors={COLORS} onPick={setColor} />
          <div className="seg">
            {btn("왼쪽", false, () => align("left"))}{btn("가운데", false, () => align("center"))}
            {btn("오른쪽", false, () => align("right"))}{btn("양쪽", false, () => align("justify"))}
          </div>
          <Swatches label={fillTarget ? `배경: ${fillTarget}` : "배경"} colors={FILLS} onPick={setFill} />
        </div>
        {/* 2줄: 넣기·배치 */}
        <div className="edbar tools">
          <span className="grp">
            <Num label="표" value={grid.rows} onSet={(v) => setGrid({ ...grid, rows: Math.max(1, v ?? 1) })} width={36} />
            <Num label="×" value={grid.cols} onSet={(v) => setGrid({ ...grid, cols: Math.max(1, v ?? 1) })} width={36} />
            <div className="seg">{btn("표 넣기", false, () => c().insertTable({ rows: grid.rows, cols: grid.cols, withHeaderRow: false }).run())}</div>
          </span>
          <div className="seg">
            {btn("박스로 감싸기", false, () => c().wrapIn("box", { border: true, round: false, pad: 7 }).run(), "선택한 문단들을 테두리 상자로 묶습니다")}
            {inBox && btn("박스 풀기", false, () => c().lift("box").run())}
          </div>
          <div className="seg">
            {btn("1단", !inCols, () => setColumns(1))}
            {btn("2단", inCols && A("columns").count === 2, () => setColumns(2))}
            {btn("3단", inCols && A("columns").count === 3, () => setColumns(3))}
          </div>
          <div className="seg">
            {btn("이미지", false, () => { asFigure.current = false; fileRef.current?.click(); }, "글줄 안에 이미지를 넣습니다")}
            {btn("캡션 그림", false, () => { asFigure.current = true; fileRef.current?.click(); }, "아래에 설명 글이 붙는 그림을 넣습니다")}
          </div>
          <input ref={fileRef} type="file" accept="image/*" hidden onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) upload(f); }} />
          <div className="seg">
            {btn("변수로 만들기", false, makeVariable, hasText ? "선택한 글자를 변수로 바꿉니다" : "먼저 글자를 선택하세요")}
            {btn("조 번호 다시 매기기", false, () => setNote(`조 제목 ${renumberArticles(editor)}개의 번호를 다시 매겼습니다. 본문 속 '제N조' 참조는 직접 확인하세요.`))}
          </div>
          <span className="grp">
            <Num label="본문 pt" value={paper.body_pt ?? 8.4} step={0.1} onSet={(v) => { if (v && v >= 3 && v <= 30) { setPaper({ ...paper, body_pt: v }); later(); } }} />
            <Num label="줄 간격" value={paper.line_height ?? 1.2} step={0.05} onSet={(v) => { if (v && v >= 0.9 && v <= 3) { setPaper({ ...paper, line_height: v }); later(); } }} />
          </span>
          <div className="seg">{btn("모듈·변수 표시", marks, () => setMarks(!marks), "끄면 인쇄될 모습으로 봅니다")}</div>
          <select className="zoom" value={zoom} onChange={(e) => setZoom(Number(e.target.value))} aria-label="확대">
            {[1, 1.25, 1.5, 2, 2.5].map((z) => <option key={z} value={z}>{z * 100}%</option>)}
          </select>
          <div className="seg">{btn("인쇄 · PDF", false, () => window.print())}</div>
        </div>
        {/* 3줄: 지금 선택한 대상의 디자인 */}
        {(inTable || inBox || inGallery || inFigure || isImage) && (
          <div className="edbar tools ctx">
            {inTable && (
              <>
                <b>표</b>
                <div className="seg">
                  {btn("행 추가", false, () => c().addRowAfter().run())}{btn("행 삭제", false, () => c().deleteRow().run())}
                  {btn("열 추가", false, () => c().addColumnAfter().run())}{btn("열 삭제", false, () => c().deleteColumn().run())}
                </div>
                <div className="seg">
                  {btn("칸 합치기", false, () => c().mergeCells().run(), "여러 칸을 끌어서 선택한 뒤 누르세요")}
                  {btn("칸 나누기", false, () => c().splitCell().run())}
                </div>
                <span className="grp">테두리
                  <div className="seg">
                    {btn("전체", false, () => c().setCellAttribute("ln", [1, 1, 1, 1]).run())}
                    {btn("가로만", false, () => c().setCellAttribute("ln", [1, 0, 1, 0]).run())}
                    {btn("세로만", false, () => c().setCellAttribute("ln", [0, 1, 0, 1]).run())}
                    {btn("없음", false, () => c().setCellAttribute("ln", [0, 0, 0, 0]).run())}
                  </div>
                </span>
                <Swatches label="선 색" colors={["#231f20", "#58595b", "#939598", "#ffffff"]} onPick={(v) => c().updateAttributes("table", { lineColor: v ?? "#231f20" }).run()} />
                <Num label="선 굵기" value={A("table").lineW} step={0.1} onSet={(v) => c().updateAttributes("table", { lineW: v ?? 0.5 }).run()} />
                <Num label="행 높이" value={A("tableRow").h} onSet={(v) => c().updateAttributes("tableRow", { h: v }).run()} />
                <Num label="표 너비 %" value={A("table").widthPct} onSet={(v) => c().updateAttributes("table", { widthPct: v && v < 100 ? v : null }).run()} />
                <select value={A("table").float ?? ""} onChange={(e) => c().updateAttributes("table", { float: e.target.value || null }).run()} aria-label="표 배치">
                  <option value="">글과 따로</option><option value="left">왼쪽에 두고 글이 옆으로</option><option value="right">오른쪽에 두고 글이 옆으로</option>
                </select>
                <div className="seg">{btn("표 삭제", false, () => c().deleteTable().run())}</div>
              </>
            )}
            {inBox && !inTable && (
              <>
                <b>박스</b>
                <div className="seg">
                  {btn("테두리", !!A("box").border, () => c().updateAttributes("box", { border: !A("box").border }).run())}
                  {btn("둥근 위 모서리", !!A("box").round, () => c().updateAttributes("box", { round: !A("box").round }).run())}
                  {btn("용지 끝까지", !!A("box").bleed, () => c().updateAttributes("box", { bleed: !A("box").bleed }).run())}
                </div>
                <Swatches label="테두리 색" colors={["#939598", "#231f20", "#58595b"]} onPick={(v) => c().updateAttributes("box", { borderColor: v ?? "#939598" }).run()} />
                <Num label="테두리 굵기" value={A("box").borderW} step={0.1} onSet={(v) => c().updateAttributes("box", { borderW: v ?? 0.5 }).run()} />
                <Num label="안쪽 여백" value={A("box").pad} onSet={(v) => c().updateAttributes("box", { pad: v ?? 0 }).run()} />
              </>
            )}
            {inGallery && (
              <>
                <b>사진 묶음</b>
                <Num label="너비 %" value={A("gallery").widthPct} onSet={(v) => c().updateAttributes("gallery", { widthPct: v && v < 100 ? v : null }).run()} />
                <select value={A("gallery").float ?? ""} onChange={(e) => c().updateAttributes("gallery", { float: e.target.value || null }).run()} aria-label="사진 묶음 배치">
                  <option value="">글과 따로</option><option value="left">왼쪽에 두고 글이 옆으로</option><option value="right">오른쪽에 두고 글이 옆으로</option>
                </select>
              </>
            )}
            {inFigure && fig.w && (
              <>
                <b>캡션 그림</b>
                <Num label="너비 pt" value={fig.w} onSet={(v) => { if (v && v > 4) c().updateAttributes("figure", { w: v, h: Math.round(((fig.h ?? v) * v) / (fig.w ?? v) * 10) / 10,
                  images: (fig.images ?? []).map((im) => ({ ...im, x: (im.x * v) / fig.w!, y: (im.y * v) / fig.w!, w: (im.w * v) / fig.w!, h: (im.h * v) / fig.w! })) }).run(); }} />
                <div className="seg">{btn("그림 삭제", false, () => c().deleteNode("figure").run())}</div>
              </>
            )}
            {isImage && (
              <>
                <b>이미지</b>
                <Num label="너비 pt" value={A("image").widthPt} onSet={(v) => c().updateAttributes("image", { widthPt: v }).run()} />
                <select value={A("image").float ?? ""} onChange={(e) => c().updateAttributes("image", { float: e.target.value || null }).run()} aria-label="이미지 배치">
                  <option value="">글줄 안</option><option value="left">왼쪽에 두고 글이 옆으로</option><option value="right">오른쪽에 두고 글이 옆으로</option>
                </select>
              </>
            )}
          </div>
        )}
        {note && <div className="note" onClick={() => setNote("")}>{note}</div>}
        <div className="edscroll">
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
  const [busy, setBusy] = useState(false);
  const [confirmDel, setConfirmDel] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => get<DraftRow[]>("/api/drafts").then(setDrafts).catch((e) => setError(String(e))), []);
  useEffect(() => { reload(); }, [reload]);
  useEffect(() => {
    if (!q.trim()) { setDocs([]); return; }
    const t = setTimeout(() => get<DocRow[]>(`/api/documents?q=${encodeURIComponent(q.trim())}`).then((r) => setDocs(r.slice(0, 15))).catch((e) => setError(String(e))), 250);
    return () => clearTimeout(t);
  }, [q]);

  const create = useCallback(async (documentId: number | null) => {
    setBusy(true);
    try {
      const d = await send<Draft>("POST", "/api/drafts", documentId === null ? {} : { document_id: documentId });
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
          <button className="wide" disabled={busy} onClick={() => create(null)}>빈 문서로 시작</button>
        </div>
        <ul>
          {docs.map((d) => (
            <li key={`doc${d.id}`} onClick={() => !busy && create(d.id)}>
              <div className="line1"><span className={`badge ${d.doc_type}`}>{d.doc_type === "terms" ? "약관" : "상품설명서"}</span><b>{d.product}</b></div>
              <div className="line2">{d.company} · {d.form_no} · 복제해서 편집 시작</div>
            </li>
          ))}
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
