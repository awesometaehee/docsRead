import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { get } from "./api";

/** 원본 PDF 쪽 위에서 글자·그림을 고친다(상품설명서). 화면의 쪽 그림은 서버가 고친 PDF 를 그린 것이라 내려받는 결과와 같다. */

type Align = "left" | "center" | "right";
interface PSpan { id: number; row: number; t: string; b: [number, number, number, number]; font: string; size: number; color: string; k: number; track: number; editable: boolean; subst: boolean; align: Align }
interface PImage { xref: number; b: [number, number, number, number]; w: number; h: number }
interface PPage { width: number; height: number; spans: PSpan[]; images: PImage[] }
interface TextEdit { page: number; span: number; text: string; was?: string; align?: Align; row?: number }
interface RowOp { page: number; op: "dup" | "del"; id: number; y0: number; y1: number; x0: number; x1: number; ya: number; yb: number; scope: string }
interface ImageEdit { page: number; image: number; asset: string }
type Edit = TextEdit | ImageEdit | RowOp;
interface Note { page: number; span?: number; row?: number; image?: number; level: "info" | "warn" | "error"; msg: string }
interface Row { id: number; document_id: number; title: string; n_edits: number; updated_at: string }
interface Detail extends Row { edits: Edit[]; pages: PPage[]; report: Note[]; doc: { id: number; product: string | null; filename: string } }

const isText = (e: Edit): e is TextEdit => "span" in e;
const isImage = (e: Edit): e is ImageEdit => "image" in e;
const isRow = (e: Edit): e is RowOp => "op" in e;
const WEIGHT: Record<string, string> = { Light: "가늘게", Normal: "보통", Regular: "보통", Medium: "중간", Bold: "굵게", Heavy: "아주 굵게" };

async function send<T>(method: string, url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, { method, headers: body ? { "Content-Type": "application/json" } : undefined, body: body ? JSON.stringify(body) : undefined });
  if (!res.ok) throw new Error(`${res.status} ${(await res.json().catch(() => ({}))).detail ?? res.statusText}`);
  return res.json() as Promise<T>;
}

