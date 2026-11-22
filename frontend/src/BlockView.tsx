import type { Block, BlockModules, Cell, Run } from "./types";

type Ctx = { modules: BlockModules; onOpenModule: (id: number) => void };

/** 이 블록이 속한 공통 모듈 표시. 누르면 모듈 화면으로 이동한다. */
function Badge({ seq, ctx }: { seq: number; ctx: Ctx }) {
  const m = ctx.modules[String(seq)];
  if (!m) return null;
  return (
    <button className={`mod ${m.n_docs < 2 ? "solo" : ""}`} title={`공통 모듈: ${m.title}`} onClick={() => ctx.onOpenModule(m.module_id)}>
      {m.n_docs < 2 ? "이 문서 전용" : `모듈 · ${m.n_docs}개 문서 공통${m.n_variants > 1 ? ` · 문안 ${m.variant + 1}/${m.n_variants}` : ""}`}
    </button>
  );
}

function Runs({ runs }: { runs: Run[] }) {
  return (
    <>
      {runs.map((r, i) => (
        <span key={i} style={{ fontWeight: r.b ? 700 : undefined, color: r.c, whiteSpace: "pre-wrap" }}>{r.t}</span>
      ))}
    </>
  );
}

function Table({ b }: { b: Block }) {
  const { rows = 0, col_w = [], cells = [] } = b.content;
  const total = col_w.reduce((a, v) => a + v, 0) || 1;
  const byRow: Cell[][] = Array.from({ length: rows }, () => []);
  cells.forEach((c) => byRow[c.r]?.push(c));
  return (
    <table className="tbl">
      <colgroup>{col_w.map((w, i) => <col key={i} style={{ width: `${(w / total) * 100}%` }} />)}</colgroup>
      <tbody>
        {byRow.map((row, r) => (
          <tr key={r}>
            {row.sort((a, c) => a.c - c.c).map((c) => (
              <td key={c.c} rowSpan={c.rs} colSpan={c.cs}
                style={{ background: c.fill, textAlign: c.align as "left" | "center" }}>
                <Runs runs={c.runs} />
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Node({ b, kids, ctx }: { b: Block; kids: Map<number, Block[]>; ctx: Ctx }) {
  const children = (kids.get(b.seq) ?? []).map((c) => <Node key={c.seq} b={c} kids={kids} ctx={ctx} />);
  switch (b.kind) {
    case "title":
      return <section><h1>{b.title}</h1>{children}</section>;
    case "section":
      return <section className="sec"><h2>{b.title}</h2><Badge seq={b.seq} ctx={ctx} />{children}</section>;
    case "article":
      return (
        <section className="art">
          <h3><span className="tag">{b.label}</span>{b.title && ` (${b.title})`}
            {!!b.content.clauses?.length && <em>{b.content.clauses.length}개 항</em>}
            <Badge seq={b.seq} ctx={ctx} /></h3>
          {children}
        </section>
      );
    case "paragraph":
      return <p><Runs runs={b.content.runs ?? []} /><Badge seq={b.seq} ctx={ctx} /></p>;
    case "table":
      return <Table b={b} />;
    case "figure":
      return (
        <figure>
          <img src={`/api/assets/${b.content.asset}.png`} alt={b.content.caption ?? ""} loading="lazy" />
          {b.content.caption && <figcaption>{b.content.caption}</figcaption>}
        </figure>
      );
    case "footer":
      return <footer><Runs runs={b.content.runs ?? []} /></footer>;
    default:
      return null;
  }
}

/** 구조 계층(조·문단·표·그림)을 흐름 문서로 보여 준다. 이후 편집기의 입력이 되는 모델이다. */
export default function BlockView({ blocks, page, modules, onOpenModule }: { blocks: Block[]; page: number } & Ctx) {
  const ctx = { modules, onOpenModule };
  const kids = new Map<number, Block[]>();
  blocks.forEach((b) => {
    if (b.parent !== null) kids.set(b.parent, [...(kids.get(b.parent) ?? []), b]);
  });
  const roots = blocks.filter((b) => b.parent === null && b.page === page);
  const figures = roots.filter((b) => b.kind === "figure");
  return (
    <div className="blocks">
      {roots.filter((b) => b.kind !== "figure").map((b) => <Node key={b.seq} b={b} kids={kids} ctx={ctx} />)}
      {figures.length > 0 && <div className="figs">{figures.map((b) => <Node key={b.seq} b={b} kids={kids} ctx={ctx} />)}</div>}
    </div>
  );
}
