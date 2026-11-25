"""이 키로 쓸 수 있는 Gemini 모델 ID 목록(이미지 입력·구조화 출력에 쓰는 generateContent 지원 모델만).

실행: uv run python -m docsread.llm.models [찾을 글자]   예) uv run python -m docsread.llm.models flash
키는 .env 의 GEMINI_API_KEY 를 쓴다. 키 값은 출력하지 않는다.
"""
from __future__ import annotations

import sys

from . import extract


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    word = (args[0] if args else "").lower()
    if not extract._gemini_key():
        print("GEMINI_API_KEY 가 없습니다(.env)")
        return 1
    names = []
    for m in extract._gemini().models.list():
        actions = getattr(m, "supported_actions", None) or []
        name = (m.name or "").removeprefix("models/")
        if "generateContent" in actions and word in name.lower():
            names.append(name)
    for n in sorted(names):
        print(("* " if n == extract.MODEL else "  ") + n)
    print(f"\n지금 설정: {extract.MODEL}" + ("" if extract.MODEL in names else "  ← 목록에 없음. .env 의 DOCSREAD_LLM_MODEL 을 위 이름으로 바꾸세요"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
