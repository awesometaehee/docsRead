"""파일명에서 회사·서식번호·상품명·적용기간·개정일을 뽑는다.

예) 101_43-1_약관_개발(보람동행580)_2026.04.01~현재)_2026.09.22.pdf
파일명 규칙이 일정하지 않으므로 실패한 항목은 None 으로 두고 원본 파일명을 함께 보존한다.
"""
from __future__ import annotations

import re
import unicodedata

COMPANIES = {"101": "개발", "130": "라이프", "177": "애니콜", "180": "리더스", "181": "실로암", "182": "플러스"}
_DATE = r"\d{2,4}(?:[.\-]\d{1,2}){0,2}\.?"


def _norm_date(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip(". ")
    if re.fullmatch(r"\d{8}", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    if re.fullmatch(r"\d{6}", s):
        return f"20{s[:2]}-{s[2:4]}-{s[4:]}"
    parts = re.split(r"[.\-]", s)
    if len(parts[0]) == 2:
        parts[0] = "20" + parts[0]
    return "-".join(p.zfill(2) for p in parts)


def parse_filename(name: str) -> dict:
    stem = unicodedata.normalize("NFC", name)
    stem = re.sub(r"\.pdf$", "", stem, flags=re.I)
    out = {"company_code": None, "company": None, "form_no": None, "product": None,
           "valid_from": None, "valid_to": None, "revised_at": None}
    m = re.match(r"(\d{3})_([\d~\-_]+?)_(?=\D)", stem)
    if m:
        out["company_code"] = m.group(1)
        out["company"] = COMPANIES.get(m.group(1))
        out["form_no"] = m.group(2)
    rest = stem[m.end():] if m else stem
    rest = re.sub(r"^(약관|상품설명서)_?", "", rest)
    # 개정일: 맨 끝의 날짜 토큰
    m = re.search(rf"[_)\s]({_DATE}|\d{{6,8}})$", rest)
    if m:
        out["revised_at"] = _norm_date(m.group(1))
        rest = rest[:m.start() + 1]
    # 적용기간: 2023.01.01~현재 / 2025.08~ / 20210801~20210818
    m = re.search(rf"({_DATE}|\d{{8}})\s*~\s*(현재|{_DATE}|\d{{8}})?", rest)
    if m:
        out["valid_from"] = _norm_date(m.group(1))
        out["valid_to"] = None if m.group(2) in (None, "현재") else _norm_date(m.group(2))
        rest = rest[:m.start()]
    rest = re.sub(r"^[\s_(]*(개발|라이프|애니콜|리더스|실로암|플러스)[\s_(]*", "", rest.strip())
    out["product"] = rest.strip(" _()") or None
    return out
