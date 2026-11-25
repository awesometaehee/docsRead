"""쪽 이미지 → LLM → PageOut(JSON). 일관성 고정 장치:

1. 출력 규격 강제: Structured Outputs(strict JSON Schema, schema.py). 규격 밖 필드·값은 API 단계에서 거부된다
2. 허용 태그 화이트리스트: 모델은 HTML 을 만들지 않고 정해진 블록 종류만 고른다. HTML·스타일은 to_editor.py 가 만든다
3. few-shot: data/llm_fewshot/ 의 (쪽 이미지, 정답 JSON) 쌍을 앞선 대화로 넣는다
4. temperature=0, seed 고정(모델이 지원할 때. DOCSREAD_LLM_TEMPERATURE 를 비우면 보내지 않음)

공급자: DOCSREAD_LLM_PROVIDER = openai | gemini | claude (비우면 모델 이름으로: gemini… → gemini, claude… → claude).
외부 통신: 쪽 이미지가 고른 공급자의 API(OpenAI · Google Gemini · Anthropic Claude)로 전송된다.
키는 .env 또는 환경변수(OPENAI_API_KEY / GEMINI_API_KEY / ANTHROPIC_API_KEY)로만 받는다(코드·DB 에 저장하지 않음).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from collections import Counter
from pathlib import Path

import pymupdf

from ..config import DATA_DIR
from .schema import PageOut

_model_env = os.environ.get("DOCSREAD_LLM_MODEL", "").strip()
PROVIDER = (os.environ.get("DOCSREAD_LLM_PROVIDER", "").strip().lower()
            or ("gemini" if _model_env.startswith("gemini") else "claude" if _model_env.startswith("claude") else "openai"))
if PROVIDER == "anthropic":
    PROVIDER = "claude"
if PROVIDER not in ("openai", "gemini", "claude"):
    raise RuntimeError(f"DOCSREAD_LLM_PROVIDER 는 openai · gemini · claude 중 하나여야 합니다: {PROVIDER}")
MODEL = _model_env or {"openai": "gpt-4.1", "gemini": "gemini-2.5-pro", "claude": "claude-haiku-4-5"}[PROVIDER]
# Claude 생각 깊이(low · medium · high …). 비우면 보내지 않음 — 기본 모델 Haiku 4.5 는 이 값을 받지 않는다(400)
EFFORT = os.environ.get("DOCSREAD_LLM_EFFORT", "").strip()
# Gemini 생각 수준: 숫자면 thinking_budget(토큰), 글자면 thinking_level(low/high 등). 비우면 모델 기본값
THINKING = os.environ.get("DOCSREAD_LLM_THINKING", "").strip()
_temp = os.environ.get("DOCSREAD_LLM_TEMPERATURE", "0").strip()
TEMPERATURE = float(_temp) if _temp else None
TEMPERATURE_REJECTED = False      # 모델이 temperature 를 거부해 빼고 보낸 적이 있는가
# 추론 모델은 생각에 쓴 토큰도 이 한도에 들어간다. 잘리면 오류로 알리므로 그때 늘린다
MAX_TOKENS = int(os.environ.get("DOCSREAD_LLM_MAX_TOKENS", "32000"))
DPI = int(os.environ.get("DOCSREAD_LLM_DPI", "200"))     # 상품설명서 본문이 4.8pt 라 144dpi 로는 글자가 뭉개진다
FEWSHOT_DIR = DATA_DIR / "llm_fewshot"
MAX_FEWSHOT = 2

SYSTEM = """당신은 한국어 약관·상품설명서 PDF 한 쪽의 이미지를 읽어 정해진 JSON 규격으로 옮기는 변환기입니다.

