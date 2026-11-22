import { useEffect, useState } from "react";
import { get } from "./api";
import type { Layout, LayoutItem } from "./types";

/** PDF 글꼴 이름 → 웹 글꼴. Source Han Sans KR 과 Noto Sans KR 은 같은 글꼴이다. */
function family(font: string): string {
  if (/nanum/i.test(font)) return "'Nanum Gothic', sans-serif";
  if (/applesdgothic/i.test(font)) return "'Apple SD Gothic Neo', 'Noto Sans KR', sans-serif";
  return "'Noto Sans KR', sans-serif";
}

function Item({ it }: { it: LayoutItem }) {
  if (it.k === "path") {
    return (
      <path d={it.d} fill={it.fill ?? "none"} fillOpacity={it.fo} fillRule={it.eo ? "evenodd" : undefined}
        stroke={it.stroke ?? "none"} strokeWidth={it.sw} strokeOpacity={it.so} />
    );
  }
  if (it.k === "image" && it.m) {
    // m 은 단위 정사각형(0..1)을 페이지 좌표로 옮기는 변환 행렬
    return (
      <image href={`/api/assets/${it.asset}.png`} width={1} height={1} preserveAspectRatio="none"
        transform={`matrix(${it.m.join(" ")})`} />
    );
  }
  if (it.k === "text" && it.spans) {
    const rot = it.dir ? (Math.atan2(it.dir[1], it.dir[0]) * 180) / Math.PI : 0;
    return (
      <>
        {it.spans.map((s, i) => {
          // 글자 폭을 원본 폭에 맞춰(textLength) 글꼴 차이로 인한 밀림을 없앤다
          const len = s.x1 - s.o[0];
          const fit = !rot && len > 0.5 && s.t.trim().length > 1;
          return (
            <text key={i} x={s.o[0]} y={s.o[1]} fontSize={s.size} fontWeight={s.w} fill={s.color}
              fontFamily={family(s.font)} textLength={fit ? len : undefined} lengthAdjust="spacing"
              transform={rot ? `rotate(${rot} ${s.o[0]} ${s.o[1]})` : undefined}>
              {s.t}
            </text>
          );
        })}
      </>
    );
  }
  return null;
}

/** 레이아웃 계층(DB)만으로 페이지를 다시 그린다. 원본 PDF 는 쓰지 않는다. */
export default function PageCanvas({ docId, pageNo }: { docId: number; pageNo: number }) {
  const [layout, setLayout] = useState<Layout | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setLayout(null);
    setError(null);
    get<Layout>(`/api/documents/${docId}/pages/${pageNo}/layout`).then(setLayout).catch((e) => setError(String(e)));
  }, [docId, pageNo]);
  if (error) return <div className="error">{error}</div>;
  if (!layout) return <div className="loading">불러오는 중…</div>;
  return (
    <svg className="page" viewBox={`0 0 ${layout.width} ${layout.height}`} xmlSpace="preserve">
      <rect width={layout.width} height={layout.height} fill="#fff" />
      {layout.items.map((it, i) => <Item key={i} it={it} />)}
    </svg>
  );
}
