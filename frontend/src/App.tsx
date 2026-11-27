import { useEffect, useMemo, useState } from "react";
import { get } from "./api";
import BlockView from "./BlockView";
import EditorView from "./EditorView";
import ModuleView from "./ModuleView";
import PageCanvas from "./PageCanvas";
import PageEditView from "./PageEditView";
import UploadPanel from "./UploadPanel";
import TooltipLayer from "./Tooltip";
import type { Block, BlockModules, DocDetail, DocRow } from "./types";

const TYPE_LABEL = { terms: "약관", brochure: "상품설명서" } as const;
type Pane = "original" | "layout" | "blocks";
const PANES: { key: Pane; label: string; hint: string }[] = [
  { key: "original", label: "원본 PDF", hint: "PDF 를 그대로 그린 그림" },
  { key: "layout", label: "데이터 재현", hint: "DB 의 글자·도형·이미지만으로 다시 그림" },
  { key: "blocks", label: "구조", hint: "조·문단·표·그림 단위(편집·모듈화의 입력)" },
];

export default function App() {
  const [docs, setDocs] = useState<DocRow[]>([]);
  const [type, setType] = useState("");
  const [company, setCompany] = useState("");
  const [q, setQ] = useState("");
  const [sel, setSel] = useState<number | null>(null);
  const [doc, setDoc] = useState<DocDetail | null>(null);
  const [blocks, setBlocks] = useState<Block[]>([]);
  const [page, setPage] = useState(0);
  const [panes, setPanes] = useState<Pane[]>(["original", "layout"]);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<"docs" | "modules" | "editor" | "pageedit">("docs");
  const [editFrom, setEditFrom] = useState<number | null>(null);
  const [pageEditFrom, setPageEditFrom] = useState<number | null>(null);
  const [blockModules, setBlockModules] = useState<BlockModules>({});
  const [moduleId, setModuleId] = useState<number | null>(null);
  const [reload, setReload] = useState(0);
  const openDoc = (id: number) => { setSel(id); setMode("docs"); };
  const openModule = (id: number) => { setModuleId(id); setMode("modules"); };

  useEffect(() => {
    const p = new URLSearchParams();
    if (type) p.set("doc_type", type);
    if (company) p.set("company", company);
    if (q.trim()) p.set("q", q.trim());
    const t = setTimeout(() => {
      get<DocRow[]>(`/api/documents?${p}`).then(setDocs).catch((e) => setError(String(e)));
    }, 200);
    return () => clearTimeout(t);
  }, [type, company, q, reload]);

  useEffect(() => {
    if (sel === null) return;
    setDoc(null);
    setPage(0);
    Promise.all([get<DocDetail>(`/api/documents/${sel}`), get<Block[]>(`/api/documents/${sel}/blocks`),
      get<BlockModules>(`/api/documents/${sel}/modules`)])
      .then(([d, b, m]) => { setDoc(d); setBlocks(b); setBlockModules(m); })
      .catch((e) => setError(String(e)));
  }, [sel]);

  // 모든 쪽을 이어서 보여 주고, 쪽 버튼은 각 보기 창을 해당 쪽으로 스크롤한다
  const goPage = (n: number) => {
    setPage(n);
    document.querySelectorAll(`.sheet[data-page="${n}"]`).forEach((el) => el.scrollIntoView({ behavior: "smooth", block: "start" }));
  };

  const companies = useMemo(() => ["개발", "라이프", "애니콜", "리더스", "실로암", "플러스"], []);
  const toggle = (k: Pane) =>
    setPanes((cur) => (cur.includes(k) ? (cur.length > 1 ? cur.filter((x) => x !== k) : cur) : PANES.map((p) => p.key).filter((x) => x === k || cur.includes(x))));

  return (
    <div className="shell">
    <TooltipLayer />
    <nav className="noprint">
      <strong>docsRead</strong>
      <div className="seg">
        <button className={mode === "docs" ? "on" : ""} onClick={() => setMode("docs")}>문서</button>
        <button className={mode === "modules" ? "on" : ""} onClick={() => setMode("modules")}>공통 모듈</button>
        <button className={mode === "editor" ? "on" : ""} onClick={() => setMode("editor")}>편집기</button>
        <button className={mode === "pageedit" ? "on" : ""} onClick={() => setMode("pageedit")} title="상품설명서: 원본 PDF 쪽 위에서 글자·그림만 고칩니다">쪽 편집</button>
      </div>
      {mode === "docs" && <span>{docs.length}건</span>}
    </nav>
    <div className="app">
      {mode === "modules" && <ModuleView key={moduleId ?? "none"} initial={moduleId} onOpenDoc={openDoc} />}
      {mode === "editor" && <EditorView startFrom={editFrom} onStarted={() => setEditFrom(null)} />}
      {mode === "pageedit" && <PageEditView startFrom={pageEditFrom} onStarted={() => setPageEditFrom(null)} />}
      {mode === "docs" && <>
      <aside>
        <div className="filters">
          <select value={type} onChange={(e) => setType(e.target.value)} aria-label="문서 종류">
            <option value="">전체 종류</option>
            <option value="terms">약관</option>
            <option value="brochure">상품설명서</option>
          </select>
          <select value={company} onChange={(e) => setCompany(e.target.value)} aria-label="회사">
            <option value="">전체 회사</option>
            {companies.map((c) => <option key={c}>{c}</option>)}
          </select>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="파일명·본문 검색" aria-label="검색" />
        </div>
        <UploadPanel onUploaded={() => setReload((n) => n + 1)} onOpenDoc={openDoc} />
        <ul>
          {docs.map((d) => (
            <li key={d.id} className={d.id === sel ? "on" : ""} onClick={() => setSel(d.id)}>
              <div className="line1">
                <span className={`badge ${d.doc_type}`}>{TYPE_LABEL[d.doc_type]}</span>
                <b>{d.product ?? d.filename}</b>
              </div>
              <div className="line2">
                {d.company} · {d.form_no} · {d.valid_from ?? "?"}~{d.valid_to ?? "현재"} · 개정 {d.revised_at ?? "?"}
              </div>
            </li>
          ))}
        </ul>
      </aside>
      <main>
        {error && <div className="error" onClick={() => setError(null)}>{error}</div>}
        {!doc && !error && (
          <div className="empty">
            <h2>문서를 선택하세요</h2>
            <p>왼쪽 목록에서 문서를 고르면 원본 PDF, DB 데이터로 다시 그린 화면, 구조화 결과를 나란히 비교할 수 있습니다.</p>
          </div>
        )}
        {doc && (
          <>
            <div className="toolbar">
              <div className="title">
                <b>{doc.product}</b>
                <span>{doc.filename}</span>
              </div>
              <div className="stat">
                글자 {doc.stats.chars.toLocaleString()} · 표 {doc.stats.tables} · 이미지 {doc.stats.images}
                {doc.doc_type === "terms" && ` · 조 ${doc.stats.articles}`}
              </div>
              {doc.doc_type === "brochure" ? (
                <button className="primary" onClick={() => { setPageEditFrom(doc.id); setMode("pageedit"); }} title="원본 PDF 쪽 위에서 글자·그림만 고칩니다(원본은 그대로, 글꼴·크기·장평·자간 유지)">
                  원본 위에서 고치기
                </button>
              ) : (
                <button className="primary" onClick={() => { setEditFrom(doc.id); setMode("editor"); }} title="이 문서를 복제해 편집기로 엽니다(원본은 그대로)">
                  이 문서로 편집 시작
                </button>
              )}
              <div className="seg">
                {PANES.map((p) => (
                  <button key={p.key} className={panes.includes(p.key) ? "on" : ""} title={p.hint} onClick={() => toggle(p.key)}>
                    {p.label}
                  </button>
                ))}
              </div>
              {doc.pages.length > 1 && (
                <div className="seg">
                  {doc.pages.map((p) => (
                    <button key={p.page_no} className={p.page_no === page ? "on" : ""} title="해당 쪽으로 이동" onClick={() => goPage(p.page_no)}>
                      {p.page_no + 1}쪽
                    </button>
                  ))}
                </div>
              )}
            </div>
            <div className="panes" style={{ gridTemplateColumns: `repeat(${panes.length}, minmax(0, 1fr))` }}>
              {panes.map((k) => (
                <section key={k} className="pane">
                  <h4>{PANES.find((p) => p.key === k)!.label}<small>{PANES.find((p) => p.key === k)!.hint}</small></h4>
                  <div className="scroll">
                    {doc.pages.map((p) => (
                      <div key={p.page_no} className="sheet" data-page={p.page_no}>
                        {doc.pages.length > 1 && <div className="pageno">{p.page_no + 1} / {doc.pages.length}쪽</div>}
                        {k === "original" && <img className="page" src={`/api/documents/${doc.id}/pages/${p.page_no}/original.png?dpi=160`} alt={`원본 ${p.page_no + 1}쪽`} />}
                        {k === "layout" && <PageCanvas docId={doc.id} pageNo={p.page_no} />}
                        {k === "blocks" && <BlockView blocks={blocks} page={p.page_no} modules={blockModules} onOpenModule={openModule} />}
                      </div>
                    ))}
                  </div>
                </section>
              ))}
            </div>
          </>
        )}
      </main>
      </>}
    </div>
    </div>
  );
}