규칙
- 글자는 이미지에 보이는 그대로 옮깁니다. 고치기·요약·번역·생략·추가를 하지 않습니다.
- 읽는 순서대로 블록을 나열합니다. 두 단 배치면 왼쪽 단 전체 → 오른쪽 단입니다.
- 서로 다른 내용이 좌우로 나란히 놓인 영역(예: 왼쪽 상품 본표 | 오른쪽 다른 상품 정보, 왼쪽 표 | 오른쪽 안내문)은
  side_start(열 너비 비율 %) → 왼쪽 열 블록들 → side_next → 오른쪽 열 블록들 → side_end 로 감쌉니다.
  한 항목의 글이 두 단으로 흐르는 것(유의사항 등)은 나란히 영역이 아니라 section 의 columns=2 입니다.
  사진 묶음이 글 옆에 놓인 것은 나란히 영역으로 감싸지 않아도 됩니다.
- 줄바꿈은 문단이 실제로 끝난 곳에서만 블록을 나눕니다. 화면 폭 때문에 넘어간 줄은 한 문단으로 잇습니다.
- 블록 종류는 규격의 것만 씁니다. 애매하면 paragraph(body)로 둡니다.
- 문서 제목은 첫 쪽 맨 위 하나만 title 입니다. 색 띠·둥근 머리 상자로 시작하는 큰 항목 제목은 section 입니다.
- section 아래 내용이 좌우 두 단으로 흘러가면(유의사항 등) columns=2 입니다.
- 조 제목(제N조 (제목))은 article_title, 그 아래 본문은 paragraph 입니다.
- 제목(title·section·article_title) 글자가 붉으면 color=red 입니다. 붉은 조 제목을 놓치지 마세요.
- ①②③, 1. 2., 가. 나. 로 시작하는 항목은 numbered, •·※·- 로 시작하면 bullet 입니다. 번호·기호도 글자에 그대로 둡니다.
- 붉은 글씨는 color=red, 회색 보조 글씨는 gray, 나머지는 default 입니다. 굵은 글씨만 bold=true 입니다.
- 글 영역 가운데에 놓인 줄(제목 바로 아래 안내 문구 등)은 align=center 입니다.
- 표는 행마다 새로 시작하는 칸만 적고, 합쳐진 칸은 colspan·rowspan 으로 나타냅니다.
  col_widths 에는 열마다 표 너비에 대한 비율(%)을 적습니다. 칸 배경색은 shade 로 고릅니다.
- 사진·그림은 image 로 자리만 표시합니다(그림 안의 글자는 옮기지 않습니다). 사진 아래 설명은 caption 입니다.
  사진마다 image 하나씩, 보이는 순서대로 씁니다. 표 칸 안에 놓인 사진은 image 블록이 아니라 그 칸의 image=true 입니다.
- 쪽 번호와 매 쪽 반복되는 머리말·꼬리말(회사 로고, 대표이사·주소 줄, 전화번호 띠)은 page_artifact 입니다. 로고를 image 로 쓰지 않습니다.

