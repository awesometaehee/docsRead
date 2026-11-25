"""Claude(Anthropic) 공급자: 쪽 이미지 → PageOut(JSON).

- 공식 Anthropic Python SDK. 키는 .env 또는 환경변수 ANTHROPIC_API_KEY(코드·DB 에 저장하지 않음)
- 출력 규격 강제: output_config.format(json_schema) — PageOut 규격 그대로. 받은 뒤 pydantic 으로 다시 검사
- 스트리밍(get_final_message): 긴 응답도 연결 시간 제한에 걸리지 않게
- 비용: 시스템 규칙 + few-shot 예시는 매 쪽 같으므로 프롬프트 캐시(cache_control)로 다시 읽는 값을 줄인다
- 거절(refusal) 대비: 서버 측 대체 모델(fallbacks="default") — 지원하는 모델(FALLBACK_MODELS)일 때만 보낸다
- 기본 모델 Claude Haiku 4.5: 생각 깊이(effort)·대체 모델을 받지 않으므로 보내지 않는다(effort 는 DOCSREAD_LLM_EFFORT 로 켤 때만)
- temperature 는 보내지 않는다(현재 API 판에서 거부)
"""
from __future__ import annotations

import base64
import json
import os

from .schema import PageOut

DEFAULT_MODEL = "claude-haiku-4-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# 서버 측 대체 모델(fallbacks="default")을 받는 모델. 그 밖(Haiku 4.5 등)에는 보내지 않는다
FALLBACK_MODELS = ("claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-2", "claude-fable-5-1")

_client = None


def key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY")


def _anthropic():
    global _client
    if _client is None:
        if not key():
            raise RuntimeError("ANTHROPIC_API_KEY 가 없습니다(.env 또는 환경변수)")
        import anthropic
        _client = anthropic.Anthropic(timeout=600, max_retries=2)
    return _client


def _image(png: bytes) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": base64.standard_b64encode(png).decode("ascii")}}


def call(png: bytes, doc_type: str, *, model: str, system: str, fewshot: list, max_tokens: int,
         effort: str | None, retry: tuple[dict, list[str]] | None, retry_text) -> tuple[dict, dict]:
    import anthropic

    messages: list[dict] = []
    for k, (ex_png, ex_json) in enumerate(fewshot):
        messages.append({"role": "user", "content": [_image(ex_png.read_bytes())]})
        block = {"type": "text", "text": ex_json}
        if k == len(fewshot) - 1:
            block["cache_control"] = {"type": "ephemeral"}     # 예시까지가 매 쪽 같은 앞부분 → 캐시
        messages.append({"role": "assistant", "content": [block]})
    messages.append({"role": "user", "content": [_image(png)]})
    if retry:
        messages.append({"role": "assistant", "content": json.dumps(retry[0], ensure_ascii=False)})
        messages.append({"role": "user", "content": retry_text(retry[1])})

    output_config: dict = {"format": {"type": "json_schema", "schema": PageOut.model_json_schema()}}
    if effort:
        output_config["effort"] = effort
    extra = {"betas": [FALLBACK_BETA], "fallbacks": "default"} if model in FALLBACK_MODELS else {}
    try:
        with _anthropic().beta.messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=messages,
            output_config=output_config,
            **extra,
        ) as stream:
            msg = stream.get_final_message()
    except anthropic.AuthenticationError:
        raise RuntimeError("Claude 키가 맞지 않습니다(ANTHROPIC_API_KEY)") from None
    except anthropic.NotFoundError:
        raise RuntimeError(f"Claude 모델을 찾을 수 없습니다: {model}") from None
    except anthropic.RateLimitError:
        raise RuntimeError("Claude 호출 한도 초과(잠시 뒤 다시)") from None
    except anthropic.BadRequestError as e:
        raise RuntimeError(f"Claude 요청 오류: {e.message[:200]}") from None
    except anthropic.APIStatusError as e:
        raise RuntimeError(f"Claude 서버 오류({e.status_code})") from None
    except anthropic.APIConnectionError:
        raise RuntimeError("Claude 연결 실패(네트워크)") from None

    if msg.stop_reason == "refusal":
        cat = getattr(getattr(msg, "stop_details", None), "category", None)
        raise RuntimeError(f"Claude 가 응답을 거절했습니다(분류: {cat or '알 수 없음'})")
    if msg.stop_reason == "max_tokens":
        raise RuntimeError("응답이 길이 제한에서 잘렸습니다(DOCSREAD_LLM_MAX_TOKENS 를 늘리세요)")
    text = next((b.text for b in msg.content if b.type == "text"), "")
    if not text:
        raise RuntimeError("Claude 응답이 비었습니다")
    try:
        page = PageOut.model_validate_json(text)
    except Exception as e:
        raise RuntimeError(f"규격에 맞지 않는 응답: {str(e)[:300]}") from e
    u = msg.usage
    cached = getattr(u, "cache_read_input_tokens", 0) or 0
    written = getattr(u, "cache_creation_input_tokens", 0) or 0
    usage = {"input": (u.input_tokens or 0) + cached + written, "output": u.output_tokens or 0,
             "cached": cached, "cache_write": written, "served_by": msg.model}
    return page.model_dump(), usage
