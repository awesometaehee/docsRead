import { useEffect, useRef, useState } from "react";
import type { DocRow } from "./types";

type Item = { key: string; name: string; state: "wait" | "busy" | "ok" | "dup" | "fail"; msg?: string; docId?: number };
type Rebuild = { state: "idle" | "running" | "done" | "error"; message: string | null; stale: boolean; finished_at: string | null };

const STATE_LABEL: Record<Item["state"], string> = { wait: "대기", busy: "데이터화 중…", ok: "완료", dup: "이미 있음", fail: "실패" };

async function uploadOne(file: File): Promise<{ status: "ingested" | "duplicate"; document: DocRow }> {
  const res = await fetch(`/api/documents/upload?filename=${encodeURIComponent(file.name)}`, {
    method: "POST", headers: { "Content-Type": "application/pdf" }, body: file,
  });
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try { msg = (await res.json()).detail ?? msg; } catch { /* 본문이 JSON 이 아님 */ }
    throw new Error(msg);
  }
  return res.json();
}

/** PDF 올리기: 한 건씩 차례로 서버에 보내 데이터화하고, 끝나면 공통 모듈 재계산을 권한다. */
export default function UploadPanel({ onUploaded, onOpenDoc }: { onUploaded: () => void; onOpenDoc: (id: number) => void }) {
  const [items, setItems] = useState<Item[]>([]);
  const [drag, setDrag] = useState(false);
  const [rebuild, setRebuild] = useState<Rebuild | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const busy = items.some((i) => i.state === "wait" || i.state === "busy");

  const refreshRebuild = () => fetch("/api/module-rebuild").then((r) => r.json()).then(setRebuild).catch(() => {});
  useEffect(() => { refreshRebuild(); }, []);
  useEffect(() => {   // 계산 중이면 상태를 주기적으로 확인
    if (rebuild?.state !== "running") return;
    const t = setInterval(refreshRebuild, 2000);
    return () => clearInterval(t);
  }, [rebuild?.state]);

  const patch = (key: string, p: Partial<Item>) => setItems((cur) => cur.map((i) => (i.key === key ? { ...i, ...p } : i)));

  const start = async (files: File[]) => {
    const pdfs = files.filter((f) => /\.pdf$/i.test(f.name) || f.type === "application/pdf");
    const skipped = files.filter((f) => !pdfs.includes(f));
    const batch: Item[] = pdfs.map((f, n) => ({ key: `${Date.now()}-${n}-${f.name}`, name: f.name, state: "wait" }));
    setItems((cur) => [...skipped.map((f, n) => ({ key: `${Date.now()}-x${n}`, name: f.name, state: "fail" as const, msg: "PDF 가 아님" })), ...batch, ...cur]);
    for (let n = 0; n < pdfs.length; n++) {
      const it = batch[n];
      patch(it.key, { state: "busy" });
      try {
        const r = await uploadOne(pdfs[n]);
        patch(it.key, { state: r.status === "ingested" ? "ok" : "dup", docId: r.document.id,
          msg: `${r.document.page_count}쪽${r.status === "duplicate" ? " · 같은 내용의 문서가 이미 있습니다" : ""}` });
        onUploaded();
      } catch (e) {
        patch(it.key, { state: "fail", msg: e instanceof Error ? e.message : String(e) });
      }
    }
    refreshRebuild();
  };

  const runRebuild = () => fetch("/api/module-rebuild", { method: "POST" }).then((r) => r.json()).then(setRebuild);

  return (
    <div className={`upload${drag ? " drag" : ""}`}
      onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
      onDragLeave={() => setDrag(false)}
      onDrop={(e) => { e.preventDefault(); setDrag(false); start([...e.dataTransfer.files]); }}>
      <div className="upload-row">
        <button className="primary" onClick={() => input.current?.click()} disabled={busy}>PDF 올리기</button>
        <span>{busy ? "처리 중…" : "또는 여기로 끌어 놓기"}</span>
        <input ref={input} type="file" accept=".pdf,application/pdf" multiple hidden
          onChange={(e) => { start([...(e.target.files ?? [])]); e.target.value = ""; }} />
      </div>
      {items.length > 0 && (
        <ul className="upload-list">
          {items.map((i) => (
            <li key={i.key} className={i.state} title={i.msg}>
              <span className="st">{STATE_LABEL[i.state]}</span>
              {i.docId != null ? <a onClick={() => onOpenDoc(i.docId!)}>{i.name}</a> : <span className="nm">{i.name}</span>}
              {i.msg && <small>{i.msg}</small>}
            </li>
          ))}
          {!busy && <li className="clear"><a onClick={() => setItems([])}>목록 지우기</a></li>}
        </ul>
      )}
      {rebuild && (rebuild.stale || rebuild.state === "running" || rebuild.state === "error") && (
        <div className={`rebuild ${rebuild.state}`}>
          {rebuild.state === "running" ? <span>공통 모듈 다시 계산 중…</span>
            : <>
              <span>{rebuild.state === "error" ? `모듈 계산 실패: ${rebuild.message}` : "새 문서는 아직 공통 모듈에 반영되지 않았습니다"}</span>
              <button onClick={runRebuild} disabled={busy}>다시 계산</button>
            </>}
        </div>
      )}
    </div>
  );
}
