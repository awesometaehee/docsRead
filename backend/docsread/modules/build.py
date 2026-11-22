"""구조 블록에서 공통 모듈을 찾아 변수화한다.  실행: uv run python -m docsread.modules.build

단계
1) 단위 추출: 약관의 조, 섹션, 유의사항의 항목 하나하나, 머리말 문단, 꼬리말
2) 문안 중복 제거: 공백을 뺀 글자가 같으면 같은 문안(form)
3) 모듈 묶기: 글자 2~4-gram TF-IDF 코사인 유사도로 평균연결 군집 → 표현이 조금 달라도 같은 모듈
4) 변형·변수: 모듈 안에서 문안을 공백을 뺀 글자 단위로 정렬(띄어쓰기 차이는 무시). 차이가 짧으면 같은 템플릿의 '변수',
   문장이 추가·삭제될 만큼 크면 별도 '변형'으로 둔다.
모두 로컬 계산이며 외부 통신·LLM 호출이 없다. 결과는 modules* 테이블만 다시 쓴다(블록은 건드리지 않음).
"""
from __future__ import annotations

import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import numpy as np
from sklearn.cluster import AgglomerativeClustering
from sklearn.feature_extraction.text import TfidfVectorizer
from sqlalchemy import delete, select

from ..db import Block, Document, Module, ModuleOccurrence, ModuleVariant, SessionLocal, init_db

SIM_THRESHOLD = 0.55     # 이 유사도 이상이면 같은 모듈
MAX_CHANGED = 0.25       # 기준 문안 대비 달라진 글자 비율이 이 이하이면 같은 템플릿(변수 처리)
MAX_SPAN = 40            # 한 군데에서 달라진 글자 수가 이를 넘으면 문장 수준 차이 → 별도 변형
TOKEN = re.compile(r"⟦[^⟧]*⟧|\d+(?:[.,]\d+)*|[A-Za-z]+|[가-힣]+|\S")


@dataclass
class Unit:
    doc_id: int
    doc_type: str
    seq: int
    kind: str
    label: str | None
    title: str
    text: str


@dataclass
class Form:
    """공백을 무시했을 때 같은 문안 하나. key 는 공백을 뺀 글자열, pos[k] 는 key 의 k번째 글자가 text 에서 있는 위치."""
    key: str
    text: str
    pos: list[int]
    units: list[Unit] = field(default_factory=list)


HEADING = re.compile(r"^\s*제\s*\d+\s*조(?:\s*의\s*\d+)?\s*[\(\[（【〔][^\)\]）】〕]{0,40}[\)\]）】〕]\s*")


def clean(text: str) -> str:
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def collect_units(session) -> list[Unit]:
    units: list[Unit] = []
    for doc in session.scalars(select(Document)):
        blocks = list(doc.blocks)
        kids: dict[int, list[Block]] = defaultdict(list)
        for b in blocks:
            if b.parent_seq is not None:
                kids[b.parent_seq].append(b)

        def body(b: Block) -> str:
            # 표는 상품별 데이터라 문안 비교에서 뺀다(표가 문단을 가르는 위치도 문서마다 달라 잡음이 된다)
            text = clean(" ".join(c.text for c in kids[b.seq] if c.kind == "paragraph" and c.text))
            return HEADING.sub("", text) if b.kind == "article" else text

        for b in blocks:
            if b.kind == "article":
                text = body(b)
                if text:
                    units.append(Unit(doc.id, doc.doc_type, b.seq, "article", b.label, b.title or b.label or "", text))
            elif b.kind in ("section", "title"):
                paras = [c for c in kids[b.seq] if c.kind == "paragraph" and c.text]
                if len(paras) > 5:      # 유의사항처럼 항목이 나열된 섹션은 항목 하나하나를 단위로
                    for c in paras:
                        units.append(Unit(doc.id, doc.doc_type, c.seq, "item", None, b.title or "", clean(c.text)))
                elif b.kind == "section" and body(b):
                    units.append(Unit(doc.id, doc.doc_type, b.seq, "section", None, b.title or "", body(b)))
                else:
                    for c in paras:
                        units.append(Unit(doc.id, doc.doc_type, c.seq, "paragraph", None, "", clean(c.text)))
            elif b.kind == "paragraph" and b.parent_seq is None and b.text:
                units.append(Unit(doc.id, doc.doc_type, b.seq, "paragraph", None, "", clean(b.text)))
            elif b.kind == "footer" and b.text:
                units.append(Unit(doc.id, doc.doc_type, b.seq, "footer", None, "꼬리말", clean(b.text)))
    return units


def make_forms(units: list[Unit]) -> list[Form]:
    forms: dict[str, Form] = {}
    for u in units:
        pos = [k for k, ch in enumerate(u.text) if not ch.isspace()]
        key = "".join(u.text[k] for k in pos)
        if not key:
            continue
        if key not in forms:
            forms[key] = Form(key, u.text, pos)
        forms[key].units.append(u)
    return sorted(forms.values(), key=lambda f: (-len(f.units), f.key))


