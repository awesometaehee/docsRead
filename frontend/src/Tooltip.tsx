import { useEffect, useLayoutEffect, useRef, useState } from "react";

interface Shown { title: string; desc: string; keys: string | undefined; rect: DOMRect }

/**
 * 말풍선 하나를 화면 전체에서 같이 쓴다: data-tip 이 있는 요소 위에 잠시 머물면 그 아래(자리가 없으면 위)에 띄운다.
 * 누르거나 키를 치거나 스크롤하면 바로 닫는다.
 */
export default function TooltipLayer() {
  const [shown, setShown] = useState<Shown | null>(null);
  const [pos, setPos] = useState<{ left: number; top: number; arrow: number; above: boolean } | null>(null);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let current: HTMLElement | null = null;
    const hide = () => { clearTimeout(timer); current = null; setShown(null); };
    const over = (e: PointerEvent) => {
      const el = (e.target as Element | null)?.closest?.("[data-tip]") as HTMLElement | null;
      if (el === current) return;
      clearTimeout(timer);
      current = el;
      setShown(null);
      if (!el) return;
      timer = setTimeout(() => {
        if (!el.isConnected) return;
        setShown({ title: el.dataset.tipTitle ?? "", desc: el.dataset.tip ?? "", keys: el.dataset.tipKey, rect: el.getBoundingClientRect() });
      }, 350);
    };
    document.addEventListener("pointerover", over);
    document.addEventListener("pointerdown", hide, true);
    document.addEventListener("keydown", hide, true);
    document.addEventListener("scroll", hide, true);
    window.addEventListener("blur", hide);
    return () => {
      clearTimeout(timer);
      document.removeEventListener("pointerover", over);
      document.removeEventListener("pointerdown", hide, true);
      document.removeEventListener("keydown", hide, true);
      document.removeEventListener("scroll", hide, true);
      window.removeEventListener("blur", hide);
    };
  }, []);

  // 말풍선 크기를 잰 뒤 위치를 정한다: 기본은 아래, 화면 아래가 모자라면 위. 좌우는 화면 안으로.
  useLayoutEffect(() => {
    if (!shown || !box.current) { setPos(null); return; }
    const { width, height } = box.current.getBoundingClientRect();
    const r = shown.rect;
    const gap = 8, margin = 8;
    const above = r.bottom + gap + height > window.innerHeight - margin && r.top - gap - height > margin;
    const center = r.left + r.width / 2;
    const left = Math.min(Math.max(center - width / 2, margin), window.innerWidth - width - margin);
    const top = above ? r.top - gap - height : r.bottom + gap;
    setPos({ left, top, arrow: Math.min(Math.max(center - left, 12), width - 12), above });
  }, [shown]);

  if (!shown) return null;
  return (
    <div ref={box} role="tooltip" className={`tip${pos ? " on" : ""}${pos?.above ? " above" : ""}`}
      style={{ left: pos?.left ?? -9999, top: pos?.top ?? -9999, ["--arrow" as string]: `${pos?.arrow ?? 0}px` }}>
      {(shown.title || shown.keys) && (
        <div className="tip-head">
          {shown.title && <b>{shown.title}</b>}
          {shown.keys && <span className="tip-keys">{shown.keys.split(" ").map((k) => <kbd key={k}>{k}</kbd>)}</span>}
        </div>
      )}
      <p>{shown.desc}</p>
    </div>
  );
}
