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

function DraftEditor({ draft, onSaved }: { draft: Draft; onSaved: (row: DraftRow) => void }) {
  const [, setTick] = useState(0);
  const [status, setStatus] = useState<"saved" | "dirty" | "saving" | "error">("saved");
  const [title, setTitle] = useState(draft.title);
  const [tab, setTab] = useState<"vars" | "module">("vars");
  const [note, setNote] = useState("");
  const [zoom, setZoom] = useState(draft.doc_type === "brochure" ? 1.5 : 1.25);
  const [marks, setMarks] = useState(true);
  const st = draft.style ?? {};
  const paperVars = {
    "--body": `${st.body_pt ?? 8.4}pt`, "--lh": String(st.line_height ?? 1.2), "--pw": `${st.page_w ?? 595.3}pt`,
    "--ph": `${st.page_h ?? 841.9}pt`, "--pad": `${st.pad ?? 14.2}pt`, zoom,
  } as React.CSSProperties;
  const timer = useRef<ReturnType<typeof setTimeout>>();

  const save = useCallback(async (ed: Editor, t: string) => {
    setStatus("saving");
    try {
      onSaved(await send<DraftRow>("PUT", `/api/drafts/${draft.id}`, { title: t, content: ed.getJSON() }));
      setStatus("saved");
    } catch { setStatus("error"); }
  }, [draft.id, onSaved]);

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
  const titleRef = useRef(title);
  titleRef.current = title;
  useEffect(() => () => clearTimeout(timer.current), []);

  if (!editor) return null;
  const sel = editor.state.selection as unknown as { node?: { type: { name: string }; attrs: { name: string } } };
  const selectedVar = sel.node?.type.name === "variable" ? sel.node.attrs.name : null;
  const hasText = !editor.state.selection.empty && !selectedVar;

  const makeVariable = () => {
    const { from, to } = editor.state.selection;
    const value = editor.state.doc.textBetween(from, to, " ");
    if (!value.trim()) return;
    const name = `변수${listVariables(editor).length + 1}`;
    editor.chain().focus().insertContentAt({ from, to }, { type: "variable", attrs: { name, value, options: [value], vtype: "문구" } }).run();
    setTab("vars");
  };
  const btn = (label: string, on: boolean, run: () => void, hint?: string) => (
    <button className={on ? "on" : ""} title={hint} onMouseDown={(e) => { e.preventDefault(); run(); }}>{label}</button>
  );

  return (
    <div className="edwrap">
      <div className="edmain">
        <div className="edbar">
          <input className="edtitle" value={title} aria-label="문서 제목"
            onChange={(e) => { setTitle(e.target.value); setStatus("dirty"); clearTimeout(timer.current); timer.current = setTimeout(() => save(editor, e.target.value), 1200); }} />
          <span className={`save ${status}`}>{{ saved: "저장됨", dirty: "수정됨", saving: "저장 중…", error: "저장 실패 — 다시 시도" }[status]}</span>
          {status === "error" && <button onClick={() => save(editor, title)}>다시 저장</button>}
        </div>
        <div className="edbar tools">
          <div className="seg">
            {btn("↶", false, () => editor.chain().focus().undo().run(), "되돌리기")}
            {btn("↷", false, () => editor.chain().focus().redo().run(), "다시 실행")}
          </div>
          <div className="seg">
            {btn("굵게", editor.isActive("bold"), () => editor.chain().focus().toggleBold().run())}
            {btn("붉은 글씨", editor.isActive("textStyle", { color: "#ed1c24" }), () =>
              editor.isActive("textStyle", { color: "#ed1c24" }) ? editor.chain().focus().unsetColor().run() : editor.chain().focus().setColor("#ed1c24").run(), "표준약관과 다른 부분 표시")}
          </div>
          <div className="seg">
            {btn("섹션 제목", editor.isActive("heading", { level: 2 }), () => editor.chain().focus().toggleHeading({ level: 2 }).run())}
            {btn("조 제목", editor.isActive("heading", { level: 3 }), () => editor.chain().focus().toggleHeading({ level: 3 }).run())}
            {btn("본문", editor.isActive("paragraph"), () => editor.chain().focus().setParagraph().run())}
          </div>
          <div className="seg">
            {btn("변수로 만들기", false, makeVariable, hasText ? "선택한 글자를 변수로 바꿉니다" : "먼저 글자를 선택하세요")}
            {btn("조 번호 다시 매기기", false, () => setNote(`조 제목 ${renumberArticles(editor)}개의 번호를 다시 매겼습니다. 본문 속 '제N조' 참조는 직접 확인하세요.`))}
          </div>
          {editor.isActive("table") && (
            <div className="seg">
              {btn("행 추가", false, () => editor.chain().focus().addRowAfter().run())}
              {btn("행 삭제", false, () => editor.chain().focus().deleteRow().run())}
              {btn("열 추가", false, () => editor.chain().focus().addColumnAfter().run())}
              {btn("열 삭제", false, () => editor.chain().focus().deleteColumn().run())}
            </div>
          )}
          <div className="seg">
            {btn("모듈·변수 표시", marks, () => setMarks(!marks), "끄면 원본과 같은 모습으로 봅니다")}
          </div>
          <select className="zoom" value={zoom} onChange={(e) => setZoom(Number(e.target.value))} aria-label="확대">
            {[1, 1.25, 1.5, 2, 2.5].map((z) => <option key={z} value={z}>{z * 100}%</option>)}
          </select>
          <div className="seg">{btn("인쇄 · PDF", false, () => window.print())}</div>
        </div>
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
