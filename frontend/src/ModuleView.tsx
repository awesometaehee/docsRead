import { useEffect, useState } from "react";
import { get } from "./api";
import type { ModuleDetail, ModuleRow, ModuleSummary, Variant } from "./types";

const KIND: Record<string, string> = { article: "조", section: "섹션", item: "항목", paragraph: "문단", footer: "꼬리말" };

/** 템플릿: 고정 문구 + 변수 칩. 칩을 누르면 그 변수가 문서들에서 가진 값을 보여 준다. */
function Template({ v, values, active, onPick }: { v: Variant; values: string[] | null; active: number | null; onPick: (k: number) => void }) {
  return (
    <p className="tpl">
      {v.parts.map((p, i) => {
        if ("t" in p) return <span key={i}>{p.t}</span>;
        const slot = v.slots[p.v];
        const val = values ? values[p.v] : slot.default;
        return (
          <button key={i} className={`chip ${active === p.v ? "on" : ""} ${val ? "" : "blank"}`} title={`${slot.name} · 값 ${slot.n_values}종`}
            onClick={() => onPick(p.v)}>
            {val || "∅"}
          </button>
        );
      })}
    </p>
  );
}

function VariantCard({ v, total, onOpenDoc }: { v: Variant; total: number; onOpenDoc: (id: number) => void }) {
  const [slot, setSlot] = useState<number | null>(null);
  const [occ, setOcc] = useState<number | null>(null);
  const [showDocs, setShowDocs] = useState(false);
  const current = occ !== null ? v.occurrences[occ] : null;
  return (
    <article className="variant">
      <header>
        <b>문안 {v.ordinal + 1}</b>
        <span>{v.n_docs}개 문서 ({Math.round((v.n_docs / total) * 100)}%)</span>
        <span>변수 {v.slots.length}개</span>
        <select value={occ ?? ""} onChange={(e) => setOcc(e.target.value === "" ? null : Number(e.target.value))} aria-label="미리보기 문서">
          <option value="">기본값으로 보기</option>
          {v.occurrences.map((o, i) => (
            <option key={i} value={i}>{o.company} · {o.product} {o.label ?? ""}</option>
          ))}
        </select>
        {current && <button className="link" onClick={() => onOpenDoc(current.doc_id)}>문서 열기</button>}
      </header>
      <Template v={v} values={current?.values ?? null} active={slot} onPick={(k) => setSlot(slot === k ? null : k)} />
      {slot !== null && (
        <div className="slotbox">
          <b>{v.slots[slot].name}</b>
          <span className="mute">문서에 따라 {v.slots[slot].n_values}가지 값</span>
          <table>
            <tbody>
              {v.slots[slot].top.map(([val, n], i) => (
                <tr key={i}><td>{val || <i className="mute">(없음)</i>}</td><td>{n}건</td></tr>
              ))}
              {v.slots[slot].n_values > v.slots[slot].top.length && (
                <tr><td className="mute" colSpan={2}>… 외 {v.slots[slot].n_values - v.slots[slot].top.length}가지</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
      <button className="link" onClick={() => setShowDocs(!showDocs)}>{showDocs ? "사용 문서 접기" : `사용 문서 ${v.n_occurrences}건 보기`}</button>
      {showDocs && (
        <div className="occwrap">
          <table className="occ">
            <thead>
              <tr><th>종류</th><th>회사</th><th>상품</th><th>위치</th>{v.slots.map((s, i) => <th key={i}>{s.name}</th>)}</tr>
            </thead>
            <tbody>
              {v.occurrences.map((o, i) => (
                <tr key={i} onClick={() => onOpenDoc(o.doc_id)}>
                  <td>{o.doc_type === "terms" ? "약관" : "상품설명서"}</td><td>{o.company}</td><td>{o.product}</td><td>{o.label ?? ""}</td>
                  {o.values.map((val, k) => <td key={k} className={val === v.slots[k].default ? "mute" : ""}>{val}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </article>
  );
}

export default function ModuleView({ initial, onOpenDoc }: { initial: number | null; onOpenDoc: (id: number) => void }) {
  const [rows, setRows] = useState<ModuleRow[]>([]);
  const [summary, setSummary] = useState<ModuleSummary | null>(null);
  const [kind, setKind] = useState("");
  const [q, setQ] = useState("");
  const [shared, setShared] = useState(true);
  const [sel, setSel] = useState<number | null>(initial);
  const [mod, setMod] = useState<ModuleDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { get<ModuleSummary>("/api/modules/summary").then(setSummary).catch((e) => setError(String(e))); }, []);
  useEffect(() => {
    const p = new URLSearchParams({ min_docs: shared ? "2" : "1" });
    if (kind) p.set("kind", kind);
    if (q.trim()) p.set("q", q.trim());
    const t = setTimeout(() => get<ModuleRow[]>(`/api/modules?${p}`).then(setRows).catch((e) => setError(String(e))), 200);
    return () => clearTimeout(t);
  }, [kind, q, shared]);
  useEffect(() => {
    if (sel === null) return;
    setMod(null);
    get<ModuleDetail>(`/api/modules/${sel}`).then(setMod).catch((e) => setError(String(e)));
  }, [sel]);

  return (
    <>
      <aside>
        <div className="filters">
          <select value={kind} onChange={(e) => setKind(e.target.value)} aria-label="모듈 종류">
            <option value="">전체 종류</option>
            {Object.entries(KIND).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <label className="check"><input type="checkbox" checked={shared} onChange={(e) => setShared(e.target.checked)} /> 2개 문서 이상</label>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="제목·문안 검색" aria-label="모듈 검색" />
        </div>
        <ul>
          {rows.map((m) => (
            <li key={m.id} className={m.id === sel ? "on" : ""} onClick={() => setSel(m.id)}>
              <div className="line1"><span className={`badge k-${m.kind}`}>{KIND[m.kind] ?? m.kind}</span><b>{m.title}</b></div>
              <div className="line2">문서 {m.n_docs} · 문안 {m.n_variants}종 · 변수 {m.n_slots}</div>
            </li>
          ))}
        </ul>
      </aside>
      <main>
        {error && <div className="error" onClick={() => setError(null)}>{error}</div>}
        {!mod && (
          <div className="empty">
            <h2>공통 모듈</h2>
            {summary && (
              <p>
                조·항목 {summary.units.toLocaleString()}개를 모듈 {summary.modules}개(문안 {summary.variants}종)로 묶었습니다.
                전체 글자의 {(summary.shared_char_ratio * 100).toFixed(1)}%가 2개 이상 문서에 공통인 모듈에 속합니다.
              </p>
            )}
            <p>왼쪽에서 모듈을 고르면 문안(템플릿)과 변수, 사용 문서를 볼 수 있습니다.</p>
          </div>
        )}
        {mod && (
          <div className="modwrap">
            <div className="modhead">
              <h2>{mod.title}</h2>
              <span>{KIND[mod.kind]} · 문서 {mod.n_docs}개 · 문안 {mod.variants.length}종 · {mod.doc_types.map((t) => (t === "terms" ? "약관" : "상품설명서")).join("·")}</span>
              <small>색 칩이 변수입니다. 칩을 누르면 문서별 값 분포를, 미리보기 문서를 고르면 그 문서의 값을 채워 보여 줍니다.</small>
            </div>
            {mod.variants.map((v) => <VariantCard key={v.id} v={v} total={mod.n_docs} onOpenDoc={onOpenDoc} />)}
          </div>
        )}
      </main>
    </>
  );
}