def cluster(forms: list[Form]) -> list[list[Form]]:
    # 제목을 한 번 더 넣어 같은 제목의 조가 잘 모이게 한다(조 번호는 문서마다 달라 쓰지 않음)
    texts = [re.sub(r"\s+", "", f.units[0].title) * 2 + "|" + f.key for f in forms]
    vec = TfidfVectorizer(analyzer="char", ngram_range=(2, 4), sublinear_tf=True, min_df=1, dtype=np.float32)
    x = vec.fit_transform(texts)
    sim = (x @ x.T).toarray()
    np.clip(sim, 0, 1, out=sim)
    labels = AgglomerativeClustering(n_clusters=None, metric="precomputed", linkage="average",
                                     distance_threshold=1 - SIM_THRESHOLD).fit_predict(1 - sim)
    groups: dict[int, list[Form]] = defaultdict(list)
    for f, lab in zip(forms, labels):
        groups[int(lab)].append(f)
    return list(groups.values())


def _ops(base: str, other: str):
    return SequenceMatcher(None, base, other, autojunk=False).get_opcodes()


def _compatible(base: str, ops) -> bool:
    changed_a = sum(i2 - i1 for tag, i1, i2, j1, j2 in ops if tag != "equal")
    changed_b = sum(j2 - j1 for tag, i1, i2, j1, j2 in ops if tag != "equal")
    return (max(changed_a, changed_b) <= max(8, MAX_CHANGED * len(base))
            and all(max(i2 - i1, j2 - j1) <= MAX_SPAN for tag, i1, i2, j1, j2 in ops if tag != "equal"))


def _token_bounds(base: Form) -> tuple[list[int], list[int]]:
    """key 좌표계에서 각 글자가 속한 토큰(숫자열·한글열·영문열·기호)의 시작/끝."""
    n = len(base.key)
    start, end = list(range(n)), list(range(1, n + 1))
    inv = {p: k for k, p in enumerate(base.pos)}
    for m in TOKEN.finditer(base.text):
        ks = [inv[p] for p in range(m.start(), m.end()) if p in inv]
        for k in ks:
            start[k], end[k] = ks[0], ks[-1] + 1
    return start, end


def _merge_ranges(ranges: list[tuple[int, int]], base: Form) -> list[tuple[int, int]]:
    start, end = _token_bounds(base)
    n = len(base.key)
    snapped = []
    for s, e in ranges:   # 변수 경계를 단어(토큰) 경계로 넓힌다
        if s < n and (e > s or start[s] < s):
            s = start[s]
        if e > 0 and e > s:
            e = end[e - 1]
        snapped.append((s, e))
    out: list[list[int]] = []
    for s, e in sorted(set(snapped)):
        # 닿아 있거나 한두 글자(년·월·, 등)만 사이에 둔 차이는 한 변수로 합친다
        if out and s - out[-1][1] <= 2:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def _map(ops, x: int, start: bool) -> int:
    cands = []
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            if i1 <= x <= i2:
                cands.append(j1 + (x - i1))
        else:
            if i1 <= x <= i2:      # 넓힌 경계가 차이 구간 안/가장자리에 놓인 경우
                cands.append(j1 if start else j2)
    return (min if start else max)(cands) if cands else 0


def _slice(f: Form, ks: int, ke: int) -> str:
    """key 좌표 [ks,ke) 에 해당하는 원문 조각(띄어쓰기 보존)."""
    if ke <= ks:
        return ""
    return f.text[f.pos[ks]:f.pos[ke - 1] + 1].strip()


DATE = re.compile(r"^\d{2,4}\s*[년.\-/]\s*\d{1,2}\s*월?(\s*[.\-/]?\s*\d{1,2}\s*일?)?\.?\s*(부터|까지)?$")
AMOUNT = re.compile(r"^[\d,]+\s*(원|만원|억원)$")
COMPANY = re.compile(r"^보람상조\s*(개발|라이프|애니콜|리더스|실로암|플러스)?\s*(\(주\)|㈜|주식회사)?$")


def slot_type(values: list[str]) -> str:
    vals = [v for v in values if v]
    if not vals:
        return "문구"
    if all(DATE.match(v) for v in vals):
        return "날짜"
    if all(AMOUNT.match(v) for v in vals):
        return "금액"
    if all(COMPANY.match(v.replace(" ", "")) for v in vals):
        return "회사명"
    if all(re.fullmatch(r"제?\s*\d+\s*조?", v) for v in vals) and any("조" in v or "제" in v for v in vals):
        return "조번호"
    if all(re.fullmatch(r"[\d,.\s%~\-]+", v) for v in vals):
        return "숫자"
    if all(re.fullmatch(r"[\d\-\s]{7,}", v) for v in vals):
        return "전화번호"
    return "문구"