예시(일부)
{"starts_mid_paragraph": false, "blocks": [
 {"type": "title", "text": "상조 서비스 약관", "color": "default"},
 {"type": "paragraph", "style": "bullet", "align": "center",
  "segments": [{"text": "※ 붉은색으로 표기된 내용은 …", "bold": false, "color": "red"}]},
 {"type": "article_title", "text": "제1조 (목적)", "color": "default"},
 {"type": "paragraph", "style": "body", "align": "left", "segments": [{"text": "이 계약은 …", "bold": false, "color": "default"}]},
 {"type": "article_title", "text": "제6조 (중간지급 정산 서비스)", "color": "red"},
 {"type": "table", "col_widths": [60, 40], "rows": [
   {"cells": [{"segments": [{"text": "상품", "bold": true, "color": "default"}], "header": true, "shade": "medium", "image": false, "colspan": 1, "rowspan": 1, "align": "center"},
              {"segments": [{"text": "상품금액", "bold": true, "color": "default"}], "header": true, "shade": "medium", "image": false, "colspan": 1, "rowspan": 1, "align": "center"}]},
   {"cells": [{"segments": [{"text": "(NEW) VIP 240", "bold": false, "color": "default"}], "header": false, "shade": "none", "image": false, "colspan": 1, "rowspan": 1, "align": "left"},
              {"segments": [{"text": "2,400,000원", "bold": false, "color": "default"}], "header": false, "shade": "none", "image": false, "colspan": 1, "rowspan": 1, "align": "right"}]}]},
 {"type": "section", "text": "장례상품 유의사항", "color": "default", "columns": 2},
 {"type": "side_start", "widths": [40, 60]},
 {"type": "table", "col_widths": [50, 50], "rows": […]},
 {"type": "side_next"},
 {"type": "paragraph", "style": "bullet", "align": "left", "segments": [{"text": "※ 회사는 공인회계사 회계감사를 받았습니다.", "bold": false, "color": "default"}]},
 {"type": "side_end"},
 {"type": "page_artifact", "text": "가입 및 상담 1588-7979  장례접수 1577-1009"}
]}"""


def _fewshot(doc_type: str) -> list[tuple[Path, str]]:
    """data/llm_fewshot/<문서종류>_<이름>.png + .json 쌍 중 같은 종류(terms|brochure)만, 최대 MAX_FEWSHOT 개.
    정답 JSON 은 규격 검사를 통과해야 쓴다. 보낼 때는 공백 없는 JSON 으로 줄인다(토큰 절약)."""
    out = []
    if FEWSHOT_DIR.exists():
        for js in sorted(FEWSHOT_DIR.glob(f"{doc_type}_*.json"))[:MAX_FEWSHOT]:
            png = js.with_suffix(".png")
            if png.exists():
                page = PageOut.model_validate_json(js.read_text(encoding="utf-8"))   # 틀린 예시는 바로 알린다
                out.append((png, page.model_dump_json()))
    return out


def prompt_version(doc_type: str) -> str:
    """프롬프트·규격·few-shot·해상도가 같으면 같은 값. 캐시 열쇠로 쓴다."""
    h = hashlib.sha256()
    h.update(SYSTEM.encode())
    h.update(json.dumps(PageOut.model_json_schema(), sort_keys=True).encode())
    h.update(str(DPI).encode())
    for png, text in _fewshot(doc_type):
        h.update(hashlib.sha256(png.read_bytes()).digest())
        h.update(text.encode())
    return h.hexdigest()[:16]


def render_page(pdf_path: Path, page_no: int, dpi: int = DPI) -> bytes:
    with pymupdf.open(pdf_path) as pdf:
        return pdf[page_no].get_pixmap(dpi=dpi).tobytes("png")


def _image_part(png: bytes) -> dict:
    return {"type": "image_url",
            "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode(), "detail": "high"}}


def _gemini_key() -> str | None:
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")


def configured() -> bool:
    if PROVIDER == "claude":
        return bool(os.environ.get("ANTHROPIC_API_KEY"))
    return bool(_gemini_key()) if PROVIDER == "gemini" else bool(os.environ.get("OPENAI_API_KEY"))


_client = None


def _openai():
    global _client
    if _client is None:
        if not configured():
            raise RuntimeError("OPENAI_API_KEY 환경변수가 없습니다")
        from openai import OpenAI
        _client = OpenAI(timeout=180, max_retries=3)
    return _client


def retry_text(problems: list[str]) -> str:
    head = "방금 응답에 다음 문제가 있습니다. 같은 쪽 이미지를 다시 보고, 규칙과 규격에 맞게 쪽 전체를 처음부터 다시 출력하세요."
    return head + "".join(f"\n- {p}" for p in problems)


def call_llm(png: bytes, doc_type: str, retry: tuple[dict, list[str]] | None = None) -> tuple[dict, dict]:
    """쪽 이미지 하나 → (PageOut dict, 사용량). 거절·잘림·규격 불일치는 예외로 알린다.
    retry=(이전 응답, 문제 목록) 이면 그 응답과 문제를 대화에 붙여 다시 요청한다(validate.problems)."""
    if PROVIDER == "claude":
        from . import claude
        return claude.call(png, doc_type, model=MODEL, system=SYSTEM, fewshot=[] if retry else _fewshot(doc_type),
                           max_tokens=MAX_TOKENS, effort=EFFORT or None, retry=retry, retry_text=retry_text)
    return _call_gemini(png, doc_type, retry) if PROVIDER == "gemini" else _call_openai(png, doc_type, retry)


# ---- Gemini ----

def _gemini_schema() -> dict:
    """PageOut JSON Schema 를 Gemini 가 받는 모양으로: $ref 를 펼치고 const → enum, 설명 외 메타 필드는 뺀다.
    (additionalProperties 등 빠진 제약은 받은 뒤 pydantic 검사로 다시 확인한다)"""
    root = PageOut.model_json_schema()
    defs = root.pop("$defs", {})

    def walk(node):
        if isinstance(node, list):
            return [walk(x) for x in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return walk(defs[node["$ref"].split("/")[-1]])
        out = {}
        for k, v in node.items():
            if k in ("title", "default", "additionalProperties", "discriminator"):
                continue
            if k == "const":
                out["enum"] = [v]
                continue
            out[k] = walk(v)
        return out

    return walk(root)


_gclient = None


def _gemini():
    global _gclient
    if _gclient is None:
        if not _gemini_key():
            raise RuntimeError("GEMINI_API_KEY 가 없습니다(.env 또는 환경변수)")
        from google import genai
        from google.genai import types
        _gclient = genai.Client(api_key=_gemini_key(), http_options=types.HttpOptions(timeout=300_000))
    return _gclient


def _call_gemini(png: bytes, doc_type: str, retry=None) -> tuple[dict, dict]:
    import time

    from google.genai import errors, types

    def image(b: bytes):
        return types.Part.from_bytes(data=b, mime_type="image/png")

    contents = []
    for ex_png, ex_json in ([] if retry else _fewshot(doc_type)):     # 다시 요청은 예시 없이(이전 응답이 형식을 보여 줌)
        contents.append(types.Content(role="user", parts=[image(ex_png.read_bytes())]))
        contents.append(types.Content(role="model", parts=[types.Part.from_text(text=ex_json)]))
    contents.append(types.Content(role="user", parts=[image(png)]))
    if retry:
        contents.append(types.Content(role="model", parts=[types.Part.from_text(text=json.dumps(retry[0], ensure_ascii=False))]))
        contents.append(types.Content(role="user", parts=[types.Part.from_text(text=retry_text(retry[1]))]))
    cfg = {"system_instruction": SYSTEM, "response_mime_type": "application/json",
           "response_json_schema": _gemini_schema(), "max_output_tokens": MAX_TOKENS}
    if TEMPERATURE is not None:
        cfg.update(temperature=TEMPERATURE, seed=0)
    if THINKING:
        cfg["thinking_config"] = types.ThinkingConfig(
            **({"thinking_budget": int(THINKING)} if THINKING.lstrip("-").isdigit() else {"thinking_level": THINKING}))
    for attempt in range(3):      # 일시 오류(과부하·속도 제한)만 잠깐 쉬고 다시 보낸다
        try:
            res = _gemini().models.generate_content(model=MODEL, contents=contents,
                                                    config=types.GenerateContentConfig(**cfg))
            break
        except errors.APIError as e:
            if getattr(e, "code", None) in (429, 500, 503) and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            raise RuntimeError(f"Gemini 호출 실패({getattr(e, 'code', '?')}): {e}") from e
    fb = getattr(res, "prompt_feedback", None)
    if fb is not None and getattr(fb, "block_reason", None):
        raise RuntimeError(f"Gemini 가 입력을 차단했습니다: {fb.block_reason}")
    cand = (res.candidates or [None])[0]
    reason = str(getattr(cand, "finish_reason", "") or "")
    if "MAX_TOKENS" in reason:
        raise RuntimeError("응답이 길이 제한에서 잘렸습니다(DOCSREAD_LLM_MAX_TOKENS 를 늘리세요)")
    if cand is None or not res.text:
        raise RuntimeError(f"Gemini 응답이 비었습니다(종료 사유: {reason or '알 수 없음'})")
    try:
        page = PageOut.model_validate_json(res.text)
    except Exception as e:
        raise RuntimeError(f"규격에 맞지 않는 응답: {str(e)[:300]}") from e
    u = res.usage_metadata
    usage = {}
    if u:
        thoughts = u.thoughts_token_count or 0
        usage = {"input": u.prompt_token_count or 0, "output": (u.candidates_token_count or 0) + thoughts,
                 "thinking": thoughts, "cached": u.cached_content_token_count or 0}
    return page.model_dump(), usage


# ---- OpenAI ----

def _call_openai(png: bytes, doc_type: str, retry=None) -> tuple[dict, dict]:
    messages: list[dict] = [{"role": "system", "content": SYSTEM}]
    for ex_png, ex_json in ([] if retry else _fewshot(doc_type)):
        messages.append({"role": "user", "content": [_image_part(ex_png.read_bytes())]})
        messages.append({"role": "assistant", "content": ex_json})
    messages.append({"role": "user", "content": [_image_part(png)]})
    if retry:
        messages.append({"role": "assistant", "content": json.dumps(retry[0], ensure_ascii=False)})
        messages.append({"role": "user", "content": retry_text(retry[1])})
    global TEMPERATURE_REJECTED
    kw = {"temperature": TEMPERATURE, "seed": 0} if TEMPERATURE is not None and not TEMPERATURE_REJECTED else {}
    from openai import BadRequestError
    try:
        res = _openai().chat.completions.parse(model=MODEL, messages=messages, response_format=PageOut,
                                               max_completion_tokens=MAX_TOKENS, **kw)
    except BadRequestError as e:
        # 추론 모델 등 temperature 를 받지 않는 모델: 빼고 다시 보내되, 결과 보고에 반드시 드러낸다
        if not kw or "temperature" not in str(e):
            raise
        TEMPERATURE_REJECTED = True
        res = _openai().chat.completions.parse(model=MODEL, messages=messages, response_format=PageOut,
                                               max_completion_tokens=MAX_TOKENS)
    choice = res.choices[0]
    if choice.finish_reason == "length":
        raise RuntimeError("응답이 길이 제한에서 잘렸습니다")
    if choice.message.refusal:
        raise RuntimeError(f"모델이 응답을 거절했습니다: {choice.message.refusal}")
    if choice.message.parsed is None:
        raise RuntimeError("규격에 맞는 응답을 받지 못했습니다")
    u = res.usage
    usage = {"input": u.prompt_tokens, "output": u.completion_tokens} if u else {}
    return choice.message.parsed.model_dump(), usage


# ---- 글자 대조: LLM 이 옮긴 글자와 PDF 글자 레이어를 비교해 빠뜨림·지어냄을 드러낸다 ----

_WS = re.compile(r"\s+")


def output_text(page: dict) -> str:
    parts = []

    def segs(ss):
        parts.extend(s["text"] for s in ss)

    for b in page["blocks"]:
        t = b["type"]
        if t in ("title", "section", "article_title", "page_artifact", "image"):
            parts.append(b.get("text") or b.get("caption") or "")
        elif t == "paragraph":
            segs(b["segments"])
        elif t == "notice":
            for p in b["paragraphs"]:
                segs(p["segments"])
        elif t == "table":
            for r in b["rows"]:
                for c in r["cells"]:
                    segs(c["segments"])
    return "".join(parts)


def fidelity(pdf_text: str, llm_text: str) -> dict | None:
    """공백을 뺀 글자 빈도로 비교. recall: PDF 글자 중 옮겨진 비율, precision: 옮긴 글자 중 PDF 에 있는 비율.
    글자 레이어가 없는 쪽(스캔본)은 None."""
    a, b = Counter(_WS.sub("", pdf_text)), Counter(_WS.sub("", llm_text))
    na, nb = sum(a.values()), sum(b.values())
    if na < 20:
        return None
    hit = sum((a & b).values())
    return {"recall": round(hit / na, 4), "precision": round(hit / nb, 4) if nb else 0.0, "pdf_chars": na}