export default function PageEditView({ startFrom, onStarted }: { startFrom: number | null; onStarted: () => void }) {
  const [rows, setRows] = useState<Row[]>([]);
  const [cur, setCur] = useState<Detail | null>(null);
  const [fonts, setFonts] = useState(true);
  const [ver, setVer] = useState(0);            // 쪽 그림 새로 받기
  const [zoom, setZoom] = useState(1.6);        // 화면 배율(pt → px)
  const [sel, setSel] = useState<{ page: number; span: number; row: number } | null>(null);
  const [draft, setDraft] = useState("");
  const [align, setAlign] = useState<Align>("left");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showBoxes, setShowBoxes] = useState(true);
  const fileRef = useRef<HTMLInputElement>(null);
  const imgTarget = useRef<{ page: number; xref: number } | null>(null);

  const refresh = useCallback(() => get<Row[]>("/api/pdfedits").then(setRows).catch((e) => setError(String(e))), []);
  const open = useCallback((id: number) => {
    setSel(null);
    get<Detail>(`/api/pdfedits/${id}`).then((d) => { setCur(d); setVer((v) => v + 1); }).catch((e) => setError(String(e)));
  }, []);

  useEffect(() => { refresh(); get<{ ready: boolean }>("/api/pdfedits/fonts").then((f) => setFonts(f.ready)).catch(() => {}); }, [refresh]);
  useEffect(() => {
    if (startFrom === null) return;
    onStarted();
    send<Row>("POST", "/api/pdfedits", { document_id: startFrom }).then((r) => { refresh(); open(r.id); }).catch((e) => setError(String(e)));
  }, [startFrom, onStarted, refresh, open]);

  const save = useCallback(async (edits: Edit[]) => {
    if (!cur) return;
    setBusy(true);
    try {
      const r = await send<Detail>("PUT", `/api/pdfedits/${cur.id}`, { edits });
      setCur({ ...cur, ...r });
      setVer((v) => v + 1);
      refresh();
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }, [cur, refresh]);

  const editOf = useCallback((page: number, span: number, row: number) =>
    cur?.edits.find((e): e is TextEdit => isText(e) && e.page === page && e.span === span && (e.row ?? 0) === row), [cur]);

  const rowOp = async (op: "dup" | "del") => {
    if (!cur || !sel) return;
    setBusy(true);
    setSel(null);
    try {
      const r = await send<Detail>("POST", `/api/pdfedits/${cur.id}/rows`, { page: sel.page, span: sel.span, row: sel.row, op });
      setCur({ ...cur, ...r });
      setVer((v) => v + 1);
      refresh();
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  };
  const restoreRow = (id: number) => cur && save(cur.edits.filter((e) => !(isRow(e) && e.id === id)));

  const pick = (page: number, sp: PSpan) => {
    if (!sp.editable || busy) return;
    const e = editOf(page, sp.id, sp.row);
    setSel({ page, span: sp.id, row: sp.row });
    setDraft(e ? e.text : sp.t);
    setAlign(e?.align ?? sp.align);
  };

  const selSpan = useMemo(() => (sel && cur ? cur.pages[sel.page].spans.find((s) => s.id === sel.span && s.row === sel.row) ?? null : null), [sel, cur]);

  const commit = (nextAlign: Align = align) => {
    if (!cur || !sel || !selSpan) return;
    const rest = cur.edits.filter((e) => !(isText(e) && e.page === sel.page && e.span === sel.span && (e.row ?? 0) === sel.row));
    const same = draft === selSpan.t;
    setSel(null);
    if (same && !editOf(sel.page, sel.span, sel.row)) return;
    save(same ? rest : [...rest, { page: sel.page, span: sel.span, text: draft, was: selSpan.t, ...(sel.row ? { row: sel.row } : {}), ...(nextAlign !== selSpan.align ? { align: nextAlign } : {}) }]);
  };
  const revert = () => {
    if (!cur || !sel) return;
    const rest = cur.edits.filter((e) => !(isText(e) && e.page === sel.page && e.span === sel.span && (e.row ?? 0) === sel.row));
    setSel(null);
    if (rest.length !== cur.edits.length) save(rest);
  };

  const pickImage = (page: number, xref: number) => { imgTarget.current = { page, xref }; fileRef.current?.click(); };
  const onFile = async (f: File | undefined) => {
    const t = imgTarget.current;
    if (!f || !t || !cur) return;
    setBusy(true);
    try {
      const res = await fetch("/api/assets", { method: "POST", body: f });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail ?? "그림을 올리지 못했습니다");
      const { src } = (await res.json()) as { src: string };
      const asset = src.split("/").pop()!.replace(".png", "");
      await save([...cur.edits.filter((e) => !(isImage(e) && e.page === t.page && e.image === t.xref)), { page: t.page, image: t.xref, asset }]);
    } catch (e) { setError(String(e)); setBusy(false); }
    if (fileRef.current) fileRef.current.value = "";
  };
  const revertImage = (page: number, xref: number) =>
    cur && save(cur.edits.filter((e) => !(isImage(e) && e.page === page && e.image === xref)));

  const remove = async (id: number) => {
    if (!window.confirm("이 쪽 편집 문서를 지울까요? 원본 PDF 는 그대로 남습니다.")) return;
    await send("DELETE", `/api/pdfedits/${id}`).catch((e) => setError(String(e)));
    if (cur?.id === id) setCur(null);
    refresh();
  };

  const noteOf = (page: number, span: number, row: number) => cur?.report.find((n) => n.page === page && n.span === span && (n.row ?? 0) === row && n.level !== "info");
  const deleted = cur?.edits.filter((e): e is RowOp => isRow(e) && e.op === "del") ?? [];

  return (
    <>
      <aside className="pe-side">
        <div className="pe-help">
          <b>쪽 위에서 고치기</b>
          <p>원본 PDF 를 다시 짜지 않고 글자·그림만 바꿉니다. 글꼴·크기·장평·자간·색은 원본에서 잰 값을 그대로 씁니다.</p>
          <p>문서 탭에서 상품설명서를 고른 뒤 <b>원본 위에서 고치기</b>를 누르면 새로 시작합니다.</p>
        </div>
        {!fonts && <div className="error">다시 쓰기용 글꼴이 준비되지 않았습니다. README 의 "원본 PDF 직접 수정용 글꼴"을 따라 준비하세요.</div>}
        <ul>
          {rows.map((r) => (
            <li key={r.id} className={cur?.id === r.id ? "on" : ""} onClick={() => open(r.id)}>
              <div className="line1"><b>{r.title}</b></div>
              <div className="line2">고친 곳 {r.n_edits} · {new Date(r.updated_at).toLocaleString()}
                <button className="link" onClick={(ev) => { ev.stopPropagation(); remove(r.id); }}>지우기</button></div>
            </li>
          ))}
          {rows.length === 0 && <li className="sep">아직 쪽 편집 문서가 없습니다.</li>}
        </ul>
        {cur && deleted.length > 0 && (
          <div className="pe-notes">
            <b>지운 행</b>
            {deleted.map((o) => (
              <div key={o.id} className="pe-note">{o.page + 1}쪽 · 위에서 {Math.round(o.y0)}pt 지점의 행
                <button className="link" onClick={() => restoreRow(o.id)}>되살리기</button></div>
            ))}
          </div>
        )}
        {cur && cur.report.length > 0 && (
          <div className="pe-notes">
            <b>알림</b>
            {cur.report.map((n, i) => (
              <div key={i} className={`pe-note ${n.level}`} onClick={() => { const sp = n.span !== undefined && cur.pages[n.page].spans.find((s) => s.id === n.span && s.row === (n.row ?? 0)); if (sp) pick(n.page, sp); }}>
                {n.page + 1}쪽 · {n.msg}
              </div>
            ))}
          </div>
        )}
      </aside>
      <main className="pe-main">
        {error && <div className="error" onClick={() => setError(null)}>{error}</div>}
        {!cur && <div className="empty"><h2>쪽 편집 문서를 고르세요</h2><p>왼쪽 목록에서 고르거나, 문서 탭의 상품설명서에서 새로 시작합니다.</p></div>}
        {cur && (
          <>
            <div className="toolbar">
              <div className="title"><b>{cur.title}</b><span>{cur.doc.filename}</span></div>
              <div className="stat">{busy ? "반영 중…" : `고친 곳 ${cur.edits.length} · 저장됨`}</div>
              <label className="pe-check"><input type="checkbox" checked={showBoxes} onChange={(e) => setShowBoxes(e.target.checked)} />고칠 수 있는 곳 표시</label>
              <div className="seg">
                {[1.2, 1.6, 2.2, 3].map((z) => <button key={z} className={zoom === z ? "on" : ""} onClick={() => setZoom(z)}>{Math.round(z / 1.6 * 100)}%</button>)}
              </div>
              <a className="primary pe-dl" href={`/api/pdfedits/${cur.id}/download.pdf`} title="고친 내용을 입힌 PDF 를 내려받습니다">PDF 내려받기</a>
            </div>
            <div className="pe-scroll" onMouseDown={(e) => { if (e.target === e.currentTarget) setSel(null); }}>
              {cur.pages.map((p, pno) => (
                <div key={pno} className={`pe-page${showBoxes ? " boxes" : ""}`} style={{ width: p.width * zoom, height: p.height * zoom }}>
                  <img src={`/api/pdfedits/${cur.id}/pages/${pno}/preview.png?dpi=${zoom > 2 ? 300 : 220}&v=${ver}`} alt={`${pno + 1}쪽`} draggable={false} />
                  {p.images.map((im, i) => {
                    const changed = cur.edits.some((e) => isImage(e) && e.page === pno && e.image === im.xref);
                    return (
                      <div key={`i${i}`} className={`pe-img${changed ? " changed" : ""}`}
                        style={{ left: im.b[0] * zoom, top: im.b[1] * zoom, width: (im.b[2] - im.b[0]) * zoom, height: (im.b[3] - im.b[1]) * zoom }}>
                        <span className="pe-imgbar">
                          <button onClick={() => pickImage(pno, im.xref)} title="같은 자리·같은 크기로 그림을 바꿉니다(비율이 다르면 가운데를 잘라 맞춤)">그림 바꾸기</button>
                          {changed && <button onClick={() => revertImage(pno, im.xref)}>원래대로</button>}
                        </span>
                      </div>
                    );
                  })}
                  {p.spans.map((sp) => {
                    const e = editOf(pno, sp.id, sp.row);
                    const note = noteOf(pno, sp.id, sp.row);
                    const on = sel?.page === pno && sel.span === sp.id && sel.row === sp.row;
                    return (
                      <div key={`${sp.row}:${sp.id}`} className={`pe-span${sp.editable ? "" : " locked"}${e || sp.row ? " changed" : ""}${note ? ` ${note.level}` : ""}${on ? " on" : ""}`}
                        title={note?.msg ?? (sp.editable ? undefined : "고칠 수 없는 글자(세로쓰기)")}
                        style={{ left: sp.b[0] * zoom, top: sp.b[1] * zoom, width: (sp.b[2] - sp.b[0]) * zoom, height: (sp.b[3] - sp.b[1]) * zoom }}
                        onMouseDown={(ev) => { ev.preventDefault(); pick(pno, sp); }} />
                    );
                  })}
                  {sel?.page === pno && selSpan && (
                    <div className="pe-pop" style={{ left: Math.max(0, Math.min(selSpan.b[0] * zoom, p.width * zoom - 380)), top: selSpan.b[3] * zoom + 4 }}>
                      <input autoFocus value={draft} onChange={(e) => setDraft(e.target.value)}
                        onKeyDown={(e) => { if (e.key === "Enter") commit(); if (e.key === "Escape") setSel(null); }} />
                      <div className="pe-row">
                        <div className="seg">
                          {(["left", "center", "right"] as Align[]).map((a) => (
                            <button key={a} className={align === a ? "on" : ""} onClick={() => setAlign(a)}
                              title="길이가 달라질 때 어느 쪽을 고정할지">{a === "left" ? "왼쪽 고정" : a === "center" ? "가운데 고정" : "오른쪽 고정"}</button>
                          ))}
                        </div>
                        <button className="primary" onClick={() => commit()}>적용</button>
                        <button onClick={revert} disabled={!editOf(pno, selSpan.id, selSpan.row)}>원래대로</button>
                        <button onClick={() => setSel(null)}>닫기</button>
                      </div>
                      <div className="pe-row">
                        <span className="pe-lab">표 행</span>
                        <button onClick={() => rowOp("dup")} title="이 행을 복제해 바로 아래에 끼웁니다. 선·배경은 그대로, 글자는 눌러서 고칩니다">아래에 행 추가</button>
                        <button onClick={() => rowOp("del")} title={selSpan.row ? "추가했던 이 행을 없앱니다" : "이 행을 빼고 아래 내용을 올립니다(왼쪽 목록에서 되살릴 수 있음)"}>이 행 삭제</button>
                      </div>
                      <div className="pe-info">
                        원본 값: {selSpan.subst ? `${selSpan.font} → 본고딕 대체` : `본고딕 ${WEIGHT[selSpan.font.split("-").pop() ?? ""] ?? ""}`} · {selSpan.size}pt ·
                        장평 {Math.round(selSpan.k * 100)}% · 자간 {Math.round(selSpan.track * 1000)} · <i style={{ background: selSpan.color }} /> {selSpan.color}
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
            <input ref={fileRef} type="file" accept="image/*" hidden onChange={(e) => onFile(e.target.files?.[0])} />
          </>
        )}
      </main>
    </>
  );
}