def build_variants(forms: list[Form]) -> list[dict]:
    """모듈 안의 문안들을 템플릿(변형)으로 묶고 변수를 뽑는다."""
    groups: list[tuple[Form, list[tuple[Form, list]]]] = []   # (기준 문안, [(문안, opcodes)])
    for f in forms:   # 많이 쓰인 문안부터 → 가장 흔한 문안이 기준이 된다
        placed = False
        for base, members in groups:
            if abs(len(base.key) - len(f.key)) > max(8, MAX_CHANGED * len(base.key)):
                continue
            ops = _ops(base.key, f.key)
            if _compatible(base.key, ops):
                members.append((f, ops))
                placed = True
                break
        if not placed:
            groups.append((f, [(f, [("equal", 0, len(f.key), 0, len(f.key))])]))
    out = []
    for base, members in groups:
        ranges = _merge_ranges([(i1, i2) for _, ops in members for tag, i1, i2, _, _ in ops if tag != "equal"], base)
        occ_values: list[tuple[Unit, list[str]]] = []
        per_slot: list[Counter] = [Counter() for _ in ranges]
        for f, ops in members:
            vals = [_slice(f, _map(ops, s, True), _map(ops, e, False)) for s, e in ranges]
            for u in f.units:
                occ_values.append((u, vals))
                for k, v in enumerate(vals):
                    per_slot[k][v] += 1
        slots, type_count = [], Counter()
        for counter in per_slot:
            typ = slot_type(list(counter))
            type_count[typ] += 1
            slots.append({"name": f"{typ}{type_count[typ]}", "type": typ, "default": counter.most_common(1)[0][0],
                          "n_values": len(counter), "top": counter.most_common(8)})
        # parts: 기준 문안을 고정 문구와 변수 자리로 나눈다(고정 문구는 원문 띄어쓰기 그대로)
        parts, pos, n = [], 0, len(base.key)
        text_pos = lambda k: base.pos[k] if k < n else len(base.text)
        for k, (s, e) in enumerate(ranges):
            if s > pos:
                parts.append({"t": base.text[text_pos(pos):text_pos(s)]})
            parts.append({"v": k})
            if e < n and e > 0 and base.pos[e] - base.pos[e - 1] > 1 and e > s:
                parts.append({"t": " "})   # 변수 뒤의 띄어쓰기 보존
            pos = e
        if pos < n:
            parts.append({"t": base.text[text_pos(pos):]})
        text = "".join(p["t"] if "t" in p else slots[p["v"]]["default"] for p in parts)
        out.append({"parts": parts, "slots": slots, "text": text, "occ": occ_values})
    return out


def main() -> int:
    init_db()
    t0 = time.time()
    with SessionLocal() as session:
        units = collect_units(session)
        forms = make_forms(units)
        print(f"단위 {len(units)}개 → 서로 다른 문안 {len(forms)}개 ({time.time() - t0:.1f}s)", flush=True)
        clusters = cluster(forms)
        print(f"모듈 후보 {len(clusters)}개 ({time.time() - t0:.1f}s)", flush=True)
        session.execute(delete(ModuleOccurrence))
        session.execute(delete(ModuleVariant))
        session.execute(delete(Module))
        n_occ = 0
        for forms_in in clusters:
            forms_in.sort(key=lambda f: (-len(f.units), f.key))
            variants = build_variants(forms_in)
            all_units = [u for f in forms_in for u in f.units]
            kind = Counter(u.kind for u in all_units).most_common(1)[0][0]
            titles = Counter(u.title for u in all_units if u.title)
            title = titles.most_common(1)[0][0] if titles else ""
            if kind in ("item", "paragraph") or not title:
                head = re.sub(r"^[•※\-\d.\s①-⑳]+", "", variants[0]["text"])[:40]
                title = f"{title} › {head}" if title else head
            m = Module(kind=kind, title=title, n_docs=len({u.doc_id for u in all_units}),
                       n_occurrences=len(all_units), n_variants=len(variants),
                       n_slots=sum(len(v["slots"]) for v in variants),
                       doc_types=sorted({u.doc_type for u in all_units}))
            variants.sort(key=lambda v: -len(v["occ"]))
            for i, v in enumerate(variants):
                mv = ModuleVariant(ordinal=i, n_docs=len({u.doc_id for u, _ in v["occ"]}),
                                   n_occurrences=len(v["occ"]), text=v["text"], parts=v["parts"], slots=v["slots"])
                for u, vals in v["occ"]:
                    mv.occurrences.append(ModuleOccurrence(document_id=u.doc_id, block_seq=u.seq, label=u.label,
                                                           chars=len(u.text), values=vals))
                    n_occ += 1
                m.variants.append(mv)
            session.add(m)
        session.commit()
        # 대사: 단위 수 = 적재된 사용처 수
        print(f"대사: 단위 {len(units)} / 사용처 {n_occ} / 모듈 {len(clusters)} ({time.time() - t0:.1f}s)")
        return 0 if n_occ == len(units) else 1


if __name__ == "__main__":
    sys.exit(main())
